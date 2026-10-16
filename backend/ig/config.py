"""Runtime configuration.

Every knob here has a safe default so the platform runs with zero
environment variables -- a hard requirement, since all upstream data sources
are free and key-less.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable settings snapshot, built once per process."""

    # --- Collection windows -------------------------------------------------
    news_lookback_hours: int = 72
    refresh_ttl_seconds: int = 900

    # --- Network behaviour --------------------------------------------------
    http_timeout_seconds: float = 25.0
    http_max_retries: int = 3
    user_agent: str = (
        "IntelligenceGrid/1.0 (+https://github.com/kennyorlando65-prog/intelligencegrid)"
    )

    # --- Per-host politeness -----------------------------------------------
    # GDELT's live API hard-refuses more than one request every 5 seconds;
    # 12 rpm keeps us comfortably under that while still refreshing.
    gdelt_rpm: int = 12
    opensky_rpm: int = 20
    nvd_rpm: int = 10
    rss_rpm: int = 90

    # --- Optional OpenSky credentials (anonymous works fine) --------------
    opensky_username: str = ""
    opensky_password: str = ""

    # --- Source toggles -----------------------------------------------------
    enabled_sources: tuple[str, ...] = field(default_factory=tuple)

    @property
    def degraded_mode(self) -> bool:
        """True when at least one source is administratively disabled."""
        return bool(self.enabled_sources)

    def source_enabled(self, name: str) -> bool:
        if not self.enabled_sources:
            return True
        return name in self.enabled_sources


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings once and cache them for the process lifetime."""
    enabled = tuple(
        s.strip().lower() for s in os.environ.get("IG_ENABLED_SOURCES", "").split(",") if s.strip()
    )
    return Settings(
        news_lookback_hours=_int("IG_NEWS_LOOKBACK_HOURS", 72),
        refresh_ttl_seconds=_int("IG_REFRESH_TTL_SECONDS", 900),
        http_timeout_seconds=_float("IG_HTTP_TIMEOUT_SECONDS", 25.0),
        http_max_retries=_int("IG_HTTP_MAX_RETRIES", 3),
        gdelt_rpm=_int("IG_GDELT_RPM", 12),
        opensky_rpm=_int("IG_OPENSKY_RPM", 20),
        nvd_rpm=_int("IG_NVD_RPM", 10),
        rss_rpm=_int("IG_RSS_RPM", 90),
        opensky_username=os.environ.get("OPENSKY_USERNAME", "").strip(),
        opensky_password=os.environ.get("OPENSKY_PASSWORD", "").strip(),
        enabled_sources=enabled,
    )
