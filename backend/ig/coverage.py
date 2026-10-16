"""Coverage confidence.

The question this module exists to answer: **is a country quiet, or is our
view of it quiet?**

These are completely different claims and an OSINT map that conflates them
is worse than no map, because it asserts the first while having measured
only the second. A country with no reachable feeds produces zero rows, and
zero rows renders identically to "nothing happened".

The confidence score is built from factors that are all independently
observable:

* **Distinct sources** -- how many independent outlets mentioned the country.
  Dominant factor: one wire story repeated by one outlet tells us almost
  nothing about whether we can see that country.
* **Absolute volume** -- with diminishing returns, since 400 items is not
  40x better than 10 for the purpose of knowing a country is monitored.
* **Source diversity across packs** -- a country covered by both its own
  regional pack and the global wire is genuinely better observed than one
  covered only by the wire.
* **Recency** -- stale coverage means our pipeline, not the country, went
  quiet.

Population is deliberately *not* a driver of confidence. It is reported for
context only. Small countries would otherwise be permanently labelled
low-data regardless of how well we actually see them, and the resulting
output would encode a bias rather than measure one.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from .geo import CONTINENTS, all_countries
from .geo import get as get_country
from .models import CoverageCell, utcnow

#: Weights sum to 1.0. Kept explicit so the score is auditable.
W_DISTINCT_SOURCES = 0.45
W_VOLUME = 0.30
W_PACK_DIVERSITY = 0.15
W_RECENCY = 0.10

#: Thresholds for the human-readable data state.
STATE_GOOD = 0.60
STATE_THIN = 0.30
STATE_LOW = 0.05


def _saturating(value: float, scale: float) -> float:
    """Map a count to 0..1 with diminishing returns.

    ``1 - exp(-v/scale)`` reaches ~0.63 at ``scale`` and ~0.95 at ``3*scale``.
    Using a linear ramp instead would make the score dominated by the single
    largest country in every dataset.
    """
    if value <= 0:
        return 0.0
    return 1.0 - math.exp(-value / scale)


def compute_coverage(
    *,
    news_items: Iterable[Any] = (),
    events: Iterable[Any] = (),
    now: datetime | None = None,
) -> list[CoverageCell]:
    """Build one CoverageCell per territory in the reference table.

    Every country in ``geo`` appears in the output, including those with
    zero records. That is the entire point -- an all-countries denominator
    is what makes the quiet ones visible as a measured state.
    """
    now = now or utcnow()

    news_by_country: dict[str, set[str]] = defaultdict(set)
    news_count: dict[str, int] = defaultdict(int)
    news_packs: dict[str, set[str]] = defaultdict(set)
    news_last: dict[str, datetime] = {}
    event_count: dict[str, int] = defaultdict(int)
    event_last: dict[str, datetime] = {}

    for item in news_items:
        ts = getattr(item, "published_at", None) or getattr(item, "collected_at", None)
        packs = {
            f.split(":", 1)[1]
            for f in (getattr(item, "quality_flags", None) or [])
            if isinstance(f, str) and f.startswith("pack:")
        }
        for iso in getattr(item, "countries", None) or []:
            news_count[iso] += 1
            news_by_country[iso].add(getattr(item, "source_name", "") or "unknown")
            news_packs[iso] |= packs
            if ts and (iso not in news_last or ts > news_last[iso]):
                news_last[iso] = ts

    for ev in events:
        iso = getattr(ev, "country_iso2", None)
        if not iso:
            continue
        event_count[iso] += 1
        ts = getattr(ev, "event_date", None)
        if ts and (iso not in event_last or ts > event_last[iso]):
            event_last[iso] = ts

    cells: list[CoverageCell] = []
    for country in all_countries():
        iso = country.iso2
        n_news = news_count.get(iso, 0)
        n_events = event_count.get(iso, 0)
        distinct_sources = len(news_by_country.get(iso, set()))
        packs = len(news_packs.get(iso, set()))
        total = n_news + n_events

        last_seen = max(
            (t for t in (news_last.get(iso), event_last.get(iso)) if t is not None),
            default=None,
        )

        if total == 0 and distinct_sources == 0:
            cells.append(
                CoverageCell(
                    country_iso2=iso,
                    country_name=country.name,
                    continent=country.continent,
                    continent_name=country.continent_name,
                    population_millions=country.population_millions,
                    coverage_confidence=0.0,
                    data_state="no_data",
                    explanation=(
                        "No records collected for this territory in the current window. "
                        "This is a statement about feed reach, not about events on the ground."
                    ),
                )
            )
            continue

        source_score = _saturating(distinct_sources, 4.0)
        volume_score = _saturating(total, 25.0)
        pack_score = _saturating(packs, 2.0)
        recency_score = _recency_score(last_seen, now)

        confidence = (
            W_DISTINCT_SOURCES * source_score
            + W_VOLUME * volume_score
            + W_PACK_DIVERSITY * pack_score
            + W_RECENCY * recency_score
        )

        state = (
            "good"
            if confidence >= STATE_GOOD
            else "thin"
            if confidence >= STATE_THIN
            else "low_data"
            if confidence >= STATE_LOW
            else "no_data"
        )
        cells.append(
            CoverageCell(
                country_iso2=iso,
                country_name=country.name,
                continent=country.continent,
                continent_name=country.continent_name,
                event_count=n_events,
                news_count=n_news,
                distinct_sources=distinct_sources,
                population_millions=country.population_millions,
                coverage_confidence=round(confidence, 4),
                data_state=state,
                explanation=_explain(state, distinct_sources, total, packs),
                last_seen_at=last_seen,
            )
        )
    return cells


def _recency_score(last_seen: datetime | None, now: datetime) -> float:
    if last_seen is None:
        return 0.0
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=now.tzinfo)
    age_hours = max(0.0, (now - last_seen).total_seconds() / 3600.0)
    if age_hours <= 24:
        return 1.0
    if age_hours >= 24 * 14:
        return 0.0
    return 1.0 - (age_hours - 24) / (24 * 14 - 24)


def _explain(state: str, sources: int, total: int, packs: int) -> str:
    if state == "good":
        return (
            f"Observed through {sources} independent source(s) across {packs} feed "
            f"pack(s), {total} records. Comparatively well covered."
        )
    if state == "thin":
        return (
            f"{total} records from {sources} source(s). Coverage is present but "
            f"thin -- treat absence of signals here as weak evidence."
        )
    if state == "low_data":
        return (
            f"Only {total} record(s) from {sources} source(s). Read this territory's "
            f"quiet as limited visibility, not as confirmed calm."
        )
    return (
        "No records in the current window. This territory is effectively "
        "unobserved by the current feed set."
    )


def continent_summary(cells: list[CoverageCell]) -> list[dict[str, Any]]:
    """Aggregate coverage per continent, including all seven."""
    grouped: dict[str, list[CoverageCell]] = defaultdict(list)
    for cell in cells:
        grouped[cell.continent].append(cell)

    out = []
    for code, name in CONTINENTS.items():
        members = grouped.get(code, [])
        if not members:
            continue
        countries = len(members)
        observed = sum(1 for c in members if c.coverage_confidence > STATE_LOW)
        good = sum(1 for c in members if c.data_state == "good")
        no_data = sum(1 for c in members if c.data_state == "no_data")
        mean_conf = sum(c.coverage_confidence for c in members) / countries
        out.append(
            {
                "continent": code,
                "continent_name": name,
                "countries": countries,
                "countries_observed": observed,
                "countries_good": good,
                "countries_no_data": no_data,
                "total_events": sum(c.event_count for c in members),
                "total_news": sum(c.news_count for c in members),
                "mean_coverage_confidence": round(mean_conf, 4),
                "observation_rate": round(observed / countries, 4) if countries else 0.0,
                "interpretation": _continent_reading(mean_conf, no_data, countries),
            }
        )
    out.sort(key=lambda r: r["continent_name"])
    return out


def _continent_reading(mean_conf: float, no_data: int, countries: int) -> str:
    if countries == 0:
        return "no territories in reference table"
    if no_data > countries * 0.5:
        return (
            "Over half of territories have no records -- treat continent-level "
            "totals as a floor, not a measurement."
        )
    if mean_conf >= STATE_GOOD:
        return "Broadly well observed across most territories."
    if mean_conf >= STATE_THIN:
        return "Partially observed; some territories are effectively dark."
    return "Poorly observed overall; continent totals understate reality."


def coverage_index(cells: list[CoverageCell]) -> dict[str, float]:
    """ISO2 -> confidence, for coverage-weighted backtest scoring."""
    return {c.country_iso2: c.coverage_confidence for c in cells}


def focus_regions(cells: list[CoverageCell], limit: int = 12) -> list[dict[str, Any]]:
    """The territories most worth improving.

    Ranks by absolute population left unobserved, because that is the
    measure of how much of the world the current feed set is structurally
    blind to -- not by confidence alone, which would flag only tiny states.
    """
    blind = [
        c
        for c in cells
        if c.coverage_confidence < STATE_THIN and c.population_millions > 0
    ]
    blind.sort(key=lambda c: -c.population_millions)
    return [
        {
            "country_iso2": c.country_iso2,
            "country_name": c.country_name,
            "continent_name": c.continent_name,
            "population_millions": c.population_millions,
            "coverage_confidence": c.coverage_confidence,
            "data_state": c.data_state,
        }
        for c in blind[:limit]
    ]


def africa_check(cells: list[CoverageCell]) -> dict[str, Any]:
    """Explicit health check on the Africa pack.

    Called out separately because countering the Western-volume bias is a
    stated requirement, and a requirement nobody measures is a requirement
    nobody meets.
    """
    from .geo import african_iso_codes

    codes = african_iso_codes()
    members = [c for c in cells if c.country_iso2 in codes]
    if not members:
        return {"countries": 0, "note": "no African territories in reference table"}
    observed = [c for c in members if c.coverage_confidence > STATE_LOW]
    return {
        "countries_in_reference": len(members),
        "countries_with_records": len(observed),
        "countries_no_data": sum(1 for c in members if c.data_state == "no_data"),
        "mean_coverage_confidence": round(
            sum(c.coverage_confidence for c in members) / len(members), 4
        ),
        "total_news": sum(c.news_count for c in members),
        "total_events": sum(c.event_count for c in members),
        "note": (
            "All 54 African states are present in the reference table even where "
            "no records were collected, so absence is measurable rather than invisible."
        ),
    }


def country_detail(iso2: str, cells: list[CoverageCell]) -> dict[str, Any] | None:
    """Single-country drill-down payload."""
    cell = next((c for c in cells if c.country_iso2 == iso2.upper()), None)
    if cell is None:
        return None
    country = get_country(iso2)
    return {
        **cell.model_dump(mode="json"),
        "centroid": {"lat": country.lat, "lon": country.lon} if country else None,
        "region": country.region if country else None,
        "coverage_components": {
            "distinct_sources": cell.distinct_sources,
            "volume": cell.news_count + cell.event_count,
            "weights": {
                "distinct_sources": W_DISTINCT_SOURCES,
                "volume": W_VOLUME,
                "pack_diversity": W_PACK_DIVERSITY,
                "recency": W_RECENCY,
            },
        },
    }
