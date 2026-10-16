"""Collection and aggregation pipeline.

Single entry point that turns upstream feeds into the datasets every module
reads. Order of operations is deliberate:

1. **Collect** -- all sources run concurrently, each independently guarded.
2. **Silver** -- deduplicate, score, resolve countries, extract sentiment.
   This happens before aggregation so that aggregates never count a
   syndicated wire story forty times.
3. **Gold** -- aggregates, signals, graph, coverage, backtest.

The dedup-before-aggregate ordering is the single most consequential choice
here. Running aggregates on raw ingest inflates every count by the
syndication factor, which varies by region -- so the distortion is not a
uniform inflation that cancels out, it actively biases the map toward
whichever region's wires syndicate most.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import get_settings
from .coverage import compute_coverage, coverage_index
from .engine import backtest as bt
from .engine import graph as graphmod
from .engine import stats
from .engine.nlp import analyze_sentiment, narrative_keywords
from .geo import all_countries
from .geo import find as find_countries
from .geo import get as get_country
from .http import HttpClient
from .models import (
    CoverageCell,
    CyberItem,
    EventItem,
    Layer,
    NewsItem,
    SanctionItem,
    Signal,
    TrackItem,
    utcnow,
)
from .quality import dedupe_events, deduplicate, score_news_item
from .sources.base import SourceRegistry
from .sources.cyber import CisaKevSource, NvdSource, merge_cyber
from .sources.news import GdeltSource, RssSource
from .sources.sanctions import (
    EuSanctionsSource,
    OfacSdnSource,
    UnSanctionsSource,
)
from .sources.tracking import AisVesselSource, OpenSkySource
from .store import Dataset, MedallionStore

DATASET_RAW_NEWS = "bronze.news"
DATASET_RAW_EVENTS = "bronze.events"
DATASET_NEWS = "silver.news"
DATASET_EVENTS = "silver.events"
DATASET_CYBER = "silver.cyber"
DATASET_SANCTIONS = "silver.sanctions"
DATASET_TRACKS = "silver.tracks"
DATASET_COVERAGE = "gold.coverage"
DATASET_COUNTRY_STATS = "gold.country_stats"
DATASET_SIGNALS = "gold.signals"
DATASET_GRAPH = "gold.graph"


@dataclass
class PipelineReport:
    """What happened on this run, in enough detail to debug a bad map."""

    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    sources_run: int = 0
    raw_counts: dict[str, int] = field(default_factory=dict)
    silver_counts: dict[str, int] = field(default_factory=dict)
    dedup: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        duration = None
        if self.finished_at:
            duration = round((self.finished_at - self.started_at).total_seconds(), 2)
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": duration,
            "sources_run": self.sources_run,
            "raw_counts": self.raw_counts,
            "silver_counts": self.silver_counts,
            "dedup": self.dedup,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class Pipeline:
    """Owns the source registry, the store and the refresh logic."""

    def __init__(self, store: MedallionStore | None = None) -> None:
        self.settings = get_settings()
        self.store = store or MedallionStore(ttl_seconds=self.settings.refresh_ttl_seconds)
        self._client: HttpClient | None = None
        self.registry: SourceRegistry | None = None
        self.last_report = PipelineReport()
        self._built_once = False

    # -- setup -------------------------------------------------------------

    async def _ensure_registry(self) -> SourceRegistry:
        if self.registry is not None:
            return self.registry
        if self._client is None:
            self._client = HttpClient()
            await self._client.start()
        reg = SourceRegistry(self._client, self.store)
        reg.register(RssSource(self._client, self.store))
        reg.register(GdeltSource(self._client, self.store))
        reg.register(CisaKevSource(self._client, self.store))
        reg.register(NvdSource(self._client, self.store))
        reg.register(OfacSdnSource(self._client, self.store))
        reg.register(EuSanctionsSource(self._client, self.store))
        reg.register(UnSanctionsSource(self._client, self.store))
        reg.register(OpenSkySource(self._client, self.store))
        reg.register(AisVesselSource(self._client, self.store))
        self.registry = reg
        return reg

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self.registry = None

    # -- refresh -----------------------------------------------------------

    async def refresh(self, force: bool = False, only: list[str] | None = None) -> PipelineReport:
        """Run collection and rebuild every dataset.

        Guarded by a coarse freshness check: an unforced refresh inside the
        TTL window is a no-op, which keeps repeated page loads from hammering
        upstream feeds whose data has not changed.
        """
        report = PipelineReport()
        if not force and self._built_once and self._is_fresh():
            report.warnings.append("refresh skipped: datasets are within TTL")
            report.finished_at = utcnow()
            self.last_report = report
            return report

        reg = await self._ensure_registry()
        raw = await reg.run_all(only=only)
        report.sources_run = len([r for r in raw.values() if r is not None])
        report.raw_counts = {k: len(v) for k, v in raw.items()}

        for source in reg.all():
            if source.health.error:
                report.warnings.append(f"{source.name}: {source.health.error}")

        self._build_silver(raw, report)
        self._build_gold(report)

        report.finished_at = utcnow()
        self.last_report = report
        self._built_once = True
        return report

    async def ensure_ready(self) -> None:
        """Build once if nothing is in the store. Used by every endpoint."""
        if not self._built_once:
            await self.refresh(force=False)

    def _is_fresh(self) -> bool:
        ds = self.store.get(DATASET_NEWS)
        return ds is not None and ds.is_fresh()

    # -- silver ------------------------------------------------------------

    def _build_silver(self, raw: dict[str, list[Any]], report: PipelineReport) -> None:
        ttl = self.settings.refresh_ttl_seconds

        # --- news ---------------------------------------------------------
        news: list[NewsItem] = list(raw.get("rss") or [])
        self.store.put(
            Dataset(name=DATASET_RAW_NEWS, layer=Layer.BRONZE, records=list(news), ttl_seconds=ttl)
        )

        # Sentiment before dedup so every copy carries its own score; the
        # surviving record's score is then comparable to the dropped ones.
        for item in news:
            if not item.sentiment:
                result = analyze_sentiment(f"{item.title} {item.summary}")
                item.sentiment = round(result.score, 4)
            if not item.simhash:
                from .quality import simhash

                item.simhash = simhash(f"{item.title} {item.summary}")
            if not item.countries:
                item.countries = find_countries(f"{item.title} {item.summary}")
            if item.countries and not item.continent:
                country = get_country(item.countries[0])
                item.continent = country.continent if country else None

        news, dedup_stats = deduplicate(news)
        for item in news:
            if item.duplicate_of:
                continue
            rep = score_news_item(item)
            item.confidence = round(rep.score, 4)
            base_flags = [f for f in item.quality_flags if f.startswith("pack:")]
            item.quality_flags = sorted(set(rep.flags + base_flags))

        report.dedup = dedup_stats.as_dict()
        report.silver_counts["news"] = len(news)
        self.store.put(
            Dataset(
                name=DATASET_NEWS,
                layer=Layer.SILVER,
                records=news,
                ttl_seconds=ttl,
                notes=[f"dedup: {dedup_stats.as_dict()}"],
            )
        )

        # --- events -------------------------------------------------------
        events: list[EventItem] = list(raw.get("gdelt") or [])
        self.store.put(
            Dataset(
                name=DATASET_RAW_EVENTS,
                layer=Layer.BRONZE,
                records=list(events),
                ttl_seconds=ttl,
            )
        )
        events, removed = dedupe_events(events)
        report.silver_counts["events"] = len(events)
        if removed:
            report.warnings.append(f"events: collapsed {removed} duplicate event rows")
        self.store.put(
            Dataset(
                name=DATASET_EVENTS,
                layer=Layer.SILVER,
                records=events,
                ttl_seconds=ttl,
                notes=[f"collapsed {removed} duplicates"],
            )
        )

        # --- cyber --------------------------------------------------------
        kev: list[CyberItem] = list(raw.get("cisa_kev") or [])
        nvd: list[CyberItem] = list(raw.get("nvd") or [])
        cyber = merge_cyber(kev, nvd)
        report.silver_counts["cyber"] = len(cyber)
        self.store.put(
            Dataset(
                name=DATASET_CYBER,
                layer=Layer.SILVER,
                records=cyber,
                ttl_seconds=ttl,
                notes=[f"{len(kev)} KEV records joined with {len(nvd)} NVD records"],
            )
        )

        # --- sanctions ----------------------------------------------------
        sanctions: list[SanctionItem] = []
        for key in ("ofac_sdn", "eu_sanctions", "un_sanctions"):
            sanctions.extend(raw.get(key) or [])
        report.silver_counts["sanctions"] = len(sanctions)
        self.store.put(
            Dataset(
                name=DATASET_SANCTIONS,
                layer=Layer.SILVER,
                records=sanctions,
                ttl_seconds=ttl,
                notes=[f"{len(sanctions)} records across three jurisdictions"],
            )
        )

        # --- tracks -------------------------------------------------------
        tracks: list[TrackItem] = list(raw.get("opensky") or [])
        report.silver_counts["tracks"] = len(tracks)
        self.store.put(
            Dataset(name=DATASET_TRACKS, layer=Layer.SILVER, records=tracks, ttl_seconds=ttl)
        )

    # -- gold --------------------------------------------------------------

    def _build_gold(self, report: PipelineReport) -> None:
        ttl = self.settings.refresh_ttl_seconds
        news = self._records(DATASET_NEWS)
        events = self._records(DATASET_EVENTS)
        cyber = self._records(DATASET_CYBER)
        sanctions = self._records(DATASET_SANCTIONS)
        tracks = self._records(DATASET_TRACKS)

        coverage = compute_coverage(news_items=news, events=events)
        self.store.put(
            Dataset(name=DATASET_COVERAGE, layer=Layer.GOLD, records=coverage, ttl_seconds=ttl)
        )

        country_stats = self._country_stats(news, events, coverage)
        self.store.put(
            Dataset(
                name=DATASET_COUNTRY_STATS,
                layer=Layer.GOLD,
                records=country_stats,
                ttl_seconds=ttl,
            )
        )

        signals = self._build_signals(news, events, cyber, tracks, coverage)
        self.store.put(
            Dataset(name=DATASET_SIGNALS, layer=Layer.GOLD, records=signals, ttl_seconds=ttl)
        )

        graph = self._build_graph(news, sanctions, cyber)
        self.store.put(
            Dataset(
                name=DATASET_GRAPH,
                layer=Layer.GOLD,
                records=[graph],
                ttl_seconds=ttl,
                notes=["single aggregated relationship graph"],
            )
        )

        report.silver_counts["signals"] = len(signals)
        report.silver_counts["countries_with_data"] = len(
            [c for c in coverage if c.coverage_confidence > 0]
        )
        offline = [c.country_name for c in coverage if c.data_state == "no_data"]
        if offline:
            report.warnings.append(
                f"{len(offline)} territories have no data this window "
                f"(coverage confidence 0) -- reported, not hidden"
            )

    def _country_stats(
        self,
        news: list[NewsItem],
        events: list[EventItem],
        coverage: list[CoverageCell],
    ) -> list[dict[str, Any]]:
        by_iso: dict[str, dict[str, Any]] = {}
        for cell in coverage:
            by_iso[cell.country_iso2] = {
                "country_iso2": cell.country_iso2,
                "country_name": cell.country_name,
                "continent": cell.continent,
                "continent_name": cell.continent_name,
                "news_count": cell.news_count,
                "event_count": cell.event_count,
                "distinct_sources": cell.distinct_sources,
                "coverage_confidence": cell.coverage_confidence,
                "data_state": cell.data_state,
                "population_millions": cell.population_millions,
                "sentiment_mean": 0.0,
                "top_categories": [],
                "last_seen_at": cell.last_seen_at.isoformat() if cell.last_seen_at else None,
            }

        sentiments: dict[str, list[float]] = {}
        for item in news:
            for iso in item.countries:
                sentiments.setdefault(iso, []).append(item.sentiment)

        categories: dict[str, dict[str, int]] = {}
        for ev in events:
            iso = ev.country_iso2
            if not iso:
                continue
            bucket = categories.setdefault(iso, {})
            bucket[ev.event_category] = bucket.get(ev.event_category, 0) + 1

        for iso, stats_row in by_iso.items():
            scores = sentiments.get(iso)
            if scores:
                stats_row["sentiment_mean"] = round(sum(scores) / len(scores), 4)
            cats = categories.get(iso)
            if cats:
                stats_row["top_categories"] = [
                    {"category": k, "count": v}
                    for k, v in sorted(cats.items(), key=lambda kv: -kv[1])[:5]
                ]
        return list(by_iso.values())

    def _build_signals(
        self,
        news: list[NewsItem],
        events: list[EventItem],
        cyber: list[CyberItem],
        tracks: list[TrackItem],
        coverage: list[CoverageCell],
    ) -> list[Signal]:
        from .models import Severity

        signals: list[Signal] = []
        coverage_by_iso = coverage_index(coverage)

        # --- 1. country-level event spikes ---------------------------------
        by_country: dict[str, list[EventItem]] = {}
        for ev in events:
            if ev.country_iso2:
                by_country.setdefault(ev.country_iso2, []).append(ev)

        for iso, country_events in by_country.items():
            series = stats.bucket_counts(country_events, "event_date", hours=168, bucket_hours=6)
            spikes = stats.detect_spikes(series, window=10, threshold=2.5, key=iso, min_baseline_points=5)
            for sp in spikes[:3]:
                country = get_country(iso)
                conf = coverage_by_iso.get(iso, 0.3)
                caveats = [
                    "Detected against the causal baseline for this territory only.",
                    "Syndicated coverage is collapsed before counting, but a single "
                    "outlet filing repeatedly can still inflate volume.",
                ]
                if conf < 0.35:
                    caveats.append(
                        f"Coverage confidence for this territory is {conf:.2f}. "
                        "A spike in a thinly-observed country is as likely to reflect "
                        "a change in feed reach as a change on the ground."
                    )
                signals.append(
                    Signal(
                        id=f"spike-{iso}-{int(sp.index)}",
                        module="events",
                        title=f"Event activity spike in {country.name if country else iso}",
                        description=(
                            f"{sp.value:.0f} events in a 6-hour bucket versus a "
                            f"baseline of {sp.baseline_mean:.1f} "
                            f"(z={sp.zscore:.2f}, {sp.direction})."
                        ),
                        signal_type="spike",
                        window_start=sp.timestamp,
                        window_end=sp.timestamp,
                        countries=[iso],
                        continent=country.continent if country else None,
                        score=round(sp.magnitude, 4),
                        confidence=round(min(0.9, 0.45 + conf * 0.5), 4),
                        severity=sp.severity,
                        metrics={
                            "value": sp.value,
                            "baseline_mean": round(sp.baseline_mean, 3),
                            "baseline_std": round(sp.baseline_std, 3),
                            "zscore": round(sp.zscore, 4),
                            "direction": sp.direction,
                            "coverage_confidence": conf,
                        },
                        caveats=caveats,
                    )
                )

        # --- 2. sentiment shift --------------------------------------------
        for iso, country_news in self._group_by_country(news).items():
            if len(country_news) < 6:
                continue
            ordered = sorted(
                country_news, key=lambda i: i.published_at or i.collected_at
            )
            values = [i.sentiment for i in ordered]
            half = max(3, len(values) // 2)
            recent, prior = values[-half:], values[:half]
            if len(prior) < 3:
                continue
            import numpy as np

            mean_prior = float(np.mean(prior))
            sd_prior = float(np.std(prior, ddof=1)) if len(prior) > 1 else 0.0
            mean_recent = float(np.mean(recent))
            if sd_prior < 1e-6:
                continue
            shift_z = (mean_recent - mean_prior) / (sd_prior / max(1e-6, len(recent) ** 0.5))
            if abs(shift_z) < 2.2:
                continue
            country = get_country(iso)
            conf = coverage_by_iso.get(iso, 0.3)
            signals.append(
                Signal(
                    id=f"sentiment-{iso}",
                    module="signals",
                    title=(
                        f"Narrative tone {'deteriorating' if shift_z < 0 else 'improving'} "
                        f"in {country.name if country else iso}"
                    ),
                    description=(
                        f"Mean lexicon tone moved from {mean_prior:+.3f} to "
                        f"{mean_recent:+.3f} across {len(recent)} recent items "
                        f"(shift z={shift_z:.2f})."
                    ),
                    signal_type="trend",
                    countries=[iso],
                    continent=country.continent if country else None,
                    score=round(abs(shift_z), 4),
                    confidence=round(min(0.85, 0.4 + conf * 0.45), 4),
                    severity=Severity.MEDIUM if abs(shift_z) > 3 else Severity.LOW,
                    metrics={
                        "mean_prior": round(mean_prior, 4),
                        "mean_recent": round(mean_recent, 4),
                        "shift_z": round(shift_z, 4),
                        "sample_size": len(values),
                    },
                    caveats=[
                        "Tone is measured by a transparent lexicon, not a trained "
                        "classifier; treat it as directional, not precise.",
                        "A change in the mix of outlets reporting can move this "
                        "figure without any change in the situation.",
                    ],
                )
            )

        # --- 3. ransomware-linked KEV cluster ------------------------------
        ransomware = [c for c in cyber if c.ransomware_used]
        if ransomware:
            vendors: dict[str, int] = {}
            for c in ransomware:
                if c.vendor:
                    vendors[c.vendor] = vendors.get(c.vendor, 0) + 1
            top_vendor = max(vendors.items(), key=lambda kv: kv[1]) if vendors else None
            if top_vendor and top_vendor[1] >= 3:
                signals.append(
                    Signal(
                        id=f"kev-ransomware-{top_vendor[0].lower().replace(' ', '-')}",
                        module="threats",
                        title=f"Repeat ransomware exploitation in {top_vendor[0]} products",
                        description=(
                            f"{top_vendor[1]} CVEs in {top_vendor[0]} products are flagged as "
                            "used in known ransomware campaigns."
                        ),
                        signal_type="anomaly",
                        score=float(top_vendor[1]),
                        confidence=0.9,
                        severity=Severity.HIGH,
                        metrics={
                            "vendor": top_vendor[0],
                            "kev_ransomware_count": top_vendor[1],
                            "total_ransomware_kev": len(ransomware),
                        },
                        caveats=[
                            "Counts reflect CISA KEV entries, which lag real-world "
                            "exploitation by the time it takes to confirm.",
                        ],
                    )
                )

        # --- 4. callsign-less aircraft concentration -----------------------
        if tracks:
            no_callsign = [t for t in tracks if "no_callsign" in t.quality_flags]
            if no_callsign and len(no_callsign) / len(tracks) > 0.25:
                signals.append(
                    Signal(
                        id="flight-no-callsign",
                        module="flights",
                        title="Elevated share of aircraft not broadcasting a callsign",
                        description=(
                            f"{len(no_callsign)} of {len(tracks)} observed aircraft "
                            f"({len(no_callsign) / len(tracks):.1%}) reported a position with "
                            "no callsign in the current watch boxes."
                        ),
                        signal_type="anomaly",
                        score=round(len(no_callsign) / len(tracks) * 10, 4),
                        confidence=0.45,
                        severity=Severity.LOW,
                        metrics={
                            "without_callsign": len(no_callsign),
                            "total": len(tracks),
                        },
                        caveats=[
                            "ADS-B is trivially spoofable and a silent transponder is "
                            "indistinguishable from a failed one. This is an observation "
                            "about transponder behaviour, not an attribution to any actor.",
                            "Military and state aircraft commonly fly without broadcasting "
                            "callsigns and are not thereby doing anything irregular.",
                        ],
                    )
                )

        signals.sort(key=lambda s: (-s.severity.rank, -s.score))
        return signals

    @staticmethod
    def _group_by_country(news: list[NewsItem]) -> dict[str, list[NewsItem]]:
        out: dict[str, list[NewsItem]] = {}
        for item in news:
            if item.duplicate_of:
                continue
            for iso in item.countries:
                out.setdefault(iso, []).append(item)
        return out

    def _build_graph(
        self,
        news: list[NewsItem],
        sanctions: list[SanctionItem],
        cyber: list[CyberItem],
    ) -> graphmod.RelationshipGraph:
        lookup = {c.iso2: c.name for c in all_countries()}
        graphs = [
            graphmod.build_news_graph(news, country_lookup=lookup, max_items=250),
            graphmod.build_sanctions_graph(sanctions, country_lookup=lookup, max_items=350),
            graphmod.build_cyber_graph(cyber, max_items=200),
        ]
        return graphmod.merge_graphs(*graphs)

    # -- accessors ---------------------------------------------------------

    def _records(self, name: str) -> list[Any]:
        ds = self.store.get(name)
        return list(ds.records) if ds else []

    def news(self) -> list[NewsItem]:
        return self._records(DATASET_NEWS)

    def events(self) -> list[EventItem]:
        return self._records(DATASET_EVENTS)

    def cyber(self) -> list[CyberItem]:
        return self._records(DATASET_CYBER)

    def sanctions(self) -> list[SanctionItem]:
        return self._records(DATASET_SANCTIONS)

    def tracks(self) -> list[TrackItem]:
        return self._records(DATASET_TRACKS)

    def coverage(self) -> list[CoverageCell]:
        return self._records(DATASET_COVERAGE)

    def signals(self) -> list[Signal]:
        return self._records(DATASET_SIGNALS)

    def graph(self) -> graphmod.RelationshipGraph | None:
        records = self._records(DATASET_GRAPH)
        return records[0] if records else None

    def country_stats(self) -> list[dict[str, Any]]:
        return self._records(DATASET_COUNTRY_STATS)

    def narrative(self) -> list[dict[str, Any]]:
        return narrative_keywords(self.news(), top_n=25)

    # -- backtest ----------------------------------------------------------

    def backtest(self, days: int = 30, detector: str = "spike") -> dict[str, Any]:
        events = self.events()
        news = self.news()
        coverage = self.coverage()

        ground_truth = bt.ground_truth_from_events(events)
        detections: list[bt.Detection] = []

        by_country: dict[str, list[EventItem]] = {}
        for ev in events:
            if ev.country_iso2:
                by_country.setdefault(ev.country_iso2, []).append(ev)
        for iso, country_events in by_country.items():
            series = stats.bucket_counts(country_events, "event_date", hours=days * 24, bucket_hours=6)
            spikes = stats.detect_spikes(
                series, window=10, threshold=1.0, key=iso, min_baseline_points=5
            )
            detections.extend(bt.detections_from_spikes(spikes, detector="event_spike"))

        grouped = self._group_by_country(news)
        for iso, country_news in grouped.items():
            series = stats.bucket_counts(
                country_news, "published_at", hours=days * 24, bucket_hours=12
            )
            values = [(ts, v) for ts, v in series]
            spikes = stats.detect_spikes(
                values, window=8, threshold=1.0, key=iso, min_baseline_points=4
            )
            detections.extend(bt.detections_from_spikes(spikes, detector="news_volume"))

        report = bt.run_detector(
            detector,
            lambda: detections,
            ground_truth,
            threshold=2.5,
            lead_window_hours=72.0,
        )
        payload = report.as_dict()
        payload["coverage_adjusted_recall"] = bt.coverage_adjusted_recall(
            report.result, coverage_index(coverage)
        )
        payload["ground_truth_events"] = len(ground_truth)
        payload["detections_evaluated"] = len([d for d in detections if d.score >= 2.5])
        payload["window_days"] = days
        payload["data_availability"] = {
            "events_total": len(events),
            "news_total": len(news),
            "insufficient_data": len(ground_truth) < 5,
        }
        if len(ground_truth) < 5:
            payload["caveats"] = [
                "Fewer than five qualifying ground-truth events are available. "
                "Precision and recall figures at this sample size are not "
                "statistically meaningful and are shown only to demonstrate the "
                "method.",
                *payload["caveats"],
            ]
        return payload
