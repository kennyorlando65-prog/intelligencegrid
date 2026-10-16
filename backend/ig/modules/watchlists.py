"""Watchlists and alert digests.

Watchlists are stored server-side in the process store. On a serverless
platform this means they are per-instance and do not survive a cold start --
a real limitation, stated plainly rather than papered over. The digest
generator is pure and stateless, so it can be driven by a scheduled job or
an external cron the moment durable storage is added.

A digest is built from signals whose countries intersect the watchlist. It
always carries the coverage caveat for each territory, because an alert
about a country nobody is observing is itself a claim that needs qualifying.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from ..models import Severity, Signal, utcnow

Frequency = Literal["daily", "weekly"]


@dataclass
class Watchlist:
    """A named set of monitored countries, optional filters and a cadence."""

    id: str
    name: str
    countries: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    min_severity: str = Severity.LOW.value
    frequency: Frequency = "daily"
    created_at: datetime = field(default_factory=utcnow)
    notes: str = ""
    #: Every watchlist is per-process and does not persist across a cold
    #: start on a serverless deployment. Surfaced in the API response.
    storage: str = "in-process (not durable across restarts)"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "countries": self.countries,
            "modules": self.modules,
            "min_severity": self.min_severity,
            "frequency": self.frequency,
            "created_at": self.created_at.isoformat(),
            "notes": self.notes,
            "storage": self.storage,
            "country_count": len(self.countries),
        }


class WatchlistStore:
    """In-memory watchlist registry."""

    def __init__(self) -> None:
        self._items: dict[str, Watchlist] = {}

    def create(
        self,
        name: str,
        countries: list[str],
        *,
        modules: list[str] | None = None,
        min_severity: str = Severity.LOW.value,
        frequency: Frequency = "daily",
        notes: str = "",
    ) -> Watchlist:
        wid = hashlib.sha1(  # noqa: S324 - identifier, not a security control
            f"{name}|{','.join(sorted(countries))}".encode()
        ).hexdigest()[:12]
        wl = Watchlist(
            id=wid,
            name=name.strip() or "Untitled watchlist",
            countries=[c.upper() for c in countries],
            modules=modules or [],
            min_severity=min_severity,
            frequency=frequency,
            notes=notes,
        )
        self._items[wid] = wl
        return wl

    def get(self, wid: str) -> Watchlist | None:
        return self._items.get(wid)

    def all(self) -> list[Watchlist]:
        return sorted(self._items.values(), key=lambda w: w.created_at, reverse=True)

    def delete(self, wid: str) -> bool:
        return self._items.pop(wid, None) is not None


def digest_for(
    watchlist: Watchlist,
    signals: list[Signal],
    *,
    coverage_by_country: dict[str, float] | None = None,
    news_lookup: dict[str, list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a digest of matching signals for one watchlist."""
    now = now or utcnow()
    coverage_by_country = coverage_by_country or {}
    news_lookup = news_lookup or {}

    window_days = 1 if watchlist.frequency == "daily" else 7
    since = now - timedelta(days=window_days)

    min_rank = _severity_rank(watchlist.min_severity)
    wanted = {c.upper() for c in watchlist.countries}

    matched: list[Signal] = []
    for sig in signals:
        if sig.detected_at < since:
            continue
        if sig.severity.rank < min_rank:
            continue
        if watchlist.modules and sig.module not in watchlist.modules:
            continue
        sig_countries = {c.upper() for c in sig.countries}
        if wanted and not (sig_countries & wanted):
            continue
        matched.append(sig)

    matched.sort(key=lambda s: (-s.severity.rank, -s.score))

    per_country: dict[str, list[dict[str, Any]]] = {}
    for sig in matched:
        for iso in sig.countries:
            per_country.setdefault(iso.upper(), []).append(
                {
                    "signal_id": sig.id,
                    "title": sig.title,
                    "severity": sig.severity.value,
                    "module": sig.module,
                    "score": sig.score,
                    "confidence": sig.confidence,
                }
            )

    coverage_notes = []
    for iso in sorted(wanted):
        conf = coverage_by_country.get(iso)
        if conf is None:
            coverage_notes.append(
                {
                    "country_iso2": iso,
                    "coverage_confidence": None,
                    "note": "not present in the coverage table for this window",
                }
            )
        elif conf < 0.3:
            coverage_notes.append(
                {
                    "country_iso2": iso,
                    "coverage_confidence": conf,
                    "note": (
                        "thin coverage -- absence of alerts for this territory is "
                        "weak evidence of calm"
                    ),
                }
            )

    quiet = [iso for iso in sorted(wanted) if iso not in per_country]

    return {
        "watchlist": watchlist.as_dict(),
        "generated_at": now.isoformat(),
        "window": {
            "frequency": watchlist.frequency,
            "days": window_days,
            "since": since.isoformat(),
        },
        "summary": {
            "signals_matched": len(matched),
            "countries_with_signals": len(per_country),
            "countries_quiet": len(quiet),
            "quiet_countries": quiet,
            "by_severity": _severity_histogram(matched),
        },
        "signals": [
            {
                "id": s.id,
                "module": s.module,
                "title": s.title,
                "description": s.description,
                "severity": s.severity.value,
                "signal_type": s.signal_type,
                "score": s.score,
                "confidence": s.confidence,
                "countries": s.countries,
                "detected_at": s.detected_at.isoformat(),
                "caveats": s.caveats,
                "evidence": [e.model_dump(mode="json") for e in s.evidence[:5]],
            }
            for s in matched[:60]
        ],
        "by_country": per_country,
        "coverage_notes": coverage_notes,
        "headlines": {
            iso: news_lookup.get(iso, [])[:5] for iso in sorted(per_country)
        },
        "caveats": [
            "Watchlists are stored in process memory and do not persist across a "
            "serverless cold start. Durable storage is a deployment decision.",
            "An empty digest for a territory may reflect thin feed coverage rather "
            "than an absence of events -- see coverage_notes.",
        ],
    }


def _severity_rank(value: str) -> int:
    try:
        return Severity(value).rank
    except ValueError:
        return Severity.LOW.rank


def _severity_histogram(signals: list[Signal]) -> dict[str, int]:
    out: dict[str, int] = {}
    for sig in signals:
        out[sig.severity.value] = out.get(sig.severity.value, 0) + 1
    return out


def digest_text(digest: dict[str, Any]) -> str:
    """Render a digest as plain text for email or chat delivery."""
    wl = digest["watchlist"]
    summary = digest["summary"]
    lines = [
        f"IntelligenceGrid digest -- {wl['name']}",
        f"Window: last {digest['window']['days']} day(s) "
        f"(generated {digest['generated_at']})",
        "",
        f"Signals: {summary['signals_matched']}  "
        f"Countries with activity: {summary['countries_with_signals']}  "
        f"Quiet: {summary['countries_quiet']}",
    ]
    if summary["by_severity"]:
        lines.append(
            "By severity: "
            + ", ".join(f"{k}={v}" for k, v in sorted(summary["by_severity"].items()))
        )
    lines.append("")
    for sig in digest["signals"][:20]:
        lines.append(f"[{sig['severity'].upper()}] {sig['title']}")
        lines.append(f"    {sig['description']}")
        if sig["countries"]:
            lines.append(f"    countries: {', '.join(sig['countries'])}")
        lines.append("")
    if digest["coverage_notes"]:
        lines.append("Coverage caveats:")
        for note in digest["coverage_notes"]:
            lines.append(f"  - {note['country_iso2']}: {note['note']}")
        lines.append("")
    lines.append("Confidence is an evidence-strength estimate, never a certainty.")
    return "\n".join(lines)
