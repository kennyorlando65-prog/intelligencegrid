"""The fourteen product modules.

Each module is a thin function over the pipeline's datasets. They are kept
as plain functions in a dispatch table rather than classes because the work
is genuinely a query plus a presentation choice -- wrapping that in a class
hierarchy adds indirection without adding clarity.

Every module returns a dict with a ``meta`` block carrying the operational
truth: when the data was collected, which sources were healthy, whether any
are degraded. A module that returns results without that context invites the
reader to treat a partial view as a complete one.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from ..coverage import (
    africa_check,
    continent_summary,
    coverage_index,
    focus_regions,
)
from ..engine import stats
from ..engine.nlp import (
    analyze_sentiment,
    extract_entities,
    extract_keywords,
    narrative_divergence,
    narrative_keywords,
)
from ..geo import CONTINENTS
from ..geo import get as get_country
from ..models import Severity, utcnow
from ..pipeline import (
    DATASET_NEWS,
    Pipeline,
)
from ..sources.cyber import cve_activity_by_day, kev_deadline_pressure
from ..sources.news import PACK_LABELS, search_news
from ..sources.sanctions import (
    cross_list_matches,
    sanctions_by_country,
    sanctions_by_program,
)
from ..sources.tracking import (
    WATCH_BOXES,
    flight_anomalies,
    flight_coverage_by_box,
    maritime_status,
)
from . import watchlists as watchmod


def _meta(pipe: Pipeline) -> dict[str, Any]:
    """Operational context attached to every module response."""
    report = pipe.last_report
    health = pipe.registry.health_report() if pipe.registry else {"sources": [], "degraded": False}
    degraded = [s for s in health.get("sources", []) if not s.get("is_usable")]
    return {
        "generated_at": utcnow().isoformat(),
        "data_built_at": (
            pipe.store.get(DATASET_NEWS).built_at.isoformat()
            if pipe.store.get(DATASET_NEWS)
            else None
        ),
        "sources_total": health.get("total", 0),
        "sources_usable": health.get("usable", 0),
        "degraded": health.get("degraded", False),
        "degraded_sources": [
            {"name": s["name"], "status": s["status"], "error": s.get("error", "")}
            for s in degraded
        ],
        "warnings": report.warnings[-8:],
        "confidence_disclaimer": (
            "Confidence values express the strength of available evidence and are "
            "never a statement of certainty."
        ),
    }


def _signal_payload(sig: Any) -> dict[str, Any]:
    return {
        "id": sig.id,
        "module": sig.module,
        "title": sig.title,
        "description": sig.description,
        "signal_type": sig.signal_type,
        "severity": sig.severity.value,
        "score": sig.score,
        "confidence": sig.confidence,
        "detected_at": sig.detected_at.isoformat(),
        "window_start": sig.window_start.isoformat() if sig.window_start else None,
        "window_end": sig.window_end.isoformat() if sig.window_end else None,
        "countries": sig.countries,
        "continent": sig.continent,
        "metrics": sig.metrics,
        "caveats": sig.caveats,
        "evidence": [e.model_dump(mode="json") for e in sig.evidence],
    }


# -- 1. News Intelligence ---------------------------------------------------


def news_intelligence(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Current and historical news with search, filters and source links."""
    items = pipe.news()
    query = str(params.get("q", "") or "")
    countries = _csv(params.get("countries"))
    continent = params.get("continent")
    source = params.get("source")
    days = _int(params.get("days"), 7)
    min_conf = _float(params.get("min_confidence"), 0.0)
    include_dupes = _bool(params.get("include_duplicates"), False)
    include_syn = _bool(params.get("include_syndicated"), True)
    limit = _int(params.get("limit"), 60)

    since = utcnow() - timedelta(days=days)
    filtered = search_news(
        items,
        query=query,
        countries=countries,
        continent=continent,
        source_name=source,
        since=since,
        min_confidence=min_conf,
        include_duplicates=include_dupes,
        include_syndicated=include_syn,
    )

    syndication = {i.syndication_group for i in items if i.syndication_group}
    return {
        "total_corpus": len(items),
        "returned": len(filtered),
        "articles": [
            {
                "id": i.id,
                "title": i.title,
                "url": i.url,
                "source_name": i.source_name,
                "published_at": i.published_at.isoformat() if i.published_at else None,
                "collected_at": i.collected_at.isoformat(),
                "summary": i.summary,
                "countries": i.countries,
                "continent": i.continent,
                "sentiment": i.sentiment,
                "confidence": i.confidence,
                "quality_flags": i.quality_flags,
                "is_syndicated": i.syndication_group is not None,
                "syndication_group": i.syndication_group,
                "is_duplicate": i.duplicate_of is not None,
                "language": i.language,
            }
            for i in filtered[:limit]
        ],
        "facets": {
            "sources": _facet_counts(i.source_name for i in filtered),
            "countries": _facet_counts(c for i in filtered for c in i.countries),
            "continents": _facet_counts(i.continent for i in filtered if i.continent),
            "feed_packs": _facet_counts(
                f.split(":", 1)[1]
                for i in filtered
                for f in i.quality_flags
                if f.startswith("pack:")
            ),
        },
        "corpus_stats": {
            "syndication_groups": len(syndication),
            "syndicated_copies": sum(1 for i in items if i.syndication_group),
            "duplicates": sum(1 for i in items if i.duplicate_of),
            "with_published_at": sum(1 for i in items if i.published_at is not None),
            "without_published_at": sum(1 for i in items if i.published_at is None),
        },
        "filters_applied": {
            "q": query,
            "countries": countries,
            "continent": continent,
            "days": days,
            "min_confidence": min_conf,
            "include_duplicates": include_dupes,
            "include_syndicated": include_syn,
        },
        "meta": _meta(pipe),
    }


