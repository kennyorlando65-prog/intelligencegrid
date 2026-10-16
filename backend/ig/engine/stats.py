"""Statistical detection: spike detection, rolling z-scores, geo clustering.

The single most important detail in this module is the **look-ahead shift**.
A rolling z-score must compare the current point against a baseline built
from *prior* points only.

If the current value is included in its own mean and standard deviation, a
genuine spike inflates the baseline it is being measured against and
partially cancels itself out -- the bigger the spike, the more it hides.
That bug produces a detector which looks correct on paper and silently
misses exactly the events it was built to find.

Every rolling statistic here shifts the window by one so the baseline is
strictly causal.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import numpy as np

from ..models import Severity, utcnow

# -- rolling statistics -----------------------------------------------------


def rolling_mean_std(
    values: Sequence[float], window: int, shift: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """Causal rolling mean and std.

    ``shift=1`` excludes the current point from its own baseline. The first
    ``window`` points have no baseline and come back as ``nan`` -- callers
    must treat those as "not yet scoreable" rather than as zero.
    """
    arr = np.asarray(values, dtype=float)
    n = arr.size
    means = np.full(n, np.nan)
    stds = np.full(n, np.nan)
    if n == 0 or window < 2:
        return means, stds
    for i in range(n):
        lo = max(0, i - shift - window + 1)
        hi = i - shift + 1
        if hi - lo < 2:
            continue
        base = arr[lo:hi]
        means[i] = float(np.mean(base))
        stds[i] = float(np.std(base, ddof=1)) if base.size > 1 else 0.0
    return means, stds


def zscores(values: Sequence[float], window: int = 30, shift: int = 1) -> list[float | None]:
    """Causal z-score per point. ``None`` where no baseline exists yet.

    A zero-variance baseline yields ``None`` rather than infinity: dividing
    by a zero std is how naive detectors emit a "spike" on every quiet day.
    """
    means, stds = rolling_mean_std(values, window=window, shift=shift)
    out: list[float | None] = []
    for i, val in enumerate(values):
        mu, sd = means[i], stds[i]
        if math.isnan(mu) or math.isnan(sd):
            out.append(None)
            continue
        if sd < 1e-9:
            # Baseline is flat. Any deviation is notable, but we cannot
            # quantify it, so report it as a bounded strong signal.
            out.append(0.0 if abs(val - mu) < 1e-9 else (8.0 if val > mu else -8.0))
            continue
        out.append(float((val - mu) / sd))
    return out


@dataclass
class Spike:
    """One detected deviation from a causal baseline."""

    index: int
    timestamp: datetime | None
    value: float
    baseline_mean: float
    baseline_std: float
    zscore: float
    direction: str  # up | down
    severity: Severity
    key: str = ""

    @property
    def magnitude(self) -> float:
        return abs(self.zscore)


def severity_for_z(z: float, *, high: float = 4.0, medium: float = 3.0, low: float = 2.0) -> Severity:
    az = abs(z)
    if az >= high + 1.5:
        return Severity.CRITICAL
    if az >= high:
        return Severity.HIGH
    if az >= medium:
        return Severity.MEDIUM
    if az >= low:
        return Severity.LOW
    return Severity.INFO


def detect_spikes(
    series: Sequence[tuple[datetime, float]],
    *,
    window: int = 30,
    threshold: float = 2.5,
    min_baseline_points: int = 7,
    key: str = "",
) -> list[Spike]:
    """Find points whose causal z-score exceeds ``threshold``.

    ``min_baseline_points`` guards against the classic cold-start error of
    declaring a spike on day three of a series. Until there are enough prior
    observations to describe "normal", there is no anomaly -- only
    insufficient data, which is a different claim.
    """
    if len(series) < min_baseline_points + 1:
        return []
    values = [float(v) for _, v in series]
    zs = zscores(values, window=window, shift=1)
    means, stds = rolling_mean_std(values, window=window, shift=1)

    visible = 0
    out: list[Spike] = []
    for i, z in enumerate(zs):
        if z is None:
            continue
        visible += 1
        if visible < min_baseline_points:
            continue
        if abs(z) < threshold:
            continue
        mu = float(means[i])
        sd = float(stds[i])
        out.append(
            Spike(
                index=i,
                timestamp=series[i][0],
                value=values[i],
                baseline_mean=mu,
                baseline_std=sd,
                zscore=float(z),
                direction="up" if z > 0 else "down",
                severity=severity_for_z(float(z)),
                key=key,
            )
        )
    return out


def burstiness(series: Sequence[float]) -> float:
    """Coefficient of variation. High values mean spiky, episodic activity."""
    arr = np.asarray(series, dtype=float)
    if arr.size < 2:
        return 0.0
    mu = float(np.mean(arr))
    if abs(mu) < 1e-9:
        return 0.0
    return float(np.std(arr, ddof=1) / mu)


def trend_slope(series: Sequence[float]) -> float:
    """Least-squares slope per step. Used for direction-of-travel signals."""
    arr = np.asarray(series, dtype=float)
    if arr.size < 2:
        return 0.0
    x = np.arange(arr.size, dtype=float)
    denom = float(np.sum((x - x.mean()) ** 2))
    if denom < 1e-12:
        return 0.0
    return float(np.sum((x - x.mean()) * (arr - arr.mean())) / denom)


# -- geo clustering (DBSCAN by hand, no sklearn dependency) -----------------


@dataclass
class Cluster:
    """A spatial cluster of events."""

    cluster_id: int
    points: list[int]  # indices into the input
    centroid_lat: float
    centroid_lon: float
    radius_km: float
    count: int
    countries: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "count": self.count,
            "centroid": {"lat": round(self.centroid_lat, 4), "lon": round(self.centroid_lon, 4)},
            "radius_km": round(self.radius_km, 2),
            "countries": self.countries,
        }


def _approx_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Cheap equirectangular distance -- adequate for clustering radii < 500 km."""
    dlat = (lat2 - lat1) * 111.0
    dlon = (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.sqrt(dlat * dlat + dlon * dlon)


def cluster_points(
    coords: Sequence[tuple[float, float]],
    *,
    eps_km: float = 150.0,
    min_samples: int = 2,
    country_ids: Sequence[str] | None = None,
) -> list[Cluster]:
    """Density-based clustering (DBSCAN) implemented directly.

    Written out rather than imported so the package carries no scientific
    stack beyond numpy -- a deliberate choice, since the serverless bundle
    limit is a real constraint and scikit-learn is large.
    """
    n = len(coords)
    if n == 0:
        return []
    labels = [-1] * n  # -1 unvisited, 0 noise, >0 cluster
    cluster_id = 0

    for i in range(n):
        if labels[i] != -1:
            continue
        neighbours = _region_query(coords, i, eps_km)
        if len(neighbours) < min_samples:
            labels[i] = 0
            continue
        cluster_id += 1
        labels[i] = cluster_id
        seeds = [j for j in neighbours if j != i]
        k = 0
        while k < len(seeds):
            j = seeds[k]
            k += 1
            if labels[j] == 0:
                labels[j] = cluster_id
            if labels[j] != -1:
                continue
            labels[j] = cluster_id
            j_neighbours = _region_query(coords, j, eps_km)
            if len(j_neighbours) >= min_samples:
                seeds.extend(x for x in j_neighbours if labels[x] in (-1, 0))

    clusters: list[Cluster] = []
    for cid in range(1, cluster_id + 1):
        idxs = [i for i in range(n) if labels[i] == cid]
        if not idxs:
            continue
        lats = [coords[i][0] for i in idxs]
        lons = [coords[i][1] for i in idxs]
        clat = sum(lats) / len(lats)
        clon = sum(lons) / len(lons)
        radius = max((_approx_km(clat, clon, la, lo) for la, lo in zip(lats, lons, strict=False)), default=0.0)
        countries: list[str] = []
        if country_ids:
            for i in idxs:
                iso = country_ids[i] if i < len(country_ids) else None
                if iso and iso not in countries:
                    countries.append(iso)
        clusters.append(
            Cluster(
                cluster_id=cid,
                points=idxs,
                centroid_lat=clat,
                centroid_lon=clon,
                radius_km=radius,
                count=len(idxs),
                countries=countries,
            )
        )
    clusters.sort(key=lambda c: -c.count)
    return clusters


def _region_query(coords: Sequence[tuple[float, float]], i: int, eps_km: float) -> list[int]:
    lat, lon = coords[i]
    out = []
    for j, (la, lo) in enumerate(coords):
        if _approx_km(lat, lon, la, lo) <= eps_km:
            out.append(j)
    return out


# -- anomaly scoring for tracks --------------------------------------------


def track_anomaly_scores(
    tracks: Sequence[Any],
    *,
    group_attr: str = "country",
) -> dict[str, float]:
    """Score aircraft/vessel observations for unusual behaviour.

    Two signals, both cheap and explainable:

    * **Altitude deviation** for aircraft -- an aircraft far from the median
      altitude of its cohort is doing something unusual.
    * **Heading change** for the same identifier across observations --
      sustained turning without displacement suggests holding or loitering.

    Grouping by cohort matters: a helicopter at 300 m is normal, and scoring
    it against a global median would flag every helicopter ever recorded.
    """
    groups: dict[str, list[Any]] = defaultdict(list)
    for t in tracks:
        groups[getattr(t, group_attr, "") or "unknown"].append(t)

    scores: dict[str, float] = {}
    for _key, members in groups.items():
        alts = [m.altitude_m for m in members if getattr(m, "altitude_m", None) is not None]
        if len(alts) < 3:
            for m in members:
                scores[m.id] = 0.0
            continue
        arr = np.asarray(alts, dtype=float)
        mu = float(np.median(arr))
        mad = float(np.median(np.abs(arr - mu)))
        if mad < 1e-6:
            for m in members:
                scores[m.id] = 0.0
            continue
        for m in members:
            alt = getattr(m, "altitude_m", None)
            if alt is None:
                scores[m.id] = 0.0
                continue
            # 1.4826 scales MAD to a std-equivalent for normal data.
            robust_z = abs(alt - mu) / (1.4826 * mad)
            scores[m.id] = round(min(1.0, robust_z / 6.0), 4)

    # Ground-vehicle presence for aircraft is a weak but real anomaly cue.
    for m in tracks:
        if getattr(m, "kind", "") == "aircraft" and getattr(m, "on_ground", False):
            scores[m.id] = max(scores.get(m.id, 0.0), 0.35)
    return scores


# -- time-series assembly ---------------------------------------------------


def bucket_counts(
    records: Sequence[Any],
    date_attr: str,
    *,
    hours: int = 72,
    bucket_hours: int = 6,
    now: datetime | None = None,
) -> list[tuple[datetime, float]]:
    """Fixed-width zero-filled time buckets.

    Zero-filling is mandatory for spike detection: a missing bucket would
    otherwise be invisible, and a genuine quiet-then-flare pattern would look
    like steady moderate activity.
    """
    now = now or utcnow()
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours)
    n_buckets = max(1, int(hours / bucket_hours))
    buckets: dict[datetime, float] = {
        start + timedelta(hours=i * bucket_hours): 0.0 for i in range(n_buckets)
    }
    for rec in records:
        ts = getattr(rec, date_attr, None)
        if not isinstance(ts, datetime):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < start or ts > end:
            continue
        offset_h = int((ts - start).total_seconds() // (bucket_hours * 3600))
        bucket = start + timedelta(hours=offset_h * bucket_hours)
        if bucket in buckets:
            buckets[bucket] += 1.0
    return sorted(buckets.items())


def percentile(values: Sequence[float], p: float) -> float:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0
    return float(np.percentile(arr, p))
