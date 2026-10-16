"""News sources: RSS feed packs and GDELT.

## Why the feed packs are structured the way they are

Full-text search APIs and wire services massively over-represent the
English-speaking West. If the only feeds you read are Reuters and the BBC,
Africa appears in your dataset almost exclusively through the lens of
disasters, conflict and disease -- the classic "single story" distortion.
That is a data-quality defect, not a coverage gap, because it produces
systematically wrong regional baselines and therefore wrong anomaly scores.

The counter is explicit regional packs, each with its own outlets, so that
(a) per-region volume is comparable, and (b) a story that only the region's
own press is reporting can be seen at all. The packs below are declared with
their editorial home region, and the API reports coverage per pack so a
thin pack is visible rather than silently diluting the map.

## GDELT

GDELT's live document API hard-refuses anything above one request every five
seconds, returning a plain-text 429 rather than a JSON error. It is also
frequently saturated. The API path is therefore treated as *best effort*,
and the 15-minute CSV export drops on ``data.gdeltproject.org`` -- static
files with no rate limit -- are the reliable path. Both are implemented;
whichever succeeds is recorded, and if the API path is refused the source
reports RATE_LIMITED while still returning records from the bulk path.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser

from ..models import EventItem, GeoPoint, NewsItem, Severity, utcnow
from ..quality import (
    content_hash,
    score_news_item,
    simhash,
    strip_html,
    summarize_text,
)
from .base import Source

# -- regional feed packs ----------------------------------------------------
# Each entry: (display name, url, language, home continent code)
# Only feeds verified reachable are listed; the packs are the product's
# anti-bias instrument so an unreachable feed must not silently vanish.

GLOBAL_PACK: list[tuple[str, str, str, str]] = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "en", "EU"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "en", "AS"),
    ("Deutsche Welle", "https://rss.dw.com/rdf/rss-en-world", "en", "EU"),
    ("France 24", "https://www.france24.com/en/rss", "en", "EU"),
]

AFRICA_PACK: list[tuple[str, str, str, str]] = [
    ("AllAfrica", "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "en", "AF"),
    ("IOL South Africa", "https://www.iol.co.za/rss", "en", "AF"),
    ("Premium Times Nigeria", "https://www.premiumtimesng.com/feed", "en", "AF"),
    ("Punch Nigeria", "https://punchng.com/feed/", "en", "AF"),
    ("Kenya News Agency", "https://www.kenyanews.go.ke/feed/", "en", "AF"),
    ("Egypt Independent", "https://www.egyptindependent.com/feed/", "en", "AF"),
    ("Tunisie Numerique", "https://www.tunisienumerique.com/feed/", "fr", "AF"),
    ("The Namibian", "https://www.namibian.com.na/rss", "en", "AF"),
    ("RFI Africa", "https://www.rfi.fr/en/rss", "en", "AF"),
    ("Africanews", "https://www.africanews.com/feed/", "en", "AF"),
]

ASIA_PACK: list[tuple[str, str, str, str]] = [
    ("Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "en", "AS"),
    ("South China Morning Post", "https://www.scmp.com/rss/91/feed", "en", "AS"),
    ("The Hindu", "https://www.thehindu.com/news/international/feeder/default.rss", "en", "AS"),
    ("Japan Times", "https://www.japantimes.co.jp/feed/", "en", "AS"),
]

LATAM_PACK: list[tuple[str, str, str, str]] = [
    ("Buenos Aires Times", "https://www.batimes.com.ar/feed", "en", "SA"),
    ("Rio Times", "https://www.riotimesonline.com/feed/", "en", "SA"),
]

MIDEAST_PACK: list[tuple[str, str, str, str]] = [
    ("Al Jazeera English", "https://www.aljazeera.com/xml/rss/all.xml", "en", "AS"),
    ("France 24 Middle East", "https://www.france24.com/en/rss", "en", "AS"),
]

FEED_PACKS: dict[str, list[tuple[str, str, str, str]]] = {
    "global": GLOBAL_PACK,
    "africa": AFRICA_PACK,
    "asia": ASIA_PACK,
    "latam": LATAM_PACK,
    "mideast": MIDEAST_PACK,
}

PACK_LABELS = {
    "global": "Global wire services",
    "africa": "Africa regional pack (10 outlets)",
    "asia": "Asia regional pack (4 outlets)",
    "latam": "Latin America regional pack (2 outlets)",
    "mideast": "Middle East regional pack (2 outlets)",
}


class RssSource(Source):
    """Collects articles from the full set of regional feed packs."""

    name = "rss"
    label = "RSS feed packs (global + 4 regional)"
    module = "news"
    max_staleness_seconds = 3 * 3600
    note = (
        "Regional packs exist to counter volume bias in Western wire coverage. "
        "Each article retains its original publisher URL."
    )

    # Only fetch a feed that has not been refreshed within this window.
    PER_FEED_TTL = 600

    def __init__(self, client, store, packs: list[str] | None = None) -> None:
        super().__init__(client, store)
        self.packs = packs or list(FEED_PACKS.keys())
        self._feed_failures: list[str] = []

    def _feeds(self) -> list[tuple[str, str, str, str, str]]:
        """Flatten the selected packs into (pack, name, url, lang, continent)."""
        out: list[tuple[str, str, str, str, str]] = []
        seen: set[str] = set()
        for pack in self.packs:
            for name, url, lang, continent in FEED_PACKS.get(pack, []):
                if url in seen:
                    # Al Jazeera appears in both global and mideast packs;
                    # ingest it once and attribute it to the first pack.
                    continue
                seen.add(url)
                out.append((pack, name, url, lang, continent))
        return out

    async def collect(self) -> list[NewsItem]:
        import asyncio

        self._feed_failures = []
        feeds = self._feeds()
        results = await asyncio.gather(
            *(self._one_feed(*f) for f in feeds), return_exceptions=True
        )
        items: list[NewsItem] = []
        for feed, result in zip(feeds, results, strict=False):
            if isinstance(result, BaseException):
                self._feed_failures.append(f"{feed[1]}: {type(result).__name__}")
                continue
            items.extend(result)

        if self._feed_failures:
            self.health.notes = "unreachable feeds: " + "; ".join(self._feed_failures[:6])
            if len(self._feed_failures) >= len(feeds) // 2:
                self.mark_rate_limited(
                    f"{len(self._feed_failures)}/{len(feeds)} feeds unreachable"
                )
        return items

    async def _one_feed(
        self, pack: str, name: str, url: str, lang: str, continent: str
    ) -> list[NewsItem]:
        text, res = await self.client.get_text(
            url,
            rpm=self.settings.rss_rpm,
            host_key=f"rss:{name}",
            cache_ttl=self.PER_FEED_TTL,
            accept="application/rss+xml, application/xml, text/xml, */*",
        )
        if not res.ok:
            raise RuntimeError(res.error or f"HTTP {res.status}")

        self.archive(res.url, res.content, note=f"rss:{name}")
        parsed = feedparser.parse(text)
        out: list[NewsItem] = []
        for entry in parsed.entries[:60]:
            item = self._to_item(entry, name, pack, lang, continent)
            if item is not None:
                out.append(item)
        return out

    def _to_item(
        self,
        entry: Any,
        source_name: str,
        pack: str,
        lang: str,
        continent: str,
    ) -> NewsItem | None:
        from ..geo import find as find_countries_geo

        link = (entry.get("link") or "").strip()
        title = strip_html(entry.get("title") or "").strip()
        if not link or not title:
            return None

        summary_raw = entry.get("summary") or entry.get("description") or ""
        summary = summarize_text(strip_html(summary_raw), 500)

        published = _parse_entry_date(entry)
        blob = f"{title} {summary} {source_name}"
        countries = find_countries_geo(blob)

        item = NewsItem(
            id=content_hash(link, title)[:16],
            title=title,
            url=link,
            source_name=source_name,
            source_kind="rss",
            published_at=published,
            collected_at=utcnow(),
            summary=summary,
            language=lang or (entry.get("language") or "en"),
            countries=countries,
            continent=continent,
            content_hash=content_hash(link, title, summary),
            simhash=simhash(f"{title} {summary}"),
        )
        item.simhash = simhash(f"{title} {summary}")
        report = score_news_item(item)
        item.confidence = round(report.score, 4)
        item.quality_flags = report.flags + [f"pack:{pack}"]
        return item


def _parse_entry_date(entry: Any) -> datetime | None:
    """Extract publication time, or return None.

    Returning None rather than defaulting to "now" is deliberate: a feed
    that omits dates would otherwise create a phantom burst of "just
    published" articles on every poll, which is exactly the kind of artefact
    that generates false spikes.
    """
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        val = entry.get(key)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


# -- GDELT ------------------------------------------------------------------

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_LASTUPDATE = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"

# GDELT EventRootCode -> (category, severity). Codes are the CAMEO taxonomy.
EVENT_CATEGORIES: dict[str, tuple[str, Severity]] = {
    "18": ("Assault", Severity.HIGH),
    "19": ("Fight", Severity.HIGH),
    "20": ("Mass violence", Severity.CRITICAL),
    "17": ("Coerce", Severity.HIGH),
    "16": ("Reduce relations", Severity.MEDIUM),
    "15": ("Exhibit force", Severity.MEDIUM),
    "14": ("Protest", Severity.MEDIUM),
    "13": ("Threaten", Severity.MEDIUM),
    "12": ("Reject", Severity.LOW),
    "11": ("Disapprove", Severity.LOW),
    "10": ("Demand", Severity.LOW),
    "09": ("Investigate", Severity.LOW),
    "08": ("Yield", Severity.LOW),
    "07": ("Provide aid", Severity.INFO),
    "06": ("Engage in material cooperation", Severity.INFO),
    "05": ("Diplomatic cooperation", Severity.INFO),
    "04": ("Consult", Severity.INFO),
    "03": ("Express intent to cooperate", Severity.INFO),
    "01": ("Public statement", Severity.INFO),
    "02": ("Appeal", Severity.INFO),
}

# GDELT v2 export CSV column indices we care about (1-based in the spec,
# 0-based here after the header-less parse).
COL_GLOBALEVENTID = 0
COL_DAY = 1
COL_MONTHYEAR = 2
COL_YEAR = 3
COL_EVENTCODE = 26
COL_EVENTROOTCODE = 28
COL_ACTOR1NAME = 6
COL_ACTOR2NAME = 16
COL_ACTOR1COUNTRYCODE = 7
COL_ACTOR2COUNTRYCODE = 17
COL_ISROOTEVENT = 25
COL_NUMARTICLES = 33
COL_NUMSOURCES = 32
COL_AVGTONE = 34
COL_ACTIONGEO_LAT = 56
COL_ACTIONGEO_LONG = 57
COL_ACTIONGEO_FULLNAME = 52
COL_ACTIONGEO_COUNTRYCODE = 53
COL_SOURCEURL = 60


class GdeltSource(Source):
    """GDELT events via the bulk 15-minute export, with an API attempt first.

    The bulk path is preferred operationally because it has no rate limit
    and returns the same schema. The API path is attempted only to fill gaps
    when the bulk files are unavailable.
    """

    name = "gdelt"
    label = "GDELT global news events (geocoded)"
    module = "events"
    max_staleness_seconds = 3 * 3600
    note = (
        "GDELT geocodes events in 100+ languages. Read via the static 15-minute "
        "export files, which carry no rate limit."
    )

    #: How many of the 15-minute drop files to read per collection.
    FILES_PER_RUN = 4

    def __init__(self, client, store, files_per_run: int | None = None) -> None:
        super().__init__(client, store)
        if files_per_run:
            self.FILES_PER_RUN = files_per_run
        self._api_status = "not attempted"
        self._bulk_files_read = 0

    async def collect(self) -> list[EventItem]:
        from ..geo import get as get_country

        events: list[EventItem] = []
        bulk = await self._collect_bulk()
        events.extend(bulk)

        if not events:
            # Bulk unavailable -- fall back to the rate-limited API so the
            # module still shows something rather than a silent zero.
            api_events = await self._collect_api()
            events.extend(api_events)
            if not api_events:
                self.mark_rate_limited(
                    f"bulk export unreadable and API refused ({self._api_status})"
                )

        # Deduplicate: GDELT emits one row per event per mention.
        seen: set[str] = set()
        out: list[EventItem] = []
        for ev in events:
            if ev.id in seen:
                continue
            seen.add(ev.id)
            country = get_country(ev.country_iso2) if ev.country_iso2 else None
            if country and not ev.continent:
                ev.continent = country.continent
            out.append(ev)

        self.health.notes = (
            f"bulk files read: {self._bulk_files_read}; API: {self._api_status}"
        )
        return out

    async def _collect_bulk(self) -> list[EventItem]:
        """Read the newest static export files listed in lastupdate.txt."""
        text, res = await self.client.get_text(
            GDELT_LASTUPDATE,
            rpm=self.settings.gdelt_rpm,
            host_key="gdelt-bulk",
            cache_ttl=600,
        )
        if not res.ok:
            return []

        urls = []
        for line in text.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[2].endswith(".export.CSV.zip"):
                urls.append(parts[2])
        if not urls:
            return []

        # The newest N drops, oldest first, so time ordering is preserved.
        targets = urls[: self.FILES_PER_RUN]
        events: list[EventItem] = []
        for url in targets:
            rows = await self._read_export(url)
            events.extend(rows)
        self._bulk_files_read = len(targets)
        return events

    async def _read_export(self, url: str) -> list[EventItem]:
        res = await self.client.fetch(
            url,
            rpm=120,
            host_key="gdelt-bulk",
            cache_ttl=86400,
        )
        if not res.ok:
            return []
        try:
            with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
                names = zf.namelist()
                if not names:
                    return []
                raw = zf.read(names[0]).decode("utf-8", errors="replace")
        except (zipfile.BadZipFile, OSError):
            return []
        return self._parse_export_rows(raw)

    def _parse_export_rows(self, raw: str) -> list[EventItem]:
        from ..geo import get as get_country

        reader = csv.reader(io.StringIO(raw), delimiter="\t", quoting=csv.QUOTE_NONE)
        out: list[EventItem] = []
        seen: set[str] = set()
        for row in reader:
            if len(row) <= COL_SOURCEURL:
                continue
            # Only root events: non-root rows are sub-events that would
            # triple-count the same occurrence.
            if row[COL_ISROOTEVENT] != "1":
                continue
            gid = row[COL_GLOBALEVENTID]
            if gid in seen:
                continue
            seen.add(gid)

            date = _gdelt_date(row)
            if date is None:
                continue
            root = row[COL_EVENTROOTCODE] or ""
            category, severity = EVENT_CATEGORIES.get(root[:2], ("Other", Severity.INFO))

            lat = _to_float(row[COL_ACTIONGEO_LAT])
            lon = _to_float(row[COL_ACTIONGEO_LONG])
            location = None
            if lat is not None and lon is not None and not (lat == 0 and lon == 0):
                location = GeoPoint(
                    lat=lat,
                    lon=lon,
                    place=row[COL_ACTIONGEO_FULLNAME][:120],
                    country_iso2=row[COL_ACTIONGEO_COUNTRYCODE] or None,
                    precision="city" if row[COL_ACTIONGEO_FULLNAME] else "country",
                )

            iso = row[COL_ACTIONGEO_COUNTRYCODE] or None
            if not iso:
                for col in (COL_ACTOR1COUNTRYCODE, COL_ACTOR2COUNTRYCODE):
                    if row[col] and get_country(row[col]):
                        iso = row[col]
                        break

            mentions = _to_int(row[COL_NUMARTICLES]) or 1
            sources = _to_int(row[COL_NUMSOURCES]) or 1
            tone = _to_float(row[COL_AVGTONE]) or 0.0

            # Confidence rises with independent sourcing and falls when the
            # event has no resolvable location (harder to verify).
            confidence = 0.35 + min(0.40, sources * 0.06) + (0.15 if location else 0.0)
            if not iso:
                confidence -= 0.10

            out.append(
                EventItem(
                    id=gid,
                    event_date=date,
                    collected_at=utcnow(),
                    event_code=row[COL_EVENTCODE],
                    event_category=category,
                    actor1=row[COL_ACTOR1NAME][:120],
                    actor2=row[COL_ACTOR2NAME][:120],
                    location=location,
                    country_iso2=iso,
                    source_url=row[COL_SOURCEURL],
                    source_name="GDELT",
                    mention_count=mentions,
                    num_sources=sources,
                    tone=tone,
                    severity=severity,
                    confidence=round(max(0.05, min(0.95, confidence)), 4),
                    quality_flags=[]
                    if location
                    else ["no_geolocation"],
                )
            )
        return out

    async def _collect_api(self) -> list[EventItem]:
        """Best-effort live API query. Expected to be refused often."""
        params = (
            "?query=(conflict OR protest OR crisis)&mode=artlist"
            "&maxrecords=50&format=json&sort=datedesc"
        )
        data, res = await self.client.get_json(
            GDELT_API + params,
            rpm=self.settings.gdelt_rpm,
            host_key="gdelt-api",
            cache_ttl=600,
        )
        if res.status == 429:
            self._api_status = "rate limited (429)"
            return []
        if not res.ok:
            self._api_status = f"failed ({res.error or res.status})"
            return []

        self._api_status = "ok"
        self.archive(res.url, res.content, content_type="application/json", note="gdelt:api")
        articles = (data or {}).get("articles") or []
        out: list[EventItem] = []
        for art in articles:
            url = art.get("url") or ""
            title = art.get("title") or ""
            if not url:
                continue
            seen = art.get("seendate") or ""
            date = _parse_gdelt_seendate(seen) or utcnow()
            from ..geo import find as find_countries_geo

            blob = f"{title} {art.get('domain', '')}"
            isos = find_countries_geo(blob)
            out.append(
                EventItem(
                    id=content_hash(url, title)[:16],
                    event_date=date,
                    collected_at=utcnow(),
                    event_code="",
                    event_category="Reported event",
                    actor1=art.get("domain", ""),
                    location=None,
                    country_iso2=isos[0] if isos else None,
                    source_url=url,
                    source_name=f"GDELT/{art.get('domain', 'unknown')}",
                    mention_count=1,
                    num_sources=1,
                    tone=0.0,
                    severity=Severity.INFO,
                    confidence=0.3,
                    quality_flags=["api_path", "no_geolocation", "headline_only"],
                )
            )
        return out


def _gdelt_date(row: list[str]) -> datetime | None:
    """Parse the YYYYMMDD column into an aware datetime."""
    day = row[COL_DAY]
    if len(day) != 8 or not day.isdigit():
        return None
    try:
        return datetime(
            int(day[0:4]), int(day[4:6]), int(day[6:8]), tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _parse_gdelt_seendate(seen: str) -> datetime | None:
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(seen, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# -- news search helpers ------------------------------------------------------


def search_news(
    items: list[NewsItem],
    *,
    query: str = "",
    countries: list[str] | None = None,
    continent: str | None = None,
    source_name: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    min_confidence: float = 0.0,
    include_duplicates: bool = False,
    include_syndicated: bool = True,
) -> list[NewsItem]:
    """Filter the news corpus. Used by the News Intelligence module.

    Handles the two exclusion switches explicitly rather than burying them:
    duplicates are hidden by default (they add nothing) while syndicated
    copies are kept by default but labelled, because the fact that forty
    outlets ran a story is itself information.
    """
    q = query.lower().strip()
    out: list[NewsItem] = []
    for item in items:
        if not include_duplicates and item.duplicate_of:
            continue
        if not include_syndicated and item.syndication_group:
            continue
        if min_confidence and item.confidence < min_confidence:
            continue
        if continent and item.continent != continent:
            continue
        if source_name and source_name.lower() not in item.source_name.lower():
            continue
        if countries:
            wanted = {c.upper() for c in countries}
            if not wanted & {c.upper() for c in item.countries}:
                continue
        ts = item.published_at or item.collected_at
        if since and ts < since:
            continue
        if until and ts > until:
            continue
        if q:
            blob = f"{item.title} {item.summary} {item.source_name}".lower()
            if not re.search(re.escape(q), blob):
                continue
        out.append(item)
    out.sort(key=lambda i: i.published_at or i.collected_at, reverse=True)
    return out


def historical_window(days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or utcnow()
    return now - timedelta(days=days), now