def news_detail(pipe: Pipeline, article_id: str) -> dict[str, Any] | None:
    """One article with its syndication cohort -- who else ran the same story."""
    items = pipe.news()
    item = next((i for i in items if i.id == article_id), None)
    if item is None:
        return None
    cohort = []
    if item.syndication_group:
        cohort = [
            {
                "id": other.id,
                "source_name": other.source_name,
                "url": other.url,
                "published_at": other.published_at.isoformat() if other.published_at else None,
            }
            for other in items
            if other.syndication_group == item.syndication_group and other.id != item.id
        ]
    return {
        "article": {
            **item.model_dump(mode="json"),
            "sentiment_detail": analyze_sentiment(f"{item.title} {item.summary}").as_dict(),
            "keywords": extract_keywords(f"{item.title} {item.summary}"),
            "entities": extract_entities(f"{item.title} {item.summary}"),
        },
        "syndication_cohort": cohort,
        "syndication_note": (
            f"{len(cohort)} other outlet(s) ran near-identical copy of this story. "
            "They are not independent confirmations."
            if cohort
            else "No near-identical copies detected in the current window."
        ),
        "meta": _meta(pipe),
    }


# -- 2. Event Intelligence --------------------------------------------------


def event_intelligence(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Geocoded events with clustering and spike detection."""
    events = pipe.events()
    countries = _csv(params.get("countries"))
    continent = params.get("continent")
    category = params.get("category")
    min_severity = params.get("min_severity")
    days = _int(params.get("days"), 30)
    limit = _int(params.get("limit"), 200)

    since = utcnow() - timedelta(days=days)
    min_rank = _severity_rank(min_severity) if min_severity else 0

    filtered = []
    for ev in events:
        if ev.event_date < since:
            continue
        if countries and ev.country_iso2 not in {c.upper() for c in countries}:
            continue
        if continent and ev.continent != continent:
            continue
        if category and (ev.event_category or "").lower() != category.lower():
            continue
        if ev.severity.rank < min_rank:
            continue
        filtered.append(ev)

    coords = [(e.location.lat, e.location.lon) for e in filtered if e.location]
    iso_of_coord = [e.country_iso2 for e in filtered if e.location]
    clusters = stats.cluster_points(
        coords, eps_km=150.0, min_samples=2, country_ids=iso_of_coord
    )
    cluster_lookup: dict[str, int] = {}
    for cl in clusters:
        for idx in cl.points:
            ev = [e for e in filtered if e.location]
            if idx < len(ev):
                cluster_lookup[ev[idx].id] = cl.cluster_id

    by_category = _facet_counts(e.event_category for e in filtered)
    by_country = _facet_counts(e.country_iso2 for e in filtered if e.country_iso2)

    series = stats.bucket_counts(filtered, "event_date", hours=days * 24, bucket_hours=12)
    spikes = stats.detect_spikes(series, window=10, threshold=2.5, min_baseline_points=5)

    return {
        "total_events": len(events),
        "returned": len(filtered),
        "events": [
            {
                "id": e.id,
                "event_date": e.event_date.isoformat(),
                "collected_at": e.collected_at.isoformat(),
                "category": e.event_category,
                "event_code": e.event_code,
                "actor1": e.actor1,
                "actor2": e.actor2,
                "location": e.location.model_dump(mode="json") if e.location else None,
                "country_iso2": e.country_iso2,
                "continent": e.continent,
                "source_url": e.source_url,
                "mention_count": e.mention_count,
                "num_sources": e.num_sources,
                "tone": e.tone,
                "severity": e.severity.value,
                "confidence": e.confidence,
                "cluster_id": cluster_lookup.get(e.id),
                "quality_flags": e.quality_flags,
            }
            for e in sorted(filtered, key=lambda x: x.event_date, reverse=True)[:limit]
        ],
        "clusters": [c.as_dict() for c in clusters[:40]],
        "aggregates": {
            "by_category": by_category,
            "by_country": by_country[:30] if isinstance(by_country, list) else by_country,
            "timeline": [{"t": ts.isoformat(), "count": c} for ts, c in series],
            "spikes": [
                {
                    "timestamp": sp.timestamp.isoformat() if sp.timestamp else None,
                    "value": sp.value,
                    "baseline_mean": round(sp.baseline_mean, 3),
                    "zscore": round(sp.zscore, 4),
                    "severity": sp.severity.value,
                    "direction": sp.direction,
                }
                for sp in spikes[:20]
            ],
        },
        "filters_applied": {
            "countries": countries,
            "continent": continent,
            "category": category,
            "days": days,
        },
        "meta": _meta(pipe),
    }


# -- 3. Signal Desk ---------------------------------------------------------


def signal_desk(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Narrative tracking, sentiment trends and anomaly detection."""
    news = pipe.news()
    signals = pipe.signals()

    module = params.get("module")
    min_sev = params.get("min_severity")
    days = _int(params.get("days"), 7)
    since = utcnow() - timedelta(days=days)
    min_rank = _severity_rank(min_sev) if min_sev else 0

    filtered = [
        s
        for s in signals
        if s.detected_at >= since
        and (not module or s.module == module)
        and s.severity.rank >= min_rank
    ]

    sentiments: dict[str, list[float]] = {}
    for item in news:
        key = item.continent or "unknown"
        sentiments.setdefault(key, []).append(item.sentiment)

    by_continent = [
        {
            "continent": code,
            "continent_name": CONTINENTS.get(code, code),
            "mean_sentiment": round(sum(vals) / len(vals), 4) if vals else 0.0,
            "articles": len(vals),
            "negative_share": round(
                sum(1 for v in vals if v <= -0.25) / len(vals), 4
            )
            if vals
            else 0.0,
        }
        for code, vals in sorted(sentiments.items())
        if code != "unknown"
    ]

    buckets: dict[str, list[float]] = {}
    for item in news:
        ts = item.published_at or item.collected_at
        buckets.setdefault(ts.strftime("%Y-%m-%d"), []).append(item.sentiment)
    trend = [
        {
            "date": day,
            "mean": round(sum(vals) / len(vals), 4),
            "count": len(vals),
        }
        for day, vals in sorted(buckets.items())
    ]

    return {
        "signals": [_signal_payload(s) for s in filtered[:80]],
        "signal_counts": {
            "total": len(signals),
            "filtered": len(filtered),
            "by_severity": _facet_counts(s.severity.value for s in signals),
            "by_module": _facet_counts(s.module for s in signals),
            "by_type": _facet_counts(s.signal_type for s in signals),
        },
        "sentiment": {
            "by_continent": by_continent,
            "daily_trend": trend,
            "corpus_mean": round(
                sum(i.sentiment for i in news) / len(news), 4
            )
            if news
            else 0.0,
            "method": (
                "Weighted lexicon polarity with negation and intensifier handling, "
                "normalised by document length. Directional, not calibrated."
            ),
        },
        "narratives": narrative_keywords(news, top_n=25),
        "filters_applied": {"module": module, "min_severity": min_sev, "days": days},
        "meta": _meta(pipe),
    }


def narrative_compare(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Compare narratives between two coverage populations.

    The anti-bias instrument: shows what a region's own outlets emphasise
    that the global wire does not.
    """
    news = pipe.news()
    left = str(params.get("left") or "africa")
    right = str(params.get("right") or "global")

    def pack_items(pack: str) -> list[str]:
        if pack in ("global", "wire"):
            packs = {"global"}
        else:
            packs = {pack}
        out = []
        for item in news:
            item_packs = {
                f.split(":", 1)[1] for f in item.quality_flags if f.startswith("pack:")
            }
            if item_packs & packs:
                out.append(f"{item.title} {item.summary}")
        return out

    groups = {left: pack_items(left), right: pack_items(right)}
    return {
        "left": left,
        "right": right,
        "sample_sizes": {k: len(v) for k, v in groups.items()},
        "divergence": narrative_divergence(groups),
        "available_packs": list(PACK_LABELS.keys()),
        "pack_labels": PACK_LABELS,
        "interpretation": (
            "Terms exclusive to one pack indicate storylines that pack covers and the "
            "other does not. This is a prompt for investigation, not a finding: "
            "different feed counts also produce exclusive terms."
        ),
        "meta": _meta(pipe),
    }


# -- 4. ThreatPulse ---------------------------------------------------------


def threat_pulse(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """CVE/CISA KEV cybersecurity alerts."""
    cyber = pipe.cyber()
    min_severity = params.get("min_severity")
    kev_only = _bool(params.get("kev_only"), False)
    ransomware_only = _bool(params.get("ransomware_only"), False)
    days = _int(params.get("days"), 90)
    limit = _int(params.get("limit"), 100)

    since = utcnow() - timedelta(days=days)
    min_rank = _severity_rank(min_severity) if min_severity else 0

    filtered = []
    for item in cyber:
        ts = item.published_at or item.collected_at
        if ts < since:
            continue
        if kev_only and not item.in_kev:
            continue
        if ransomware_only and not item.ransomware_used:
            continue
        if item.severity.rank < min_rank:
            continue
        filtered.append(item)

    return {
        "total_cves": len(cyber),
        "returned": len(filtered),
        "kev_total": sum(1 for c in cyber if c.in_kev),
        "ransomware_linked": sum(1 for c in cyber if c.ransomware_used),
        "alerts": [
            {
                "id": c.id,
                "cve_id": c.cve_id,
                "title": c.title,
                "description": c.description,
                "cvss_score": c.cvss_score,
                "severity": c.severity.value,
                "in_kev": c.in_kev,
                "ransomware_used": c.ransomware_used,
                "vendor": c.vendor,
                "product": c.product,
                "required_action": c.required_action,
                "published_at": c.published_at.isoformat() if c.published_at else None,
                "kev_date_added": c.kev_date_added.isoformat() if c.kev_date_added else None,
                "kev_due_date": c.kev_due_date.isoformat() if c.kev_due_date else None,
                "source_url": c.source_url,
                "confidence": c.confidence,
                "quality_flags": c.quality_flags,
            }
            for c in filtered[:limit]
        ],
        "deadline_pressure": kev_deadline_pressure(cyber)[:40],
        "activity_by_day": cve_activity_by_day(cyber, days=days),
        "aggregates": {
            "by_vendor": _facet_counts(c.vendor for c in cyber if c.vendor)[:25],
            "by_severity": _facet_counts(c.severity.value for c in cyber),
            "cvss_bands": {
                "critical_9_plus": sum(1 for c in cyber if (c.cvss_score or 0) >= 9),
                "high_7_to_9": sum(
                    1 for c in cyber if 7 <= (c.cvss_score or 0) < 9
                ),
                "medium_4_to_7": sum(
                    1 for c in cyber if 4 <= (c.cvss_score or 0) < 7
                ),
                "low_under_4": sum(1 for c in cyber if 0 < (c.cvss_score or 0) < 4),
                "unscored": sum(1 for c in cyber if c.cvss_score is None),
            },
        },
        "notes": {
            "kev_definition": (
                "CISA KEV lists only CVEs with confirmed in-the-wild exploitation. "
                "Membership implies active risk regardless of CVSS score."
            ),
            "nvd_definition": (
                "NVD is the full CVE catalogue. Most entries have no known "
                "exploitation -- absence from KEV is not absence of risk."
            ),
        },
        "meta": _meta(pipe),
    }


# -- 5. SanctionsGraph ------------------------------------------------------


def sanctions_graph(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Sanctions entities and the relationship graph around them."""
    sanctions = pipe.sanctions()
    list_source = params.get("list")
    country = params.get("country")
    entity_type = params.get("entity_type")
    query = str(params.get("q", "") or "").lower()
    limit = _int(params.get("limit"), 120)

    filtered = []
    for s in sanctions:
        if list_source and s.list_source != list_source:
            continue
        if country and country.upper() not in s.countries:
            continue
        if entity_type and s.entity_type != entity_type:
            continue
        if query and query not in s.name.lower() and not any(query in a.lower() for a in s.aliases):
            continue
        filtered.append(s)

    graph = pipe.graph()
    graph_payload = {"nodes": [], "edges": [], "stats": {}}
    if graph is not None:
        sanction_ids = {f"sanction:{s.id}" for s in filtered[:120]}
        if sanction_ids:
            sub = graph.subgraph(sanction_ids, depth=1)
            graph_payload = sub.as_payload(nodes=120, edges=250)
        else:
            graph_payload = graph.as_payload(nodes=60, edges=120)

    return {
        "total_entities": len(sanctions),
        "returned": len(filtered),
        "entities": [
            {
                "id": s.id,
                "name": s.name,
                "entity_type": s.entity_type,
                "list_source": s.list_source,
                "programs": s.programs,
                "countries": s.countries,
                "aliases": s.aliases[:8],
                "addresses": s.addresses[:3],
                "identifiers": s.identifiers,
                "listed_at": s.listed_at.isoformat() if s.listed_at else None,
                "source_url": s.source_url,
                "confidence": s.confidence,
                "quality_flags": s.quality_flags,
            }
            for s in filtered[:limit]
        ],
        "cross_list_matches": cross_list_matches(sanctions)[:40],
        "aggregates": {
            "by_list": _facet_counts(s.list_source for s in sanctions),
            "by_type": _facet_counts(s.entity_type for s in sanctions),
            "by_program": sanctions_by_program(sanctions, top_n=25),
            "by_country": sanctions_by_country(sanctions, top_n=30),
        },
        "graph": graph_payload,
        "sources": {
            "lists": sorted({s.list_source for s in sanctions}),
            "note": (
                "Three separate jurisdictions are kept separate rather than merged: "
                "a UN listing is a materially different condition from a single "
                "national listing."
            ),
        },
        "meta": _meta(pipe),
    }


# -- 6. FlightWatch ---------------------------------------------------------


def flight_watch(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Aircraft movement anomalies."""
    tracks = [t for t in pipe.tracks() if t.kind == "aircraft"]
    threshold = _float(params.get("threshold"), 0.5)
    anomalies = flight_anomalies(tracks, threshold=threshold)

    return {
        "total_observations": len(tracks),
        "distinct_aircraft": len({t.identifier for t in tracks}),
        "anomalies": anomalies[:100],
        "coverage_by_box": flight_coverage_by_box(tracks),
        "aggregates": {
            "by_country": _facet_counts(t.country for t in tracks if t.country)[:25],
            "without_callsign": sum(1 for t in tracks if "no_callsign" in t.quality_flags),
            "on_ground": sum(1 for t in tracks if t.on_ground),
            "altitude_reported": sum(1 for t in tracks if t.altitude_m is not None),
        },
        "observations": [
            {
                "id": t.id,
                "identifier": t.identifier,
                "callsign": t.callsign,
                "country": t.country,
                "lat": t.lat,
                "lon": t.lon,
                "altitude_m": t.altitude_m,
                "velocity_ms": t.velocity_ms,
                "heading": t.heading,
                "on_ground": t.on_ground,
                "anomaly_score": t.anomaly_score,
                "quality_flags": t.quality_flags,
            }
            for t in sorted(tracks, key=lambda x: -x.anomaly_score)[:200]
        ],
        "watch_boxes": [
            {"label": label, "bounds": [lamin, lomin, lamax, lomax]}
            for label, lamin, lomin, lamax, lomax in WATCH_BOXES
        ],
        "caveats": [
            "ADS-B signals can be spoofed or lost. Every figure here describes "
            "transponder behaviour, not intent or identity.",
            "Military and state aircraft routinely fly without broadcasting a "
            "callsign. That pattern is common and not by itself irregular.",
            "Anomaly scores are relative to the aircraft's own origin-country "
            "cohort, not to a global baseline.",
        ],
        "meta": _meta(pipe),
    }


# -- 7. MaritimeWatch -------------------------------------------------------


def maritime_watch(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Vessel activity signals, with honest availability reporting."""
    sanctions = pipe.sanctions()
    vessels = [s for s in sanctions if s.entity_type == "vessel"]

    ais_source = pipe.registry.get("ais") if pipe.registry else None
    status = (
        maritime_status(ais_source)
        if ais_source is not None
        else {
            "live_feed_available": False,
            "status": "unknown",
            "reason": "AIS source not registered",
            "what_is_shown": "sanctioned vessels only",
            "notes": "",
        }
    )

    return {
        "status": status,
        "sanctioned_vessels": [
            {
                "id": v.id,
                "name": v.name,
                "list_source": v.list_source,
                "programs": v.programs,
                "countries": v.countries,
                "identifiers": v.identifiers,
                "aliases": v.aliases[:5],
                "source_url": v.source_url,
                "confidence": v.confidence,
            }
            for v in vessels[:100]
        ],
        "sanctioned_vessel_count": len(vessels),
        "aggregates": {
            "by_list": _facet_counts(v.list_source for v in vessels),
            "by_program": sanctions_by_program(vessels, top_n=15) if vessels else [],
        },
        "what_this_module_can_and_cannot_show": {
            "can_show": [
                "Vessels named on OFAC, EU and UN sanctions lists",
                "Which jurisdictions list each vessel and under which programme",
            ],
            "cannot_show": [
                "Live global vessel positions -- no free key-less feed exists",
                "Vessel tracks or dark-fleet detection without a commercial AIS provider",
            ],
            "why": (
                "Authoritative global live AIS is commercial. Rather than approximate "
                "positions, this module reports the real availability state so an "
                "empty map is never mistaken for an empty ocean."
            ),
        },
        "meta": _meta(pipe),
    }


# -- 8. Timeline & Replay ---------------------------------------------------


def timeline_replay(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Historical reconstruction and animation frames."""
    days = _int(params.get("days"), 14)
    continent = params.get("continent")
    countries = _csv(params.get("countries"))
    bucket_hours = _int(params.get("bucket_hours"), 12)

    events = pipe.events()
    news = pipe.news()
    since = utcnow() - timedelta(days=days)
    wanted = {c.upper() for c in countries} if countries else set()

    def keep_iso(iso: str | None) -> bool:
        if not iso:
            return False
        if wanted and iso not in wanted:
            return False
        if continent:
            country = get_country(iso)
            if not country or country.continent != continent:
                return False
        return True

    filtered_events = [
        e for e in events if e.event_date >= since and keep_iso(e.country_iso2)
    ]
    filtered_news = [
        n
        for n in news
        if (n.published_at or n.collected_at) >= since
        and any(keep_iso(c) for c in n.countries)
    ]

    n_buckets = max(1, int(days * 24 / bucket_hours))
    start = (utcnow() - timedelta(days=days)).replace(
        minute=0, second=0, microsecond=0
    )
    frames: list[dict[str, Any]] = []
    for i in range(n_buckets):
        b_start = start + timedelta(hours=i * bucket_hours)
        b_end = b_start + timedelta(hours=bucket_hours)
        bucket_events = [
            e for e in filtered_events if b_start <= e.event_date < b_end
        ]
        bucket_news = [
            n
            for n in filtered_news
            if b_start <= (n.published_at or n.collected_at) < b_end
        ]
        sev_counts = _facet_counts(e.severity.value for e in bucket_events)
        frames.append(
            {
                "frame": i,
                "window_start": b_start.isoformat(),
                "window_end": b_end.isoformat(),
                "event_count": len(bucket_events),
                "news_count": len(bucket_news),
                "by_severity": sev_counts,
                "by_country": _facet_counts(
                    e.country_iso2 for e in bucket_events if e.country_iso2
                ),
                "points": [
                    {
                        "lat": e.location.lat,
                        "lon": e.location.lon,
                        "severity": e.severity.value,
                        "category": e.event_category,
                        "country_iso2": e.country_iso2,
                        "source_url": e.source_url,
                    }
                    for e in bucket_events
                    if e.location
                ][:200],
            }
        )

    return {
        "window": {"days": days, "bucket_hours": bucket_hours, "start": start.isoformat()},
        "frames": frames,
        "totals": {
            "events": len(filtered_events),
            "news": len(filtered_news),
            "countries": len({e.country_iso2 for e in filtered_events if e.country_iso2}),
        },
        "note": (
            "Frames are zero-filled: an empty frame means no *collected* activity in "
            "that window, which may reflect feed reach rather than real quiet."
        ),
        "meta": _meta(pipe),
    }


# -- 9. Watchlists & Digests ------------------------------------------------


def watchlists_module(
    pipe: Pipeline, params: dict[str, Any], store: watchmod.WatchlistStore
) -> dict[str, Any]:
    """List, create, delete watchlists and generate digests."""
    action = str(params.get("action") or "list")

    if action == "create":
        countries = _csv(params.get("countries"))
        name = str(params.get("name") or "") or "Watchlist"
        wl = store.create(
            name,
            countries,
            modules=_csv(params.get("modules")),
            min_severity=str(params.get("min_severity") or Severity.LOW.value),
            frequency="weekly" if params.get("frequency") == "weekly" else "daily",
            notes=str(params.get("notes") or ""),
        )
        return {"action": "create", "watchlist": wl.as_dict(), "meta": _meta(pipe)}

    if action == "delete":
        wid = str(params.get("id") or "")
        removed = store.delete(wid)
        return {
            "action": "delete",
            "id": wid,
            "removed": removed,
            "watchlists": [w.as_dict() for w in store.all()],
            "meta": _meta(pipe),
        }

    if action == "digest":
        wid = str(params.get("id") or "")
        wl = store.get(wid)
        if wl is None:
            return {
                "action": "digest",
                "error": "watchlist not found",
                "available": [w.id for w in store.all()],
                "meta": _meta(pipe),
            }
        coverage = pipe.coverage()
        cov = coverage_index(coverage)
        news_lookup = _news_by_country(pipe.news())
        digest = watchmod.digest_for(
            wl, pipe.signals(), coverage_by_country=cov, news_lookup=news_lookup
        )
        digest["text_preview"] = watchmod.digest_text(digest)
        digest["meta"] = _meta(pipe)
        return {"action": "digest", "digest": digest}

    return {
        "action": "list",
        "watchlists": [w.as_dict() for w in store.all()],
        "storage_note": (
            "Watchlists live in process memory. On a serverless deployment they do "
            "not survive a cold start -- durable storage is a deployment decision."
        ),
        "available_frequencies": ["daily", "weekly"],
        "meta": _meta(pipe),
    }


# -- 10. Evidence Explorer --------------------------------------------------


def evidence_explorer(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Sources, links, timestamps and reasoning behind every signal."""
    signals = pipe.signals()
    signal_id = params.get("signal_id")

    if signal_id:
        sig = next((s for s in signals if s.id == signal_id), None)
        if sig is None:
            return {"found": False, "signal_id": signal_id, "meta": _meta(pipe)}
        return {
            "found": True,
            "signal": _signal_payload(sig),
            "reasoning": _reasoning_for(sig),
            "meta": _meta(pipe),
        }

    return {
        "found": True,
        "signal_count": len(signals),
        "signals": [_signal_payload(s) for s in signals[:60]],
        "provenance": {
            "bronze": pipe.store.bronze.stats(),
            "datasets": pipe.store.summary()["datasets"],
        },
        "chain_of_custody": {
            "bronze": "Raw upstream payload, appended and never mutated.",
            "silver": "Cleansed, deduplicated, country-resolved, quality-scored.",
            "gold": "Aggregated and evidence-linked.",
            "note": (
                "Every signal records the collection time and the original "
                "publication time separately, so a freshly-collected old story is "
                "distinguishable from a new one."
            ),
        },
        "meta": _meta(pipe),
    }


def _reasoning_for(sig: Any) -> dict[str, Any]:
    metrics = sig.metrics or {}
    steps = [
        {
            "step": 1,
            "action": "collect",
            "detail": "Records fetched from the listed upstream sources and archived "
            "to the immutable bronze layer.",
        },
        {
            "step": 2,
            "action": "deduplicate",
            "detail": (
                "Exact duplicates removed by canonical URL; near-identical copies "
                "grouped as syndications, so one wire story is not counted as many."
            ),
        },
        {
            "step": 3,
            "action": "detect",
            "detail": f"Detector '{sig.module}' produced this {sig.signal_type} signal.",
        },
        {
            "step": 4,
            "action": "score",
            "detail": (
                f"Score {sig.score}, confidence {sig.confidence}. Confidence reflects "
                "evidence strength, not the probability that the finding is true."
            ),
        },
    ]
    if "coverage_confidence" in metrics:
        steps.append(
            {
                "step": 5,
                "action": "qualify",
                "detail": (
                    f"Coverage confidence for this territory is "
                    f"{metrics['coverage_confidence']}. Low values mean absence of "
                    "signals is weak evidence."
                ),
            }
        )
    return {
        "steps": steps,
        "metrics": metrics,
        "caveats": sig.caveats,
        "evidence_count": len(sig.evidence),
    }


# -- 11. Global map ---------------------------------------------------------


def map_view(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Points, choropleth values and filters for the global map."""
    coverage = pipe.coverage()
    events = pipe.events()
    days = _int(params.get("days"), 30)
    continent = params.get("continent")
    category = params.get("category")
    severity = params.get("min_severity")
    since = utcnow() - timedelta(days=days)
    min_rank = _severity_rank(severity) if severity else 0

    points = []
    for e in events:
        if e.event_date < since or e.location is None:
            continue
        if continent and e.continent != continent:
            continue
        if category and (e.event_category or "").lower() != category.lower():
            continue
        if e.severity.rank < min_rank:
            continue
        points.append(
            {
                "id": e.id,
                "lat": e.location.lat,
                "lon": e.location.lon,
                "place": e.location.place,
                "precision": e.location.precision,
                "country_iso2": e.country_iso2,
                "category": e.event_category,
                "severity": e.severity.value,
                "confidence": e.confidence,
                "event_date": e.event_date.isoformat(),
                "source_url": e.source_url,
            }
        )

    choropleth = [
        {
            "country_iso2": c.country_iso2,
            "country_name": c.country_name,
            "events": c.event_count,
            "news": c.news_count,
            "coverage_confidence": c.coverage_confidence,
            "data_state": c.data_state,
            "centroid": _centroid(c.country_iso2),
        }
        for c in coverage
    ]

    return {
        "points": points[:2000],
        "point_count": len(points),
        "choropleth": choropleth,
        "continents": continent_summary(coverage),
        "bounds": _bounds(points),
        "filters": {
            "days": days,
            "continent": continent,
            "category": category,
            "min_severity": severity,
            "available_categories": sorted(
                {e.event_category for e in events if e.event_category}
            ),
            "available_continents": [
                {"code": k, "name": v} for k, v in CONTINENTS.items()
            ],
        },
        "legend_note": (
            "Choropleth shading is coverage confidence, not event volume, so a dark "
            "cell always means 'well observed'. Event counts are shown separately."
        ),
        "meta": _meta(pipe),
    }


# -- 12. Entity explorer ----------------------------------------------------


def entity_explorer(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Explore entities and relationships across every node type."""
    graph = pipe.graph()
    if graph is None:
        return {"nodes": [], "edges": [], "stats": {}, "meta": _meta(pipe)}

    kind = params.get("kind")
    node_id = params.get("node_id")
    query = str(params.get("q", "") or "").lower()
    limit = _int(params.get("limit"), 80)

    if node_id:
        if node_id not in graph.nodes:
            return {"found": False, "node_id": node_id, "meta": _meta(pipe)}
        sub = graph.subgraph([node_id], depth=_int(params.get("depth"), 1))
        payload = sub.as_payload(nodes=120, edges=250)
        node = graph.nodes[node_id]
        return {
            "found": True,
            "node": node.as_dict(),
            "neighbours": graph.neighbours(node_id)[:80],
            "edges": [
                e.as_dict()
                for e in sorted(graph.edges_of(node_id), key=lambda x: -x.weight)[:80]
            ],
            "subgraph": payload,
            "meta": _meta(pipe),
        }

    nodes = list(graph.nodes.values())
    if kind:
        nodes = [n for n in nodes if n.kind == kind]
    if query:
        nodes = [n for n in nodes if query in n.label.lower() or query in n.id.lower()]
    nodes.sort(key=lambda n: (-n.weight, n.label))

    return {
        "found": True,
        "stats": graph.stats(),
        "node_kinds": graph.stats().get("nodes_by_kind", {}),
        "nodes": [n.as_dict() for n in nodes[:limit]],
        "top_edges": [e.as_dict() for e in graph.top_edges(60)],
        "communities": [
            {
                "size": len(c),
                "members": [
                    {"id": m, "label": graph.nodes[m].label, "kind": graph.nodes[m].kind}
                    for m in c[:20]
                ],
            }
            for c in graph.communities(min_size=3)[:12]
        ],
        "graph": graph.as_payload(nodes=80, edges=180),
        "node_type_guide": {
            "country": "Territory resolved from text",
            "org": "Organisation, programme or vendor",
            "person": "Person name candidate",
            "location": "Place name candidate",
            "sanction": "Entity on a sanctions list",
            "cve": "Vulnerability identifier",
            "story": "Collected news article",
        },
        "meta": _meta(pipe),
    }


# -- 13. Backtesting --------------------------------------------------------


def backtesting(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Signal evaluation: precision, recall, false positives, lead time."""
    days = _int(params.get("days"), 30)
    detector = str(params.get("detector") or "spike")
    return {
        "evaluation": pipe.backtest(days=days, detector=detector),
        "method": {
            "matching": (
                "Detection-side greedy nearest-in-time matching. A detection matches "
                "an event on the same country if it fires at most 72h before, or up "
                "to 6h after. One event matches at most one detection, so repeated "
                "firing around a single event counts as one hit plus false positives."
            ),
            "ground_truth": (
                "Events at MEDIUM severity or above. Using every recorded event would "
                "make recall look excellent while measuring nothing."
            ),
            "threshold_sweep": (
                "Precision and recall across thresholds 1.5 to 4.0. There is no single "
                "correct operating point -- it depends on the cost of a miss versus "
                "the cost of a false alarm."
            ),
        },
        "how_to_read_this": [
            "Precision = of the alerts fired, how many had a real event behind them.",
            "Recall = of the real events, how many the detector found.",
            "Lead time = how early a detection fired before the event was recorded.",
            "Coverage-adjusted recall discounts misses in thinly-observed territories, "
            "separating detector failure from lack of data.",
        ],
        "meta": _meta(pipe),
    }


# -- 14. Coverage dashboard -------------------------------------------------


def coverage_dashboard(pipe: Pipeline, params: dict[str, Any]) -> dict[str, Any]:
    """Per-continent and per-country coverage with confidence scoring."""
    coverage = pipe.coverage()
    continent = params.get("continent")

    cells = coverage
    if continent:
        cells = [c for c in coverage if c.continent == continent]

    states = _facet_counts(c.data_state for c in cells)
    return {
        "continents": continent_summary(coverage),
        "africa_check": africa_check(coverage),
        "countries": [
            {
                "country_iso2": c.country_iso2,
                "country_name": c.country_name,
                "continent": c.continent,
                "continent_name": c.continent_name,
                "events": c.event_count,
                "news": c.news_count,
                "distinct_sources": c.distinct_sources,
                "coverage_confidence": c.coverage_confidence,
                "data_state": c.data_state,
                "explanation": c.explanation,
                "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
            }
            for c in sorted(cells, key=lambda x: -x.coverage_confidence)
        ],
        "summary": {
            "territories": len(cells),
            "with_data": sum(1 for c in cells if c.coverage_confidence > 0),
            "no_data": sum(1 for c in cells if c.data_state == "no_data"),
            "by_state": states,
            "mean_coverage_confidence": round(
                sum(c.coverage_confidence for c in cells) / len(cells), 4
            )
            if cells
            else 0.0,
        },
        "focus_regions": focus_regions(coverage),
        "scoring": {
            "distinct_sources": 0.45,
            "volume": 0.30,
            "pack_diversity": 0.15,
            "recency": 0.10,
            "population_used": False,
            "why_population_is_excluded": (
                "Population would permanently label small countries low-data "
                "regardless of how well they are actually observed, encoding a bias "
                "instead of measuring one."
            ),
        },
        "interpretation": (
            "A dark cell means 'we cannot see this territory well', which is a "
            "different claim from 'nothing happened there'. The platform never "
            "asserts the second."
        ),
        "meta": _meta(pipe),
    }


# -- helpers ----------------------------------------------------------------


def _csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(",") if p.strip()]


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _severity_rank(value: Any) -> int:
    try:
        return Severity(str(value)).rank
    except ValueError:
        return 0


def _facet_counts(values: Any) -> list[dict[str, Any]]:
    from collections import Counter

    counter: Counter[str] = Counter(str(v) for v in values if v)
    return [{"key": k, "count": c} for k, c in counter.most_common(60)]


def _news_by_country(items: list[Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.duplicate_of:
            continue
        for iso in item.countries:
            out.setdefault(iso, []).append(
                {
                    "title": item.title,
                    "url": item.url,
                    "source_name": item.source_name,
                    "published_at": item.published_at.isoformat()
                    if item.published_at
                    else None,
                }
            )
        for iso in list(out):
            out[iso].sort(
                key=lambda r: r["published_at"] or "", reverse=True
            )
    return out


def _centroid(iso2: str) -> dict[str, float] | None:
    c = get_country(iso2)
    return {"lat": c.lat, "lon": c.lon} if c else None


def _bounds(points: list[dict[str, Any]]) -> dict[str, float]:
    if not points:
        return {"min_lat": -90, "max_lat": 90, "min_lon": -180, "max_lon": 180}
    lats = [p["lat"] for p in points]
    lons = [p["lon"] for p in points]
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
    }


# -- dispatch ---------------------------------------------------------------

#: The fourteen modules, in product order.
MODULES: dict[str, Callable[..., dict[str, Any]]] = {
    "news": news_intelligence,
    "events": event_intelligence,
    "signals": signal_desk,
    "threats": threat_pulse,
    "sanctions": sanctions_graph,
    "flights": flight_watch,
    "maritime": maritime_watch,
    "timeline": timeline_replay,
    "watchlists": watchlists_module,
    "evidence": evidence_explorer,
    "map": map_view,
    "entities": entity_explorer,
    "backtest": backtesting,
    "coverage": coverage_dashboard,
}

MODULE_INDEX: list[dict[str, str]] = [
    {"id": "news", "name": "News Intelligence", "description": "Searchable news with source links, dedup and syndication tracking."},
    {"id": "events", "name": "Event Intelligence", "description": "Geocoded events with clustering and spike detection."},
    {"id": "signals", "name": "Signal Desk", "description": "Narrative tracking, sentiment trends and anomaly detection."},
    {"id": "threats", "name": "ThreatPulse", "description": "CVE and CISA KEV cybersecurity alerts."},
    {"id": "sanctions", "name": "SanctionsGraph", "description": "Sanctions entities and their relationship graph."},
    {"id": "flights", "name": "FlightWatch", "description": "Aircraft movement anomalies from open ADS-B."},
    {"id": "maritime", "name": "MaritimeWatch", "description": "Vessel activity signals, with honest availability reporting."},
    {"id": "timeline", "name": "Timeline & Replay", "description": "Historical reconstruction and animation frames."},
    {"id": "watchlists", "name": "Watchlists & Digests", "description": "Personalised monitoring with daily and weekly digests."},
    {"id": "evidence", "name": "Evidence Explorer", "description": "Sources, timestamps and reasoning behind every signal."},
    {"id": "map", "name": "Global Map", "description": "Global view with date, category, severity and confidence filters."},
    {"id": "entities", "name": "Entity Explorer", "description": "Explore people, organisations, countries, vessels, CVEs and stories."},
    {"id": "backtest", "name": "Backtesting", "description": "Signal evaluation: precision, recall, false positives and lead time."},
    {"id": "coverage", "name": "Coverage Dashboard", "description": "Per-continent and per-country coverage confidence."},
]


def run_module(
    module_id: str,
    pipe: Pipeline,
    params: dict[str, Any],
    watch_store: watchmod.WatchlistStore,
) -> dict[str, Any] | None:
    """Execute one module by id, or return None if it does not exist."""
    fn = MODULES.get(module_id)
    if fn is None:
        return None
    if module_id == "watchlists":
        return fn(pipe, params, watch_store)
    return fn(pipe, params)
