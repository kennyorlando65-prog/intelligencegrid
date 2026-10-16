"""Shared test fixtures.

A fixed "now" is injected everywhere rather than calling the real clock, so
assertions about freshness, recency and spike windows are deterministic. A
suite that depends on wall-clock time passes locally and fails in CI at
midnight, which is the least useful kind of failure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ig.models import CyberItem, EventItem, GeoPoint, NewsItem, Severity

#: Frozen reference instant. Every fixture is positioned relative to this.
NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return NOW


def make_news(
    index: int,
    *,
    title: str | None = None,
    summary: str | None = None,
    url: str | None = None,
    source_name: str = "Test Wire",
    countries: list[str] | None = None,
    hours_ago: float = 2.0,
    published: bool = True,
    sentiment: float = 0.0,
    pack: str | None = None,
) -> NewsItem:
    """Build one news item, positioned ``hours_ago`` before the frozen now."""
    stamp = NOW - timedelta(hours=hours_ago)
    item = NewsItem(
        id=f"news-{index:04d}",
        title=title or f"Headline number {index}",
        url=url or f"https://example.test/story/{index}",
        source_name=source_name,
        published_at=stamp if published else None,
        collected_at=NOW,
        summary=summary or f"Summary text for story {index}. " * 4,
        countries=countries or [],
        sentiment=sentiment,
    )
    if pack:
        item.quality_flags.append(f"pack:{pack}")
    return item


def make_event(
    index: int,
    *,
    country: str = "NG",
    category: str = "Protest",
    severity: Severity = Severity.MEDIUM,
    days_ago: float = 1.0,
    with_location: bool = True,
) -> EventItem:
    stamp = NOW - timedelta(days=days_ago)
    location = (
        GeoPoint(lat=9.08 + index * 0.01, lon=8.68 + index * 0.01, place="Test City", precision="city")
        if with_location
        else None
    )
    return EventItem(
        id=f"event-{index:04d}",
        event_date=stamp,
        collected_at=NOW,
        event_code=f"14{index:03d}",
        event_category=category,
        actor1="Actor A",
        actor2="Actor B",
        location=location,
        country_iso2=country,
        source_url=f"https://example.test/event/{index}",
        mention_count=1 + (index % 5),
        num_sources=1 + (index % 3),
        severity=severity,
    )


def make_cve(
    index: int,
    *,
    in_kev: bool = False,
    ransomware: bool = False,
    cvss: float | None = 7.5,
    vendor: str = "Acme",
    days_ago: float = 5.0,
) -> CyberItem:
    stamp = NOW - timedelta(days=days_ago)
    return CyberItem(
        id=f"cve-{index:04d}",
        cve_id=f"CVE-2026-{1000 + index}",
        title=f"Vulnerability {index}",
        description=f"A vulnerability affecting {vendor} products.",
        published_at=stamp,
        collected_at=NOW,
        cvss_score=cvss,
        in_kev=in_kev,
        kev_date_added=stamp if in_kev else None,
        kev_due_date=(stamp + timedelta(days=21)) if in_kev else None,
        ransomware_used=ransomware,
        vendor=vendor,
        product=f"Product {index % 3}",
        source_url=f"https://nvd.nist.gov/vuln/detail/CVE-2026-{1000 + index}",
    )


def make_spike_series(
    n: int = 40,
    *,
    baseline: float = 5.0,
    spike_value: float = 60.0,
    spike_index: int = 35,
    jitter: float = 0.4,
):
    """A flat series with a single injected outlier near the end."""
    series = []
    for i in range(n):
        ts = NOW - timedelta(hours=(n - i) * 6)
        if i == spike_index:
            value = spike_value
        else:
            value = baseline + (jitter if i % 2 else -jitter)
        series.append((ts, value))
    return series


@pytest.fixture
def frozen_now(monkeypatch) -> datetime:
    """Patch ``utcnow`` in the modules that read the clock at import time."""
    import ig.models as models

    monkeypatch.setattr(models, "utcnow", lambda: NOW)
    return NOW
