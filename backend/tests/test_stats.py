"""Statistical detection tests.

The look-ahead test is the one that matters. If the current point is included
in the mean and standard deviation it is being scored against, a large spike
inflates its own baseline and partially cancels itself out -- the bigger the
spike, the more it hides. A detector with that bug looks correct in a demo
and silently misses exactly the events it exists to find.
"""

from __future__ import annotations

import math

import pytest

from ig.engine.stats import (
    bucket_counts,
    burstiness,
    cluster_points,
    detect_spikes,
    rolling_mean_std,
    severity_for_z,
    trend_slope,
    zscores,
)
from ig.models import Severity, utcnow

from .conftest import NOW, make_event, make_spike_series


class TestRollingStatistics:
    def test_first_points_have_no_baseline(self):
        means, stds = rolling_mean_std([1.0, 2.0, 3.0, 4.0, 5.0], window=3)
        assert math.isnan(means[0])
        assert math.isnan(stds[0])

    def test_baseline_excludes_the_current_point(self):
        """The look-ahead guard, stated directly.

        At index 3 with window=3 and shift=1, the baseline must be exactly
        [1, 2, 3] -- not [2, 3, 4] and certainly not including index 3.
        """
        values = [1.0, 2.0, 3.0, 99.0, 5.0]
        means, _ = rolling_mean_std(values, window=3, shift=1)
        assert means[3] == pytest.approx(2.0)

    def test_spike_does_not_inflate_its_own_baseline(self):
        values = [5.0] * 10 + [500.0]
        means, stds = rolling_mean_std(values, window=5, shift=1)
        assert means[10] == pytest.approx(5.0)
        assert stds[10] == pytest.approx(0.0, abs=1e-9)

    def test_zscores_are_none_without_baseline(self):
        zs = zscores([1.0, 2.0, 3.0], window=5)
        assert zs[0] is None

    def test_flat_baseline_with_deviation_is_bounded_not_infinite(self):
        """Dividing by a zero std is how naive detectors alarm on quiet days."""
        zs = zscores([5.0] * 10 + [50.0], window=5)
        assert zs[10] == pytest.approx(8.0)

    def test_flat_baseline_with_no_deviation_is_zero(self):
        zs = zscores([5.0] * 12, window=5)
        assert zs[-1] == pytest.approx(0.0)


class TestSpikeDetection:
    def test_injected_spike_is_found(self):
        series = make_spike_series(n=40, baseline=5.0, spike_value=60.0, spike_index=35)
        spikes = detect_spikes(series, window=10, threshold=2.5, min_baseline_points=7)
        assert spikes, "a 12x outlier on a flat baseline must be detected"
        assert any(s.index == 35 for s in spikes)
        assert all(s.zscore > 0 for s in spikes)
        assert all(s.direction == "up" for s in spikes)

    def test_flat_series_produces_no_spikes(self):
        series = [(NOW, 5.0) for _ in range(40)]
        assert detect_spikes(series, window=10, threshold=2.5, min_baseline_points=7) == []

    def test_short_series_produces_no_spikes(self):
        """Cold start: without enough prior observations there is no baseline,
        so there is no anomaly -- only insufficient data."""
        series = [(NOW, 5.0), (NOW, 5.0), (NOW, 900.0)]
        assert detect_spikes(series, window=10, threshold=2.5, min_baseline_points=7) == []

    def test_threshold_is_respected(self):
        series = make_spike_series(n=40, baseline=5.0, spike_value=12.0, spike_index=35)
        high = detect_spikes(series, window=10, threshold=100.0, min_baseline_points=7)
        assert high == []

    def test_downward_movement_is_detected_with_direction(self):
        series = [(NOW, 50.0) for _ in range(20)]
        series += [(NOW, 50.0 + (0.5 if i % 2 else -0.5)) for i in range(15)]
        series.append((NOW, 0.0))
        spikes = detect_spikes(series, window=10, threshold=2.0, min_baseline_points=7)
        assert any(s.direction == "down" for s in spikes)

    def test_severity_ladder(self):
        assert severity_for_z(2.1) == Severity.LOW
        assert severity_for_z(3.2) == Severity.MEDIUM
        assert severity_for_z(4.2) == Severity.HIGH
        assert severity_for_z(6.0) == Severity.CRITICAL
        assert severity_for_z(0.1) == Severity.INFO


class TestSeriesHelpers:
    def test_bucket_counts_zero_fills_every_bucket(self):
        events = [make_event(i, days_ago=0.1) for i in range(3)]
        buckets = bucket_counts(events, "event_date", hours=24, bucket_hours=6)
        assert len(buckets) == 4
        assert sum(v for _, v in buckets) == 3
        assert any(v == 0 for _, v in buckets), "empty buckets must be present, not omitted"

    def test_bucket_counts_ignores_records_outside_window(self):
        events = [make_event(i, days_ago=90) for i in range(3)]
        buckets = bucket_counts(events, "event_date", hours=24, bucket_hours=6)
        assert sum(v for _, v in buckets) == 0

    def test_trend_slope_sign(self):
        assert trend_slope([1, 2, 3, 4, 5]) > 0
        assert trend_slope([5, 4, 3, 2, 1]) < 0
        assert trend_slope([3, 3, 3]) == pytest.approx(0.0)

    def test_burstiness_zero_for_uniform_series(self):
        assert burstiness([4.0, 4.0, 4.0, 4.0]) == pytest.approx(0.0)

    def test_burstiness_positive_for_episodic_series(self):
        assert burstiness([0.0, 0.0, 0.0, 20.0]) > 1.0


class TestGeoClustering:
    def test_nearby_points_cluster(self):
        coords = [(9.08, 8.68), (9.09, 8.69), (9.07, 8.67), (35.0, 51.0)]
        clusters = cluster_points(coords, eps_km=150.0, min_samples=2)
        assert clusters
        assert clusters[0].count == 3

    def test_isolated_point_is_not_a_cluster(self):
        coords = [(9.08, 8.68), (9.09, 8.69), (-33.9, 18.4)]
        clusters = cluster_points(coords, eps_km=150.0, min_samples=2)
        assert all(c.count == 2 for c in clusters)

    def test_cluster_reports_countries(self):
        coords = [(9.08, 8.68), (9.09, 8.69)]
        clusters = cluster_points(
            coords, eps_km=150.0, min_samples=2, country_ids=["NG", "NG"]
        )
        assert clusters[0].countries == ["NG"]

    def test_empty_input(self):
        assert cluster_points([], eps_km=100.0, min_samples=2) == []

    def test_cluster_payload_shape(self):
        coords = [(9.08, 8.68), (9.09, 8.69)]
        payload = cluster_points(coords, eps_km=150.0, min_samples=2)[0].as_dict()
        assert set(payload) == {"cluster_id", "count", "centroid", "radius_km", "countries"}


class TestClockSafety:
    def test_utcnow_is_timezone_aware(self):
        """Naive datetimes compared against aware ones raise at runtime."""
        assert utcnow().tzinfo is not None
