"""Medallion storage: bronze -> silver -> gold.

Layering contract enforced here:

* **Bronze** is append-only and immutable. Raw payloads land here and are
  never mutated. Re-running a collection appends; it never overwrites. This
  is what makes historical replay and audit possible.
* **Silver** is the cleansed, deduplicated, conformed view. Records here
  have normalised timestamps, resolved country codes, dedup markers and
  quality flags. Silver is joinable across domains (a news item and an
  event can be related by country + time window).
* **Gold** is aggregated for query patterns: per-country counts, per-day
  series, source health, coverage confidence.

On a serverless platform there is no writable disk between invocations, so
the canonical store lives in process memory with a TTL, and disk
persistence is an *optional* local-development convenience. The API is
identical either way, which keeps the deployment story honest.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from .models import Layer, utcnow

T = TypeVar("T", bound=BaseModel)


@dataclass
class BronzeRecord:
    """One immutable raw payload, exactly as received."""

    id: str
    source: str
    fetched_at: datetime
    url: str
    payload: bytes
    content_type: str = ""
    status: int = 200
    note: str = ""

    def size(self) -> int:
        return len(self.payload)


@dataclass
class Dataset(Generic[T]):
    """A named collection of silver/gold records with provenance."""

    name: str
    layer: Layer
    records: list[T] = field(default_factory=list)
    built_at: datetime = field(default_factory=utcnow)
    ttl_seconds: int = 900
    notes: list[str] = field(default_factory=list)

    def is_fresh(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return (now - self.built_at).total_seconds() < self.ttl_seconds

    def age_seconds(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        return int((now - self.built_at).total_seconds())


class BronzeStore:
    """Append-only raw payload store.

    Bounded in memory (``max_records``) because a serverless function cannot
    hold an unbounded archive; the bound is explicit and surfaced in health
    output rather than silently dropping data.
    """

    def __init__(self, max_records: int = 200, persist_dir: str | None = None) -> None:
        self._lock = threading.RLock()
        self._records: list[BronzeRecord] = []
        self._max = max_records
        self._persist_dir = persist_dir
        self._total_appended = 0
        self._evicted = 0

    def append(self, record: BronzeRecord) -> None:
        """Append a raw payload. Never mutates existing entries."""
        with self._lock:
            self._records.append(record)
            self._total_appended += 1
            while len(self._records) > self._max:
                self._records.pop(0)
                self._evicted += 1
        if self._persist_dir:
            self._persist(record)

    def _persist(self, record: BronzeRecord) -> None:
        try:
            path = os.path.join(self._persist_dir, record.source)
            os.makedirs(path, exist_ok=True)
            stamp = record.fetched_at.strftime("%Y%m%dT%H%M%S")
            with open(os.path.join(path, f"{stamp}_{record.id}.raw"), "wb") as fh:
                fh.write(record.payload)
        except OSError:
            # Persistence is a convenience; never let it break collection.
            pass

    def latest(self, source: str) -> BronzeRecord | None:
        with self._lock:
            for rec in reversed(self._records):
                if rec.source == source:
                    return rec
        return None

    def all(self) -> list[BronzeRecord]:
        with self._lock:
            return list(self._records)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            by_source: dict[str, int] = defaultdict(int)
            bytes_by_source: dict[str, int] = defaultdict(int)
            for rec in self._records:
                by_source[rec.source] += 1
                bytes_by_source[rec.source] += rec.size()
            return {
                "records": len(self._records),
                "total_appended": self._total_appended,
                "evicted": self._evicted,
                "max_records": self._max,
                "bytes": sum(bytes_by_source.values()),
                "by_source": dict(by_source),
                "bytes_by_source": dict(bytes_by_source),
            }


class MedallionStore:
    """Holds every dataset and the bronze archive.

    Read-through: a caller asks for a dataset by name; if it is fresh it is
    returned, otherwise the registered builder runs and replaces it. This is
    the single place TTL semantics live, so no module re-implements caching.
    """

    def __init__(self, ttl_seconds: int = 900, persist: bool = False) -> None:
        self.ttl_seconds = ttl_seconds
        self.bronze = BronzeStore(persist_dir="data/bronze" if persist else None)
        self._datasets: dict[str, Dataset[Any]] = {}
        self._lock = threading.RLock()
        self._build_errors: list[str] = []

    def put(self, dataset: Dataset[Any]) -> None:
        with self._lock:
            self._datasets[dataset.name] = dataset

    def get(self, name: str) -> Dataset[Any] | None:
        with self._lock:
            return self._datasets.get(name)

    def get_fresh(self, name: str, now: datetime | None = None) -> Dataset[Any] | None:
        ds = self.get(name)
        if ds is not None and ds.is_fresh(now):
            return ds
        return None

    def record_error(self, message: str) -> None:
        with self._lock:
            self._build_errors.append(message)
            if len(self._build_errors) > 50:
                self._build_errors.pop(0)

    def errors(self) -> list[str]:
        with self._lock:
            return list(self._build_errors)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._datasets.keys())

    def summary(self) -> dict[str, Any]:
        with self._lock:
            rows = []
            for name, ds in sorted(self._datasets.items()):
                rows.append(
                    {
                        "dataset": name,
                        "layer": ds.layer.value,
                        "records": len(ds.records),
                        "age_seconds": ds.age_seconds(),
                        "ttl_seconds": ds.ttl_seconds,
                        "fresh": ds.is_fresh(),
                        "notes": ds.notes,
                    }
                )
            return {
                "datasets": rows,
                "bronze": self.bronze.stats(),
                "build_errors": self._build_errors[-10:],
            }


# -- gold aggregation helpers ------------------------------------------------


def count_by(records: list[Any], attr: str) -> dict[str, int]:
    """Count records grouping by a scalar attribute, skipping None."""
    out: dict[str, int] = defaultdict(int)
    for rec in records:
        val = getattr(rec, attr, None)
        if val:
            out[str(val)] += 1
    return dict(out)


def daily_series(
    records: list[Any],
    date_attr: str,
    days: int = 30,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Zero-filled daily counts.

    Zero-fill matters: a chart that silently omits empty days makes a gap
    look like continuous activity. Every day in the window is present.
    """
    now = now or utcnow()
    start = (now - timedelta(days=days - 1)).date()
    buckets: dict[str, int] = defaultdict(int)
    for rec in records:
        val = getattr(rec, date_attr, None)
        if isinstance(val, datetime):
            day = val.astimezone(timezone.utc).date()
            if day >= start:
                buckets[day.isoformat()] += 1
    out = []
    for i in range(days):
        day = (start + timedelta(days=i)).isoformat()
        out.append({"date": day, "count": buckets.get(day, 0)})
    return out


def top_n(counts: dict[str, int], n: int = 10) -> list[dict[str, Any]]:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"key": k, "count": v} for k, v in ranked[:n]]


def summarize_text(text: str, limit: int = 280) -> str:
    """Truncate on a word boundary, appending an ellipsis only if cut."""
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rsplit(" ", 1)[0]
    return f"{cut}..."


def compact_json(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"), ensure_ascii=False)


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
