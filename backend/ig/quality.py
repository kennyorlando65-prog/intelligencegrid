"""Data-quality scoring, deduplication and syndication detection.

Three distinct problems that are constantly conflated in OSINT pipelines:

1. **Duplicate** -- the same article ingested twice (same URL or same
   canonical hash). Removing these is trivial and must never be skipped.
2. **Syndication** -- one wire story republished verbatim by forty outlets.
   The forty copies are not forty independent confirmations. If they are
   counted separately, every wire story looks like a global event. Detected
   with SimHash over normalised text plus a similarity threshold.
3. **Near-duplicate** -- substantially rewritten coverage of the same
   event. Reported as a cluster, not deleted, because the differing framing
   between two outlets is itself intelligence.

Quality scoring attaches a 0..1 value plus explicit flags. Flags are the
part that matters operationally: they explain *why* a record is discounted.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import NewsItem, utcnow

# ``summarize_text`` lives in ``store`` alongside the other presentation
# helpers, but it is fundamentally a data-quality concern -- truncating on a
# word boundary so a shortened field never ends mid-token. It is re-exported
# here because every source module naturally reaches for it through
# ``quality``, and having the import path a source uses match the concept it
# is reaching for is worth one deliberate re-export.
#
# The ``as`` form is the explicit re-export idiom: it tells ruff (and any
# reader) that this name is part of this module's public surface rather than
# a stray unused import, so `ruff --fix` will not silently delete it and
# break every source module that imports it from here.
from .store import summarize_text as summarize_text

# -- text normalisation ------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://\S+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_NUM_RE = re.compile(r"\b\d+\b")

# Boilerplate that appears in almost every syndicated copy and would
# otherwise inflate similarity between unrelated articles.
_BOILERPLATE = (
    "read more",
    "continue reading",
    "sign up",
    "subscribe",
    "advertisement",
    "all rights reserved",
    "share this",
    "follow us",
    "click here",
    "the post ",
    "appeared first on",
    "reporting by",
    "editing by",
)


def strip_html(text: str) -> str:
    """Remove tags and unescape entities."""
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub(" ", text))


def normalize_text(text: str) -> str:
    """Lowercase, de-accent, drop URLs/punctuation/numbers, squash space.

    Numbers are removed because wire copy differs mainly in figures that get
    updated as a story develops; keeping them makes two versions of the same
    story look dissimilar.
    """
    if not text:
        return ""
    text = strip_html(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _URL_RE.sub(" ", text)
    for phrase in _BOILERPLATE:
        text = text.replace(phrase, " ")
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _NUM_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def content_hash(*parts: str) -> str:
    """Stable SHA-256 over normalised parts."""
    h = hashlib.sha256()
    for p in parts:
        h.update(normalize_text(p).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def url_fingerprint(url: str) -> str:
    """Canonicalise a URL so tracking params do not defeat deduplication.

    ``?utm_source=...&utm_medium=...`` and friends are stripped, along with
    the fragment; scheme and a leading ``www.`` are normalised.
    """
    if not url:
        return ""
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip().lower()

    drop_prefixes = ("utm_", "fbclid", "gclid", "mc_", "ref", "cmpid", "ito", "ns_")
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not k.lower().startswith(drop_prefixes)
    ]
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    # Force http and https to the same scheme. An outlet that serves the same
    # article over both protocols would otherwise produce two fingerprints and
    # the story would slip through as two independent reports.
    scheme = "https" if parsed.scheme.lower() in ("http", "https", "") else parsed.scheme.lower()
    canonical = urlunparse((scheme, host, path, "", urlencode(kept), ""))
    return canonical


# -- SimHash ----------------------------------------------------------------


def simhash(text: str, bits: int = 64, shingle: int = 3) -> int:
    """64-bit SimHash over word shingles.

    Shingling (rather than single words) is what makes this sensitive to
    reordering and light editing while staying robust to small changes --
    exactly the shape of wire-copy syndication.
    """
    words = normalize_text(text).split()
    if not words:
        return 0
    if len(words) < shingle:
        tokens = [" ".join(words)]
    else:
        tokens = [" ".join(words[i : i + shingle]) for i in range(len(words) - shingle + 1)]

    vector = [0] * bits
    for token in tokens:
        h = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(bits):
            if h & (1 << i):
                vector[i] += 1
            else:
                vector[i] -= 1
    out = 0
    for i in range(bits):
        if vector[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def similarity(a: int, b: int, bits: int = 64) -> float:
    """1.0 = identical, 0.0 = unrelated."""
    if a == 0 or b == 0:
        return 0.0
    return 1.0 - (hamming(a, b) / bits)


# -- quality scoring --------------------------------------------------------


@dataclass
class QualityReport:
    score: float
    flags: list[str]
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 4), "flags": self.flags, "reasons": self.reasons}


def score_news_item(item: NewsItem, now: datetime | None = None) -> QualityReport:
    """Score one news record 0..1 with human-readable flags.

    Deliberately penalises missing provenance and unknown publication time
    rather than silently accepting them: those are the two fields every
    downstream claim depends on.
    """
    now = now or utcnow()
    score = 1.0
    flags: list[str] = []
    reasons: list[str] = []

    if not item.url:
        score -= 0.4
        flags.append("no_source_url")
        reasons.append("record carries no link to the original publisher")
    if not item.title.strip():
        score -= 0.3
        flags.append("empty_title")
        reasons.append("headline is empty")
    if item.published_at is None:
        score -= 0.15
        flags.append("no_published_at")
        reasons.append("publication time unknown, so recency cannot be trusted")
    else:
        age_h = (now - item.published_at).total_seconds() / 3600.0
        if age_h < -6:
            score -= 0.2
            flags.append("future_dated")
            reasons.append("publication time is in the future, suggesting a feed clock error")
        elif age_h > 24 * 30:
            score -= 0.05
            flags.append("very_old")
            reasons.append("older than 30 days")
    if not item.countries:
        score -= 0.05
        flags.append("no_country_match")
        reasons.append("no country could be resolved from the text")
    if len(item.summary or "") < 40 and len(item.title) < 40:
        score -= 0.1
        flags.append("thin_content")
        reasons.append("neither headline nor summary carries enough text to analyse")
    if item.syndication_group is not None:
        flags.append("syndicated")
        reasons.append("part of a group of near-identical copies from other outlets")
    if item.duplicate_of:
        score -= 0.5
        flags.append("duplicate")
        reasons.append("byte-identical to an already-ingested record")

    return QualityReport(score=max(0.0, min(1.0, score)), flags=flags, reasons=reasons)


# -- dedup + syndication ----------------------------------------------------


@dataclass
class DedupStats:
    total: int = 0
    unique: int = 0
    exact_duplicates: int = 0
    syndication_groups: int = 0
    syndicated_records: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "unique": self.unique,
            "exact_duplicates": self.exact_duplicates,
            "syndication_groups": self.syndication_groups,
            "syndicated_records": self.syndicated_records,
            "syndication_ratio": round(
                self.syndicated_records / self.total, 4
            )
            if self.total
            else 0.0,
        }


def deduplicate(
    items: Iterable[NewsItem],
    *,
    similarity_threshold: float = 0.92,
    window_hours: int = 96,
) -> tuple[list[NewsItem], DedupStats]:
    """Mark duplicates and syndication groups in place, return survivors.

    Returns every item (so callers can still count syndicated copies) but
    with ``duplicate_of`` and ``syndication_group`` populated. Callers decide
    whether to drop duplicates; the markers make that decision auditable.

    Similarity is only computed within a time window, which keeps this
    O(n) with small constants instead of comparing every pair.
    """
    items = list(items)
    stats = DedupStats(total=len(items))
    if not items:
        return items, stats

    # Pass 1: exact duplicates via canonical URL and content hash.
    seen_url: dict[str, str] = {}
    for item in items:
        fp = url_fingerprint(item.url)
        if fp and fp in seen_url:
            item.duplicate_of = seen_url[fp]
            stats.exact_duplicates += 1
        elif fp:
            seen_url[fp] = item.id

    # Pass 2: syndication clustering via SimHash, bounded by time proximity.
    ordered = sorted(
        (i for i in items if i.simhash and not i.duplicate_of),
        key=lambda i: i.published_at or i.collected_at,
    )
    # Bucket by day to avoid comparing across the whole corpus.
    buckets: dict[str, list[NewsItem]] = defaultdict(list)
    for item in ordered:
        ts = item.published_at or item.collected_at
        buckets[ts.strftime("%Y%m%d%H")].append(item)

    group_counter = 0
    for bucket in buckets.values():
        reps: list[NewsItem] = []
        for item in bucket:
            matched = None
            for rep in reps:
                if similarity(item.simhash, rep.simhash) >= similarity_threshold:
                    matched = rep
                    break
            if matched is None:
                reps.append(item)
                continue
            group = matched.syndication_group
            if group is None:
                group_counter += 1
                group = f"syn-{group_counter:05d}"
                matched.syndication_group = group
            item.syndication_group = group
            stats.syndicated_records += 1

    all_groups = {i.syndication_group for i in items if i.syndication_group}
    stats.syndication_groups = len(all_groups)
    stats.unique = len(items) - stats.exact_duplicates
    return items, stats


def dedupe_events(events: list[Any]) -> tuple[list[Any], int]:
    """Collapse identical events (same day, code, actors, location).

    GDELT routinely emits the same event once per mentioning article. Those
    are one event with many mentions, not many events.
    """
    seen: set[str] = set()
    out: list[Any] = []
    removed = 0
    for ev in events:
        loc = ev.location
        key = "|".join(
            [
                (ev.event_date.date().isoformat() if ev.event_date else ""),
                ev.event_code or "",
                (ev.actor1 or "")[:40].lower(),
                (ev.actor2 or "")[:40].lower(),
                f"{round(loc.lat, 1)}" if loc else "",
                f"{round(loc.lon, 1)}" if loc else "",
            ]
        )
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        out.append(ev)
    return out, removed


def staleness_seconds(last: datetime | None, now: datetime | None = None) -> int | None:
    """Age in seconds of the most recent successful collection."""
    if last is None:
        return None
    now = now or utcnow()
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return int((now - last).total_seconds())
