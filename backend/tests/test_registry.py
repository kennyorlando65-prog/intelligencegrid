"""Regression tests for the source registry.

The bug these exist to prevent: ``SourceRegistry.enabled()`` read
``self.settings``, which ``__init__`` never set. Because the pipeline guards
every source individually, the resulting ``AttributeError`` did not surface as
an error -- it surfaced as an *empty collection run*, so all fourteen modules
rendered as "no data". That is a silent wrong answer rather than a visible
failure, which is the worst possible shape for a bug in an intelligence tool.

The API tests could not catch it: they seed the store directly and mark the
pipeline already-built, so ``ensure_ready`` short-circuits and the registry is
never exercised. These tests drive the registry itself, with no network.
"""

from __future__ import annotations

import pytest

from ig.config import get_settings
from ig.http import HttpClient
from ig.sources.base import Source, SourceRegistry
from ig.store import MedallionStore


class _StubSource(Source):
    """A source that returns fixed records, or raises on demand."""

    name = "stub"
    label = "Stub source"
    module = "news"

    def __init__(self, client, store, records=None, boom=False):
        super().__init__(client, store)
        self._records = records or []
        self._boom = boom

    async def collect(self):
        if self._boom:
            raise RuntimeError("upstream exploded")
        return list(self._records)


@pytest.fixture
def registry():
    return SourceRegistry(HttpClient(), MedallionStore())


class TestRegistryWiring:
    def test_registry_exposes_settings(self, registry):
        """The exact attribute whose absence broke every module."""
        assert registry.settings is get_settings()

    def test_enabled_does_not_raise(self, registry):
        registry.register(_StubSource(registry.client, registry.store))
        assert [s.name for s in registry.enabled()] == ["stub"]

    def test_enabled_respects_the_source_toggle(self, registry, monkeypatch):
        """An explicit allow-list must actually filter."""
        monkeypatch.setattr(
            registry,
            "settings",
            get_settings().__class__(enabled_sources=("something-else",)),
        )
        registry.register(_StubSource(registry.client, registry.store))
        assert registry.enabled() == []


class TestRunAll:
    async def test_run_all_returns_records_keyed_by_source(self, registry):
        registry.register(_StubSource(registry.client, registry.store, records=[1, 2, 3]))
        assert await registry.run_all() == {"stub": [1, 2, 3]}

    async def test_a_failing_source_is_recorded_not_raised(self, registry):
        """One dead feed must degrade the run, never break it."""
        registry.register(_StubSource(registry.client, registry.store, boom=True))
        assert await registry.run_all() == {"stub": []}
        row = registry.health_report()["sources"][0]
        assert row["status"] == "failed"
        assert "exploded" in row["error"]

    async def test_only_filter_selects_a_subset(self, registry):
        registry.register(_StubSource(registry.client, registry.store, records=[1]))
        assert await registry.run_all(only=["absent"]) == {}
