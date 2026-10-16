"""Source protocol, registry and health tracking.

Every upstream feed is wrapped in a class implementing ``Source``. The
registry owns the lifecycle and, crucially, the *health record*: when a
source fails, that failure is data. A map that quietly shows nothing because
three feeds are down is worse than useless -- it is actively misleading.

The rule enforced here: a source that fails returns an empty list AND
records a health entry explaining why. It never raises into the caller, so
one dead feed cannot take down the whole collection run.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from ..config import get_settings
from ..http import HttpClient
from ..models import SourceHealth, SourceStatus, utcnow
from ..store import BronzeRecord, MedallionStore


class Source(ABC):
    """Base class for every upstream feed.

    Subclasses implement ``collect`` and return whatever record type they
    produce. ``run`` wraps that with timing, bronze archival and health
    bookkeeping so subclasses never re-implement it.
    """

    #: Short stable identifier, used as the health key and bronze directory.
    name: str = "unnamed"
    #: Human label shown in the UI.
    label: str = "Unnamed source"
    #: Which module consumes it, for the health panel grouping.
    module: str = ""
    #: Freshness expectation -- older than this means the feed is stale.
    max_staleness_seconds: int = 6 * 3600
    #: Free-text note about provenance/licensing shown to the user.
    note: str = ""

    def __init__(self, client: HttpClient, store: MedallionStore) -> None:
        self.client = client
        self.store = store
        self.settings = get_settings()
        self.health = SourceHealth(name=self.name, status=SourceStatus.OK)

    @abstractmethod
    async def collect(self) -> list[Any]:
        """Fetch and parse records. May return an empty list."""

    async def run(self) -> list[Any]:
        """Execute with full operational bookkeeping.

        Never propagates an exception: a failing source degrades the product
        but must not break the run.
        """
        self.health.last_attempt_at = utcnow()
        started = time.monotonic()
        try:
            records = await self.collect()
        except Exception as exc:
            self.health.consecutive_failures += 1
            self.health.status = SourceStatus.FAILED
            self.health.error = f"{type(exc).__name__}: {exc}"
            self.health.records_last_run = 0
            self.health.latency_ms = int((time.monotonic() - started) * 1000)
            return []

        latency = int((time.monotonic() - started) * 1000)
        self.health.latency_ms = latency
        self.health.last_success_at = utcnow()
        self.health.consecutive_failures = 0
        self.health.records_last_run = len(records)

        if records:
            self.health.status = SourceStatus.OK
            self.health.error = ""
        else:
            # Zero records is a distinct condition from an error: the source
            # answered but had nothing to give. Surfaced, not swallowed.
            self.health.status = SourceStatus.DEGRADED
            self.health.error = self.health.error or "returned zero records"
        self._refresh_staleness()
        return records

    def _refresh_staleness(self) -> None:
        from ..quality import staleness_seconds

        age = staleness_seconds(self.health.last_success_at)
        self.health.staleness_seconds = age
        if age is not None and age > self.max_staleness_seconds:
            self.health.status = SourceStatus.STALE

    def mark_rate_limited(self, detail: str = "") -> None:
        """Record an upstream refusal to serve us.

        Kept distinct from FAILED because the fix is different: a rate limit
        is a configuration problem (slow down, cache, or use the bulk path),
        not an outage.
        """
        self.health.status = SourceStatus.RATE_LIMITED
        self.health.error = detail or "upstream rate limit reached"

    def archive(self, url: str, payload: bytes, content_type: str = "", note: str = "") -> None:
        """Append a raw payload to the immutable bronze layer."""
        import hashlib

        rid = hashlib.sha1(  # noqa: S324 - integrity tag, not a security control
            f"{self.name}|{url}|{len(payload)}".encode()
        ).hexdigest()[:16]
        self.store.bronze.append(
            BronzeRecord(
                id=rid,
                source=self.name,
                fetched_at=utcnow(),
                url=url,
                payload=payload,
                content_type=content_type,
                note=note,
            )
        )

    def health_payload(self) -> dict[str, Any]:
        self._refresh_staleness()
        return {
            "name": self.name,
            "label": self.label,
            "module": self.module,
            "note": self.note,
            "status": self.health.status.value,
            "last_success_at": self.health.last_success_at.isoformat()
            if self.health.last_success_at
            else None,
            "last_attempt_at": self.health.last_attempt_at.isoformat()
            if self.health.last_attempt_at
            else None,
            "records_last_run": self.health.records_last_run,
            "consecutive_failures": self.health.consecutive_failures,
            "latency_ms": self.health.latency_ms,
            "staleness_seconds": self.health.staleness_seconds,
            "max_staleness_seconds": self.max_staleness_seconds,
            "error": self.health.error,
            "is_usable": self.health.is_usable,
        }


class SourceRegistry:
    """Owns all sources and runs them, tolerating individual failures."""

    def __init__(self, client: HttpClient, store: MedallionStore) -> None:
        self.client = client
        self.store = store
        self._sources: dict[str, Source] = {}

    def register(self, source: Source) -> None:
        self._sources[source.name] = source

    def get(self, name: str) -> Source | None:
        return self._sources.get(name)

    def all(self) -> list[Source]:
        return list(self._sources.values())

    def enabled(self) -> list[Source]:
        return [s for s in self._sources.values() if self.settings.source_enabled(s.name)]

    async def run_all(self, only: list[str] | None = None) -> dict[str, list[Any]]:
        """Run sources concurrently and collect results keyed by source name.

        Concurrency is bounded by each source's own host gate, so running
        them in parallel does not violate upstream politeness limits.
        """
        import asyncio

        targets = [
            s
            for s in self.enabled()
            if only is None or s.name in only
        ]
        if not targets:
            return {}
        results = await asyncio.gather(
            *(s.run() for s in targets), return_exceptions=True
        )
        out: dict[str, list[Any]] = {}
        for source, result in zip(targets, results, strict=False):
            if isinstance(result, BaseException):
                source.health.status = SourceStatus.FAILED
                source.health.error = f"{type(result).__name__}: {result}"
                out[source.name] = []
            else:
                out[source.name] = result
        return out

    def health_report(self) -> dict[str, Any]:
        rows = [s.health_payload() for s in self.all()]
        usable = sum(1 for r in rows if r["is_usable"])
        by_status: dict[str, int] = {}
        for r in rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        return {
            "sources": sorted(rows, key=lambda r: r["name"]),
            "total": len(rows),
            "usable": usable,
            "by_status": by_status,
            "degraded": usable < len(rows),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def stale_sources(self) -> list[str]:
        return [s.name for s in self.all() if not s.health.is_usable]
