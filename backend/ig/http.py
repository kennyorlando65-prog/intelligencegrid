"""HTTP client with per-host rate limiting, retries and honest error reporting.

Every upstream source goes through this. It exists because the free data
sources have very different tolerances:

* GDELT's live API refuses more than one request every 5 seconds with a
  plain-text 429 (not JSON), and it will keep refusing if you retry hot.
* NVD rate-limits by IP without credentials and answers 403 when unhappy.
* OpenSky throttles anonymous clients by request weight, not request count.
* CISA/OFAC/EU/UN are static files behind CDNs -- fast, but large.

The client therefore rate-limits *before* sending, treats 429/503 as
"back off and do not count as a hard failure", and records enough
diagnostics that the health endpoint can explain itself.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import get_settings


class RateLimited(Exception):
    """Upstream asked us to slow down and we could not wait it out."""


class SourceUnavailable(Exception):
    """Upstream is reachable but returned a hard error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class HostGate:
    """Token-bucket-ish gate: enforces a minimum gap between requests."""

    rpm: int
    _last: float = field(default=0.0, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def wait(self) -> None:
        if self.rpm <= 0:
            return
        min_gap = 60.0 / self.rpm
        async with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < min_gap:
                await asyncio.sleep(min_gap - delta)
            self._last = time.monotonic()


@dataclass
class FetchResult:
    """What came back, plus how it went -- the second part drives health."""

    url: str
    status: int
    content: bytes = b""
    elapsed_ms: int = 0
    from_cache: bool = False
    error: str = ""
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.error

    def text(self, encoding: str = "utf-8") -> str:
        return self.content.decode(encoding, errors="replace")


class HttpClient:
    """Shared async HTTP client. One instance per process."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: httpx.AsyncClient | None = None
        self._gates: dict[str, HostGate] = {}
        self._cache: dict[str, tuple[float, bytes]] = {}

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> HttpClient:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.http_timeout_seconds,
                follow_redirects=True,
                headers={
                    "User-Agent": self._settings.user_agent,
                    "Accept-Encoding": "gzip, deflate",
                },
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def gate(self, host: str, rpm: int) -> HostGate:
        g = self._gates.get(host)
        if g is None or g.rpm != rpm:
            g = HostGate(rpm=rpm)
            self._gates[host] = g
        return g

    # -- core --------------------------------------------------------------

    async def fetch(
        self,
        url: str,
        *,
        rpm: int = 60,
        host_key: str | None = None,
        cache_ttl: int = 0,
        accept: str | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> FetchResult:
        """Fetch ``url`` subject to the host gate, with bounded retries.

        Retries only on transient conditions (timeouts, 429, 5xx). A 404 or
        403 fails immediately, because retrying a hard refusal wastes the
        upstream's patience and ours.
        """
        await self.start()
        assert self._client is not None

        key = url if not auth else f"{auth[0]}@{url}"
        if cache_ttl > 0:
            hit = self._cache.get(key)
            if hit and (time.monotonic() - hit[0]) < cache_ttl:
                return FetchResult(url=url, status=200, content=hit[1], from_cache=True)

        gate = self.gate(host_key or _host_of(url), rpm)
        started = time.monotonic()
        last_error = ""
        status = 0

        for attempt in range(1, self._settings.http_max_retries + 1):
            await gate.wait()
            try:
                req_headers = dict(headers or {})
                if accept:
                    req_headers["Accept"] = accept
                resp = await self._client.get(url, headers=req_headers, auth=auth)
                status = resp.status_code
                if status == 200:
                    elapsed = int((time.monotonic() - started) * 1000)
                    if cache_ttl > 0:
                        self._cache[key] = (time.monotonic(), resp.content)
                    return FetchResult(
                        url=url,
                        status=200,
                        content=resp.content,
                        elapsed_ms=elapsed,
                        attempts=attempt,
                    )
                if status in (429, 503):
                    last_error = f"rate limited (HTTP {status})"
                    # Exponential backoff with jitter; never hammer a limiter.
                    await asyncio.sleep(min(30.0, (2**attempt) + random.random()))
                    continue
                if 500 <= status < 600:
                    last_error = f"upstream error (HTTP {status})"
                    await asyncio.sleep(min(10.0, 2**attempt))
                    continue
                elapsed = int((time.monotonic() - started) * 1000)
                return FetchResult(
                    url=url,
                    status=status,
                    elapsed_ms=elapsed,
                    error=f"HTTP {status}",
                    attempts=attempt,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(min(10.0, 2**attempt))
            except Exception as exc:  # pragma: no cover - defensive
                last_error = f"{type(exc).__name__}: {exc}"
                break

        elapsed = int((time.monotonic() - started) * 1000)
        return FetchResult(
            url=url,
            status=status,
            elapsed_ms=elapsed,
            error=last_error or "request failed",
            attempts=self._settings.http_max_retries,
        )

    async def get_json(self, url: str, **kwargs: Any) -> tuple[Any, FetchResult]:
        import json

        res = await self.fetch(url, **kwargs)
        if not res.ok:
            return None, res
        try:
            return json.loads(res.text()), res
        except json.JSONDecodeError as exc:
            res.error = f"invalid JSON: {exc}"
            return None, res

    async def get_text(self, url: str, **kwargs: Any) -> tuple[str, FetchResult]:
        res = await self.fetch(url, **kwargs)
        if not res.ok:
            return "", res
        return res.text(), res


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc or url


# A module-level singleton keeps connection pooling and rate-limit state
# coherent across all sources within one serverless invocation.
_shared: HttpClient | None = None


async def get_client() -> HttpClient:
    global _shared
    if _shared is None:
        _shared = HttpClient()
        await _shared.start()
    return _shared
