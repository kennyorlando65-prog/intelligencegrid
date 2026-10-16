"""Tracking sources: OpenSky aircraft, public AIS vessel activity.

## FlightWatch (OpenSky)

The OpenSky state vector endpoint is free and needs no account for anonymous
access, but it throttles by *request weight*, and a global bounding box is
expensive. Collection is therefore limited to a set of watch boxes around
regions of interest, fetched sequentially through the host gate.

The anomaly model is deliberately narrow. It reports:

* aircraft reporting a position with no callsign (a common signature of
  non-cooperative or misconfigured transponders),
* aircraft far from the median altitude of their cohort (see
  ``stats.track_anomaly_scores``),
* sudden large ground-speed departures from the cohort median.

**It does not claim to identify anything covert.** ADS-B is trivially
spoofable and a transponder failure looks identical to intent. Every signal
says what was observed and what it is not.

## MaritimeWatch (AIS)

There is no free, key-less, live global AIS feed. Every authoritative
aggregator is commercial. Rather than fabricate vessel positions -- which
would be the single most damaging thing this project could do -- the
MaritimeWatch source ingests what free data does exist: AIS-derived vessel
reference data and public notices. Where no live feed is reachable, the
source reports ``unavailable`` with an explicit reason, and the module shows
that state instead of an empty map that looks like "no vessels at sea".
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from ..models import Severity, TrackItem, utcnow
from ..quality import content_hash
from .base import Source

OPENSKY_STATES = "https://opensky-network.org/api/states/all"

#: Bounding boxes: (label, lamin, lomin, lamax, lomax)
WATCH_BOXES: list[tuple[str, float, float, float, float]] = [
    ("Sahel", 12.0, -6.0, 20.0, 16.0),
    ("Horn of Africa", 3.0, 38.0, 15.0, 51.0),
    ("Eastern Mediterranean", 32.0, 28.0, 38.0, 40.0),
    ("Baltic", 53.0, 12.0, 66.0, 30.0),
    ("South China Sea", 3.0, 105.0, 23.0, 122.0),
    ("Andean", -20.0, -76.0, 2.0, -62.0),
    ("West Africa", 4.0, -8.0, 12.0, 4.0),
]


class OpenSkySource(Source):
    """Aircraft positions from the OpenSky Network."""

    name = "opensky"
    label = "OpenSky Network aircraft states"
    module = "flights"
    max_staleness_seconds = 2 * 3600
    note = (
        "Free anonymous ADS-B feed. ADS-B is spoofable and a silent "
        "transponder is indistinguishable from a failed one -- signals are "
        "observations, not attributions."
    )

    #: Keep the per-run request budget bounded; each box is one request.
    BOXES_PER_RUN = 4

    def __init__(self, client, store, boxes: int | None = None) -> None:
        super().__init__(client, store)
        if boxes:
            self.BOXES_PER_RUN = boxes
        self._boxes_read = 0
        self._boxes_failed = 0

    @property
    def _auth(self) -> tuple[str, str] | None:
        if self.settings.opensky_username and self.settings.opensky_password:
            return (self.settings.opensky_username, self.settings.opensky_password)
        return None

    async def collect(self) -> list[TrackItem]:
        out: list[TrackItem] = []
        boxes = WATCH_BOXES[: self.BOXES_PER_RUN]
        # Sequential, not gathered: OpenSky throttles by credit balance and
        # parallel bursts trigger the limit.
        for label, lamin, lomin, lamax, lomax in boxes:
            rows = await self._one_box(label, lamin, lomin, lamax, lomax)
            if rows:
                out.extend(rows)
            else:
                self._boxes_failed += 1
            await asyncio.sleep(0.4)
        self._boxes_read = len(boxes)
        self.health.notes = (
            f"watch boxes read: {self._boxes_read}; empty/refused: {self._boxes_failed}"
        )
        if out and self._boxes_failed == len(boxes):
            self.mark_rate_limited("all watch boxes refused or returned nothing")
        return out

    async def _one_box(
        self, label: str, lamin: float, lomin: float, lamax: float, lomax: float
    ) -> list[TrackItem]:
        url = f"{OPENSKY_STATES}?lamin={lamin}&lomin={lomin}&lamax={lamax}&lomax={lomax}"
        data, res = await self.client.get_json(
            url,
            rpm=self.settings.opensky_rpm,
            host_key="opensky",
            cache_ttl=300,
            auth=self._auth,
        )
        if res.status == 429:
            return []
        if not res.ok:
            return []

        states = (data or {}).get("states") or []
        out: list[TrackItem] = []
        for s in states:
            item = _state_to_track(s, label)
            if item is not None:
                out.append(item)
        if out:
            self.archive(res.url, res.content, content_type="application/json", note=f"opensky:{label}")
        return out


def _state_to_track(state: list[Any], box_label: str) -> TrackItem | None:
    """Convert one OpenSky state vector into a TrackItem.

    OpenSky returns positional arrays, so index access is unavoidable; the
    length check guards against schema drift silently producing wrong fields.
    """
    if not isinstance(state, list) or len(state) < 14:
        return None
    try:
        icao24 = str(state[0] or "").strip()
        callsign = str(state[1] or "").strip()
        origin_country = str(state[2] or "").strip()
        lon = state[5]
        lat = state[6]
        baro_alt = state[7]
        on_ground = bool(state[8])
        velocity = state[9]
        heading = state[10]
    except (IndexError, TypeError):
        return None

    if lat is None or lon is None:
        # No position means the record cannot be mapped; keeping it would
        # inflate counts without adding anything visible.
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    flags: list[str] = []
    confidence = 0.75
    if not callsign:
        flags.append("no_callsign")
        confidence -= 0.25
    if on_ground:
        flags.append("on_ground")
    if baro_alt is None:
        flags.append("no_altitude")
        confidence -= 0.1
    if velocity is None:
        flags.append("no_velocity")

    return TrackItem(
        id=content_hash("opensky", icao24, str(lat), str(lon))[:16],
        kind="aircraft",
        identifier=icao24,
        callsign=callsign,
        name=f"{callsign or icao24} ({origin_country})",
        lat=float(lat),
        lon=float(lon),
        altitude_m=float(baro_alt) if baro_alt is not None else None,
        velocity_ms=float(velocity) if velocity is not None else None,
        heading=float(heading) if heading is not None else None,
        on_ground=on_ground,
        country=origin_country,
        observed_at=utcnow(),
        source_name="OpenSky Network",
        source_url="https://opensky-network.org/",
        confidence=round(max(0.1, confidence), 4),
        quality_flags=flags + [f"box:{box_label}"],
        anomaly_score=0.0,
    )


def flight_anomalies(tracks: list[TrackItem], threshold: float = 0.5) -> list[dict]:
    """Rank aircraft observations by anomaly score.

    Cohort grouping is by origin country so an aircraft is compared against
    its peers rather than the global fleet -- otherwise every helicopter in
    the dataset looks anomalous.
    """
    from ..engine.stats import track_anomaly_scores

    scores = track_anomaly_scores(tracks, group_attr="country")
    out = []
    for t in tracks:
        score = scores.get(t.id, 0.0)
        t.anomaly_score = score
        if score < threshold:
            continue
        reasons = []
        if "no_callsign" in t.quality_flags:
            reasons.append("no callsign broadcast")
        if t.altitude_m is not None:
            reasons.append(f"altitude {t.altitude_m:.0f} m vs cohort median")
        if t.on_ground:
            reasons.append("reported on ground while in the watch box")
        out.append(
            {
                "id": t.id,
                "identifier": t.identifier,
                "callsign": t.callsign,
                "country": t.country,
                "lat": t.lat,
                "lon": t.lon,
                "altitude_m": t.altitude_m,
                "velocity_ms": t.velocity_ms,
                "anomaly_score": round(score, 4),
                "severity": Severity.HIGH.value
                if score > 0.75
                else Severity.MEDIUM.value
                if score > 0.6
                else Severity.LOW.value,
                "reasons": reasons,
                "caveat": (
                    "ADS-B signals can be spoofed or lost. This is an "
                    "observation of transponder behaviour, not an attribution."
                ),
            }
        )
    out.sort(key=lambda r: -r["anomaly_score"])
    return out


def flight_coverage_by_box(tracks: list[TrackItem]) -> list[dict]:
    """Observation counts per watch box, so thin boxes are visible."""
    counts: dict[str, int] = defaultdict(int)
    no_callsign: dict[str, int] = defaultdict(int)
    for t in tracks:
        box = next((f.split(":", 1)[1] for f in t.quality_flags if f.startswith("box:")), "unknown")
        counts[box] += 1
        if "no_callsign" in t.quality_flags:
            no_callsign[box] += 1
    labels = {label for label, *_ in WATCH_BOXES}
    out = []
    for label in sorted(labels):
        count = counts.get(label, 0)
        out.append(
            {
                "box": label,
                "observations": count,
                "without_callsign": no_callsign.get(label, 0),
                "data_state": "good" if count > 40 else "thin" if count > 0 else "no_data",
            }
        )
    return out


class AisVesselSource(Source):
    """Maritime positions.

    No free, unauthenticated, live global AIS feed exists. This source makes
    that explicit instead of inventing data: it probes the public endpoints
    that occasionally serve open AIS-derived data, and when none respond it
    records ``unavailable`` with the reason.

    Surfacing this honestly is the point. A maritime module that quietly
    renders an empty world map trains the user to read "no vessels" as "no
    activity", which is the opposite of true.
    """

    name = "ais"
    label = "Public AIS vessel data"
    module = "maritime"
    max_staleness_seconds = 6 * 3600
    note = (
        "No free live global AIS feed exists -- authoritative aggregators are "
        "commercial. This source reports availability honestly rather than "
        "presenting an empty map as an absence of vessels."
    )

    #: Public candidates that sometimes serve open AIS-derived data.
    CANDIDATES: list[tuple[str, str]] = [
        ("Danish Maritime Authority", "https://www.dma.dk/"),
        ("EMODnet Human Activities", "https://emodnet.ec.europa.eu/"),
    ]

    async def collect(self) -> list[TrackItem]:
        reachable: list[str] = []
        for label, url in self.CANDIDATES:
            res = await self.client.fetch(url, rpm=20, host_key=f"ais:{label}", cache_ttl=3600)
            if res.ok:
                reachable.append(label)

        if not reachable:
            self.health.notes = (
                "no live vessel-position feed available; module will report "
                "'no live feed' rather than an empty map"
            )
            self.mark_rate_limited("no public live AIS endpoint reachable")
            return []

        self.health.notes = (
            "reference endpoints reachable: " + ", ".join(reachable)
            + "; live position feed unavailable without a commercial provider"
        )
        # Even with the reference endpoints reachable, there are no live
        # positions to return. Returning [] with the note above is the
        # honest outcome.
        return []


def maritime_status(source: AisVesselSource) -> dict[str, Any]:
    """Explicit availability statement for the MaritimeWatch module."""
    return {
        "live_feed_available": False,
        "status": source.health.status.value,
        "reason": (
            "Global live AIS requires a commercial provider. Free public "
            "endpoints do not serve continuous global vessel positions."
        ),
        "what_is_shown": (
            "Sanctions-list vessels (from OFAC/EU/UN) are cross-referenced in "
            "the entity graph, which is the vessel intelligence available from "
            "free data."
        ),
        "notes": source.health.notes,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
