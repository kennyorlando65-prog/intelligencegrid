"""Backtesting and signal evaluation.

Without this module the platform's signals are unfalsifiable assertions.
With it, every detector can be asked the only questions that matter:

* When a signal fired, did something actually happen?
* How early did it fire relative to the event?
* How often did it fire with nothing behind it (false positive rate)?
* How many real events did it miss (recall)?

**A note on the ground truth used here.** This is retrospective
self-evaluation against the platform's own collected record, not a
peer-reviewed validation. If a detector is scored against events that were
themselves derived from the same feed, precision is inflated. The
``caveats`` field says so explicitly, and the UI repeats it. Presenting this
as rigorous validation would be the dishonest part -- the numbers are still
useful for *relative* comparison between detectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from ..models import Severity, utcnow


@dataclass
class Detection:
    """One signal firing, at a known time, about a known subject."""

    key: str  # country iso2, entity id, etc.
    fired_at: datetime
    score: float
    severity: Severity = Severity.INFO
    detector: str = ""
    detail: str = ""


@dataclass
class GroundTruthEvent:
    """A known real-world event to evaluate against."""

    key: str
    occurred_at: datetime
    label: str = ""
    magnitude: float = 1.0


@dataclass
class MatchResult:
    matched: list[tuple[Detection, GroundTruthEvent, float]] = field(default_factory=list)
    false_positives: list[Detection] = field(default_factory=list)
    missed: list[GroundTruthEvent] = field(default_factory=list)

    @property
    def true_positives(self) -> int:
        return len(self.matched)

    @property
    def precision(self) -> float:
        denom = self.true_positives + len(self.false_positives)
        return self.true_positives / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + len(self.missed)
        return self.true_positives / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return (2 * p * r / (p + r)) if (p + r) else 0.0

    @property
    def mean_lead_time_hours(self) -> float:
        if not self.matched:
            return 0.0
        leads = [lt for _, _, lt in self.matched]
        return sum(leads) / len(leads)

    @property
    def earliest_lead_time_hours(self) -> float:
        if not self.matched:
            return 0.0
        return max(lt for _, _, lt in self.matched)

    def as_dict(self, *, caveats: list[str] | None = None) -> dict[str, Any]:
        return {
            "true_positives": self.true_positives,
            "false_positives": len(self.false_positives),
            "missed": len(self.missed),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "mean_lead_time_hours": round(self.mean_lead_time_hours, 2),
            "earliest_lead_time_hours": round(self.earliest_lead_time_hours, 2),
            "false_positive_rate": round(
                1.0 - self.precision if (self.true_positives + len(self.false_positives)) else 0.0,
                4,
            ),
            "matches": [
                {
                    "key": d.key,
                    "detector": d.detector,
                    "fired_at": d.fired_at.isoformat(),
                    "event": ev.label or ev.key,
                    "occurred_at": ev.occurred_at.isoformat(),
                    "lead_time_hours": round(lt, 2),
                    "detection_score": round(d.score, 4),
                }
                for d, ev, lt in self.matched[:50]
            ],
            "false_positive_detail": [
                {
                    "key": d.key,
                    "detector": d.detector,
                    "fired_at": d.fired_at.isoformat(),
                    "score": round(d.score, 4),
                    "detail": d.detail,
                }
                for d in self.false_positives[:50]
            ],
            "missed_detail": [
                {"key": ev.key, "label": ev.label, "occurred_at": ev.occurred_at.isoformat()}
                for ev in self.missed[:50]
            ],
            "caveats": caveats or DEFAULT_CAVEATS,
        }


DEFAULT_CAVEATS = [
    "Evaluated retrospectively against this platform's own collected record, not an "
    "independently labelled dataset.",
    "Because ground-truth events may derive from the same feeds the detectors read, "
    "precision figures are optimistic and should be read as relative comparisons "
    "between detectors rather than absolute accuracy.",
    "Lead time is measured from detection to the earliest recorded instance of the "
    "event, and is limited by the collection interval.",
    "Countries with thin feed coverage will show systematically lower recall for "
    "reasons unrelated to detector quality -- cross-check with coverage confidence.",
]


def evaluate(
    detections: Sequence[Detection],
    events: Sequence[GroundTruthEvent],
    *,
    lead_window_hours: float = 72.0,
    late_tolerance_hours: float = 6.0,
) -> MatchResult:
    """Greedy nearest-in-time matching, detection-side first.

    Matching rules, stated because they drive the numbers:

    * A detection matches an event on the same key when the detection fires
      at most ``lead_window_hours`` before the event, or up to
      ``late_tolerance_hours`` after it (a detector that fires slightly late
      is still detecting the event, not producing a false positive).
    * Each event matches at most one detection -- the closest in time. A
      detector firing five times around one real event has one true positive
      and four false positives, which is the honest accounting.
    * Each detection matches at most one event.
    """
    matched: list[tuple[Detection, GroundTruthEvent, float]] = []
    used_events: set[int] = set()
    false_positives: list[Detection] = []

    for det in sorted(detections, key=lambda d: d.fired_at):
        best_idx = None
        best_delta = None
        for idx, ev in enumerate(events):
            if idx in used_events or ev.key != det.key:
                continue
            delta_h = (ev.occurred_at - det.fired_at).total_seconds() / 3600.0
            if delta_h > lead_window_hours or delta_h < -late_tolerance_hours:
                continue
            distance = abs(delta_h)
            if best_delta is None or distance < best_delta:
                best_delta = distance
                best_idx = idx
        if best_idx is None:
            false_positives.append(det)
            continue
        ev = events[best_idx]
        lead = (ev.occurred_at - det.fired_at).total_seconds() / 3600.0
        matched.append((det, ev, lead))
        used_events.add(best_idx)

    missed = [ev for idx, ev in enumerate(events) if idx not in used_events]
    return MatchResult(matched=matched, false_positives=false_positives, missed=missed)


def detections_from_spikes(
    spikes: Sequence[Any], *, detector: str = "spike", key_attr: str = "key"
) -> list[Detection]:
    """Adapt spike objects into detections for evaluation."""
    out = []
    for sp in spikes:
        ts = getattr(sp, "timestamp", None)
        if ts is None:
            continue
        out.append(
            Detection(
                key=getattr(sp, key_attr, "") or "",
                fired_at=ts,
                score=float(getattr(sp, "zscore", 0.0)),
                severity=getattr(sp, "severity", Severity.INFO),
                detector=detector,
                detail=f"z={getattr(sp, 'zscore', 0.0):.2f} value={getattr(sp, 'value', 0)}",
            )
        )
    return out


def ground_truth_from_events(
    events: Sequence[Any], key_attr: str = "country_iso2", date_attr: str = "event_date"
) -> list[GroundTruthEvent]:
    """Build ground truth from recorded events.

    Only events at or above MEDIUM severity become ground truth. Using every
    recorded event would make recall look excellent while measuring nothing:
    the baseline tick of the feed is not a thing that happened.
    """
    out: list[GroundTruthEvent] = []
    for ev in events:
        sev = getattr(ev, "severity", Severity.INFO)
        if getattr(sev, "rank", 0) < Severity.MEDIUM.rank:
            continue
        ts = getattr(ev, date_attr, None)
        key = getattr(ev, key_attr, None)
        if ts is None or not key:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append(
            GroundTruthEvent(
                key=str(key),
                occurred_at=ts,
                label=f"{getattr(ev, 'event_category', '')} in {key}",
                magnitude=float(getattr(ev, "mention_count", 1)),
            )
        )
    return out


@dataclass
class DetectorReport:
    """Result for one detector configuration, plus a threshold sweep."""

    detector: str
    threshold: float
    result: MatchResult
    sweep: list[dict[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = self.result.as_dict()
        payload.update(
            {
                "detector": self.detector,
                "threshold": self.threshold,
                "threshold_sweep": self.sweep,
            }
        )
        return payload


def sweep_thresholds(
    detections: Sequence[Detection],
    events: Sequence[GroundTruthEvent],
    *,
    thresholds: Sequence[float] = (1.5, 2.0, 2.5, 3.0, 3.5, 4.0),
    lead_window_hours: float = 72.0,
) -> list[dict[str, float]]:
    """Evaluate precision/recall across a range of alert thresholds.

    This is the most actionable output of the module: it shows the operating
    point. A user who cannot tolerate false positives picks a high threshold;
    one who cannot tolerate misses picks a low one. There is no single
    correct number and pretending otherwise is what makes alerting systems
    distrusted.
    """
    out: list[dict[str, float]] = []
    for t in thresholds:
        subset = [d for d in detections if d.score >= t]
        res = evaluate(subset, events, lead_window_hours=lead_window_hours)
        out.append(
            {
                "threshold": float(t),
                "detections": len(subset),
                "precision": round(res.precision, 4),
                "recall": round(res.recall, 4),
                "f1": round(res.f1, 4),
                "mean_lead_time_hours": round(res.mean_lead_time_hours, 2),
            }
        )
    return out


def run_detector(
    name: str,
    detections_fn: Callable[[], Sequence[Detection]],
    events: Sequence[GroundTruthEvent],
    *,
    threshold: float = 2.5,
    lead_window_hours: float = 72.0,
) -> DetectorReport:
    """Evaluate one named detector at a threshold, with a full sweep."""
    try:
        raw = list(detections_fn())
    except Exception:
        # A detector that raises must not take down the whole report.
        raw = []
    subset = [d for d in raw if d.score >= threshold]
    result = evaluate(subset, events, lead_window_hours=lead_window_hours)
    sweep = sweep_thresholds(raw, events, lead_window_hours=lead_window_hours)
    return DetectorReport(detector=name, threshold=threshold, result=result, sweep=sweep)


def coverage_adjusted_recall(result: MatchResult, coverage_by_key: dict[str, float]) -> float:
    """Recall weighted by how well-covered each key actually is.

    A missed event in a country with 0.1 coverage confidence is not evidence
    of a bad detector -- there was almost nothing to detect. This separates
    "the detector failed" from "we had no data", which is the distinction the
    whole coverage-confidence apparatus exists to make.
    """
    if not result.missed:
        return 1.0
    weights = [coverage_by_key.get(ev.key, 0.5) for ev in result.missed]
    if not weights:
        return 1.0
    mean_cov = sum(weights) / len(weights)
    # Blends raw recall with the mean coverage of missed events: high recall
    # stands on its own, low recall in thin-coverage regions is discounted.
    return round(min(1.0, result.recall + (1.0 - result.recall) * (1.0 - mean_cov)), 4)


def backtest_window(days: int = 30, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Standard evaluation window, ending now."""
    now = now or utcnow()
    return now - timedelta(days=days), now
