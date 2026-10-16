"""Canonical record models shared by every source and module.

Design rules enforced by these models:

* ``published_at`` and ``collected_at`` are separate, always-populated
  fields. Historical analysis needs publication time; operability needs
  collection time. Collapsing them loses the ability to tell a genuinely
  old story from a freshly-discovered one.
* ``source_url`` is required on every news/event record. Nothing enters the
  system without a link back to the original publisher.
* ``confidence`` is a 0..1 float and is documented as a strength-of-evidence
  estimate, never a probability of truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    """Timezone-aware UTC now. Naive datetimes are a recurring source of bugs."""
    return datetime.now(timezone.utc)


class Layer(str, Enum):
    """Medallion architecture layer."""

    BRONZE = "bronze"  # raw, immutable, append-only
    SILVER = "silver"  # cleansed, deduplicated, conformed
    GOLD = "gold"  # aggregated, business-ready


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class SourceStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    STALE = "stale"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


class GeoPoint(BaseModel):
    """A geocoded location with an explicit precision marker."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    place: str = ""
    country_iso2: str | None = None
    precision: str = "unknown"  # exact | city | admin | country | unknown

    @field_validator("country_iso2")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class Evidence(BaseModel):
    """The proof attached to a signal.

    Every derived signal carries this so a consumer can always answer
    "why does the platform believe this?" without leaving the API.
    """

    source_url: str
    source_name: str
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=utcnow)
    excerpt: str = ""
    notes: str = ""

    @field_validator("published_at", "collected_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class NewsItem(BaseModel):
    """A single article from any news source."""

    id: str
    title: str
    url: str
    source_name: str
    source_kind: str = "rss"  # rss | gdelt
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=utcnow)
    summary: str = ""
    language: str = "en"
    countries: list[str] = Field(default_factory=list)
    continent: str | None = None
    topics: list[str] = Field(default_factory=list)
    sentiment: float = 0.0  # -1..1 lexicon polarity
    content_hash: str = ""
    simhash: int = 0
    duplicate_of: str | None = None
    syndication_group: str | None = None
    confidence: float = 0.5
    quality_flags: list[str] = Field(default_factory=list)

    @field_validator("published_at", "collected_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


class EventItem(BaseModel):
    """A geocoded event, typically from GDELT."""

    id: str
    event_date: datetime
    collected_at: datetime = Field(default_factory=utcnow)
    event_code: str = ""
    event_category: str = ""
    actor1: str = ""
    actor2: str = ""
    location: GeoPoint | None = None
    country_iso2: str | None = None
    continent: str | None = None
    source_url: str = ""
    source_name: str = "GDELT"
    mention_count: int = 1
    tone: float = 0.0
    num_sources: int = 1
    confidence: float = 0.5
    severity: Severity = Severity.INFO
    cluster_id: int | None = None
    is_spike: bool = False
    quality_flags: list[str] = Field(default_factory=list)

    @field_validator("event_date", "collected_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


class CyberItem(BaseModel):
    """A vulnerability: CISA KEV entry, NVD record, or both joined."""

    id: str
    cve_id: str
    title: str = ""
    description: str = ""
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=utcnow)
    cvss_score: float | None = None
    severity: Severity = Severity.INFO
    in_kev: bool = False
    kev_date_added: datetime | None = None
    kev_due_date: datetime | None = None
    ransomware_used: bool = False
    vendor: str = ""
    product: str = ""
    required_action: str = ""
    source_url: str = ""
    confidence: float = 0.8
    quality_flags: list[str] = Field(default_factory=list)


class SanctionItem(BaseModel):
    """A sanctioned entity from OFAC, EU, UN or UK lists."""

    id: str
    name: str
    entity_type: str = "unknown"  # individual | entity | vessel | aircraft | unknown
    list_source: str = ""  # OFAC-SDN | EU-FSD | UN-SC | UK-OFSI
    programs: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(default_factory=dict)
    addresses: list[str] = Field(default_factory=list)
    listed_at: datetime | None = None
    collected_at: datetime = Field(default_factory=utcnow)
    source_url: str = ""
    confidence: float = 0.9
    quality_flags: list[str] = Field(default_factory=list)


class TrackItem(BaseModel):
    """An aircraft or vessel observation."""

    id: str
    kind: str  # aircraft | vessel
    identifier: str  # icao24 | mmsi
    callsign: str = ""
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    altitude_m: float | None = None
    velocity_ms: float | None = None
    heading: float | None = None
    on_ground: bool = False
    country: str = ""
    observed_at: datetime = Field(default_factory=utcnow)
    collected_at: datetime = Field(default_factory=utcnow)
    source_name: str = ""
    source_url: str = ""
    confidence: float = 0.7
    anomaly_score: float = 0.0
    quality_flags: list[str] = Field(default_factory=list)


class Signal(BaseModel):
    """A derived analytic finding, always evidence-backed."""

    id: str
    module: str
    title: str
    description: str
    signal_type: str  # spike | anomaly | trend | relationship | escalation
    detected_at: datetime = Field(default_factory=utcnow)
    window_start: datetime | None = None
    window_end: datetime | None = None
    countries: list[str] = Field(default_factory=list)
    continent: str | None = None
    score: float = 0.0
    confidence: float = 0.5
    severity: Severity = Severity.INFO
    evidence: list[Evidence] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)


class SourceHealth(BaseModel):
    """Operational state of one upstream source.

    Surfaced in the UI so a quiet map is never mistaken for a quiet world.
    """

    name: str
    status: SourceStatus
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    records_last_run: int = 0
    consecutive_failures: int = 0
    latency_ms: int | None = None
    staleness_seconds: int | None = None
    error: str = ""
    notes: str = ""

    @property
    def is_usable(self) -> bool:
        return self.status in (SourceStatus.OK, SourceStatus.DEGRADED)


class CoverageCell(BaseModel):
    """Per-country coverage confidence.

    Answers a subtle but important question: is a country quiet because
    nothing happened, or because we have almost no feeds there? The
    ``confidence`` field is what stops the platform implying the former
    when the truth is the latter.
    """

    country_iso2: str
    country_name: str
    continent: str
    continent_name: str
    event_count: int = 0
    news_count: int = 0
    distinct_sources: int = 0
    population_millions: float = 0.0
    coverage_confidence: float = 0.0
    data_state: str = "unknown"  # good | thin | low_data | no_data
    explanation: str = ""
    last_seen_at: datetime | None = None
