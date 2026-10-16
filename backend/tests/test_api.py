"""API integration tests.

Two things are asserted here that nothing else can:

1. **Every one of the fourteen modules answers.** A module that 500s on an
   empty store is a module that will 500 at 3am on a quiet day, which is
   exactly when it is being relied on.

2. **``/api/x`` and ``/x`` return an identical body.** The deployed service is
   reached through a rewrite that prepends ``/api``; a local ``uvicorn
   main:app`` is not. If the two ever diverge, the app works locally and 404s
   in production -- the most expensive possible way to find a routing bug.

The pipeline is seeded directly and marked already-built, so ``ensure_ready``
short-circuits and no test touches the network. A test suite that depends on
live upstream feeds fails intermittently for reasons unrelated to the code.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from ig.models import Layer, SanctionItem
from ig.modules.registry import MODULES
from ig.pipeline import (
    DATASET_COUNTRY_STATS,
    DATASET_COVERAGE,
    DATASET_CYBER,
    DATASET_EVENTS,
    DATASET_GRAPH,
    DATASET_NEWS,
    DATASET_SANCTIONS,
    DATASET_SIGNALS,
    DATASET_TRACKS,
    Pipeline,
    PipelineReport,
)
from ig.store import Dataset

from .conftest import make_cve, make_event, make_news

#: Fields that legitimately differ between two calls to the same endpoint.
VOLATILE = {"generated_at", "checked_at", "built_at", "data_built_at", "refreshed_at"}


def _strip_volatile(payload):
    if isinstance(payload, dict):
        return {k: _strip_volatile(v) for k, v in payload.items() if k not in VOLATILE}
    if isinstance(payload, list):
        return [_strip_volatile(v) for v in payload]
    return payload


@pytest.fixture
def seeded_pipeline():
    """A real Pipeline with real gold-layer derivation, but no collection."""
    pipe = Pipeline()
    ttl = 900

    news = [
        make_news(
            i,
            countries=[["NG", "KE", "ZA", "EG", "GH"][i % 5]],
            source_name=f"Outlet {i % 4}",
            pack=["africa", "global"][i % 2],
            hours_ago=(i % 20) + 1,
        )
        for i in range(30)
    ]
    events = [
        make_event(i, country=["NG", "KE", "ZA", "EG"][i % 4], days_ago=(i % 5) * 0.4)
        for i in range(24)
    ]
    cyber = [
        make_cve(
            i,
            in_kev=(i % 3 == 0),
            ransomware=(i % 6 == 0),
            cvss=[9.8, 7.5, 5.0, None][i % 4],
            vendor=["Acme", "Globex", "Initech"][i % 3],
        )
        for i in range(12)
    ]
    sanctions = [
        SanctionItem(
            id=f"san-{i}",
            name=f"Sanctioned Entity {i}",
            entity_type=["individual", "entity", "vessel"][i % 3],
            list_source=["OFAC-SDN", "EU-FSD", "UN-SC"][i % 3],
            countries=[["IR", "RU", "KP"][i % 3]],
            programs=[f"PROGRAM-{i % 4}"],
            source_url="https://example.test/sanctions",
        )
        for i in range(9)
    ]

    pipe.store.put(Dataset(name=DATASET_NEWS, layer=Layer.SILVER, records=news, ttl_seconds=ttl))
    pipe.store.put(
        Dataset(name=DATASET_EVENTS, layer=Layer.SILVER, records=events, ttl_seconds=ttl)
    )
    pipe.store.put(Dataset(name=DATASET_CYBER, layer=Layer.SILVER, records=cyber, ttl_seconds=ttl))
    pipe.store.put(
        Dataset(name=DATASET_SANCTIONS, layer=Layer.SILVER, records=sanctions, ttl_seconds=ttl)
    )
    pipe.store.put(Dataset(name=DATASET_TRACKS, layer=Layer.SILVER, records=[], ttl_seconds=ttl))

    # Derive the real gold layer so the tests exercise the aggregation code
    # rather than a hand-built stand-in.
    pipe._build_gold(PipelineReport())

    assert pipe.store.get(DATASET_COVERAGE) is not None
    assert pipe.store.get(DATASET_SIGNALS) is not None
    assert pipe.store.get(DATASET_COUNTRY_STATS) is not None
    assert pipe.store.get(DATASET_GRAPH) is not None

    pipe._built_once = True
    return pipe


@pytest.fixture
def client(seeded_pipeline, monkeypatch):
    monkeypatch.setitem(main._STATE, "pipeline", seeded_pipeline)
    monkeypatch.setitem(main._STATE, "watchlists", None)
    return TestClient(main.app)


class TestServiceEndpoints:
    def test_root_lists_all_fourteen_modules(self, client):
        body = client.get("/").json()
        assert body["module_count"] == 14
        assert len(body["modules"]) == 14

    def test_health_is_cheap_and_reports_state(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["datasets_built"] is True
        assert body["dataset_count"] >= 5

    def test_modules_index_matches_the_dispatch_table(self, client):
        body = client.get("/modules").json()
        assert set(body["ids"]) == set(MODULES)
        assert body["count"] == len(MODULES) == 14

    def test_sources_endpoint_explains_absence_of_data(self, client):
        """Before any collection there is no health to report, and the API
        must say that rather than implying a healthy empty world."""
        body = client.get("/sources").json()
        assert "sources" in body
        assert body.get("note") or body.get("total") is not None


class TestPrefixEquivalence:
    """The deployed service sees ``/api``; local uvicorn does not."""

    @pytest.mark.parametrize(
        "path",
        [
            "/health",
            "/modules",
            "/news",
            "/events",
            "/signals",
            "/threats",
            "/sanctions",
            "/flights",
            "/maritime",
            "/timeline",
            "/evidence",
            "/map",
            "/entities",
            "/backtest",
            "/coverage",
            "/watchlists",
        ],
    )
    def test_prefixed_and_bare_paths_return_identical_bodies(self, client, path):
        bare = client.get(path)
        prefixed = client.get(f"/api{path}")
        assert bare.status_code == 200, f"{path} -> {bare.status_code}"
        assert prefixed.status_code == 200, f"/api{path} -> {prefixed.status_code}"
        assert _strip_volatile(bare.json()) == _strip_volatile(prefixed.json())

    def test_bare_api_root_resolves(self, client):
        assert client.get("/api").status_code == 200
        assert client.get("/api").json()["service"] == "IntelligenceGrid API"


class TestEveryModuleAnswers:
    @pytest.mark.parametrize("module_id", sorted(MODULES))
    def test_module_returns_payload_with_meta(self, client, module_id):
        if module_id == "watchlists":
            path = "/watchlists"
        else:
            path = f"/{module_id}"
        response = client.get(path)
        assert response.status_code == 200, (
            f"module '{module_id}' failed with {response.status_code}: {response.text[:300]}"
        )
        body = response.json()
        assert isinstance(body, dict)
        assert "meta" in body, f"module '{module_id}' returned no operational meta block"

    @pytest.mark.parametrize("module_id", sorted(MODULES))
    def test_module_meta_reports_source_health(self, client, module_id):
        path = "/watchlists" if module_id == "watchlists" else f"/{module_id}"
        meta = client.get(path).json()["meta"]
        assert "generated_at" in meta
        assert "degraded" in meta
        assert "confidence_disclaimer" in meta
        assert "certainty" in meta["confidence_disclaimer"]


class TestModulePayloads:
    def test_news_filters_by_country(self, client):
        body = client.get("/news", params={"countries": "NG", "limit": 50}).json()
        assert body["returned"] >= 1
        for article in body["articles"]:
            assert "NG" in article["countries"]

    def test_news_search_narrows_results(self, client):
        broad = client.get("/news", params={"limit": 100}).json()["returned"]
        narrow = client.get("/news", params={"q": "Headline number 7", "limit": 100}).json()
        assert narrow["returned"] <= broad

    def test_events_carry_coordinates_for_the_map(self, client):
        body = client.get("/events", params={"limit": 100}).json()
        assert body["returned"] >= 1
        placed = [e for e in body["events"] if e["location"]]
        assert placed, "geocoded events must retain coordinates"
        assert all(-90 <= e["location"]["lat"] <= 90 for e in placed)

    def test_threats_separates_kev_from_catalogue(self, client):
        body = client.get("/threats", params={"limit": 100}).json()
        assert body["kev_total"] >= 1
        assert body["total_cves"] >= body["kev_total"]
        assert "kev_definition" in body["notes"]

    def test_threats_kev_only_filter(self, client):
        body = client.get("/threats", params={"kev_only": "true", "limit": 100}).json()
        assert all(a["in_kev"] for a in body["alerts"])

    def test_sanctions_keeps_jurisdictions_separate(self, client):
        body = client.get("/sanctions", params={"limit": 100}).json()
        lists = {e["list_source"] for e in body["entities"]}
        assert len(lists) > 1, "three jurisdictions must not be merged into one"

    def test_sanctions_graph_is_attached(self, client):
        body = client.get("/sanctions", params={"limit": 100}).json()
        assert "graph" in body
        assert body["graph"]["stats"]["nodes"] > 0

    def test_maritime_states_its_limitation_explicitly(self, client):
        body = client.get("/maritime").json()
        assert body["status"]["live_feed_available"] is False
        caveats = body["what_this_module_can_and_cannot_show"]
        assert caveats["cannot_show"], "must state what it cannot show"
        assert "cannot be shown" or "cannot_show" in caveats

    def test_flights_carries_spoofing_caveat(self, client):
        body = client.get("/flights").json()
        assert any("spoof" in c for c in body["caveats"])

    def test_entities_explains_node_types(self, client):
        body = client.get("/entities").json()
        assert "node_type_guide" in body
        assert "country" in body["node_type_guide"]

    def test_map_shading_is_coverage_not_volume(self, client):
        body = client.get("/map").json()
        assert "coverage confidence" in body["legend_note"]

    def test_coverage_reports_the_africa_check(self, client):
        body = client.get("/coverage").json()
        assert body["africa_check"]["countries_in_reference"] == 54
        assert body["continents"] and len(body["continents"]) == 7
        assert body["summary"]["territories"] >= 190

    def test_coverage_never_claims_absence(self, client):
        body = client.get("/coverage").json()
        assert "low-data" in body["interpretation"] or "cannot see" in body["interpretation"]

    def test_backtest_carries_method_and_caveats(self, client):
        body = client.get("/backtest").json()
        assert "method" in body
        assert "how_to_read_this" in body
        assert body["evaluation"]["caveats"]

    def test_evidence_explains_reasoning_chain(self, client):
        body = client.get("/evidence").json()
        assert "chain_of_custody" in body
        assert body["chain_of_custody"]["bronze"]

    def test_timeline_frames_are_zero_filled_and_ordered(self, client):
        body = client.get("/timeline", params={"days": 3, "bucket_hours": 12}).json()
        frames = body["frames"]
        assert len(frames) == 6
        starts = [f["window_start"] for f in frames]
        assert starts == sorted(starts)

    def test_signal_payload_always_carries_evidence_and_caveats(self, client):
        body = client.get("/signals").json()
        for signal in body["signals"]:
            assert "evidence" in signal
            assert "caveats" in signal
            assert "confidence" in signal


class TestWatchlistLifecycle:
    def test_create_list_digest_delete(self, client):
        created = client.post(
            "/watchlists", params={"name": "Sahel", "countries": "NG,KE", "modules": "events"}
        ).json()
        watchlist = created["watchlist"]
        wid = watchlist["id"]
        assert watchlist["country_count"] == 2
        assert "not durable" in watchlist["storage"]

        listed = client.get("/watchlists").json()
        assert any(w["id"] == wid for w in listed["watchlists"])

        digest = client.get(f"/watchlists/{wid}/digest").json()
        assert digest["digest"]["watchlist"]["id"] == wid
        assert digest["digest"]["text_preview"]
        assert digest["digest"]["caveats"]

        removed = client.delete(f"/watchlists/{wid}")
        assert removed.status_code == 200
        assert removed.json()["removed"] is True

        assert client.get(f"/watchlists/{wid}/digest").status_code == 404

    def test_digest_of_unknown_watchlist_is_404(self, client):
        assert client.get("/watchlists/does-not-exist/digest").status_code == 404

    def test_deleting_unknown_watchlist_is_404(self, client):
        assert client.delete("/watchlists/nope").status_code == 404


class TestExport:
    def test_csv_export_has_header_and_rows(self, client):
        response = client.get("/export", params={"module": "news", "format": "csv"})
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        lines = [ln for ln in response.text.splitlines() if ln.strip()]
        assert len(lines) >= 2
        assert "title" in lines[0]

    def test_csv_export_is_quoted_for_embedded_commas(self, client):
        response = client.get("/export", params={"module": "events", "format": "csv"})
        assert '"' in response.text, "fields containing commas must be quoted"

    def test_json_export_round_trips(self, client):
        response = client.get("/export", params={"module": "coverage", "format": "json"})
        payload = response.json()
        assert payload["countries"]

    def test_unknown_module_is_404(self, client):
        assert client.get("/export", params={"module": "nope"}).status_code == 404

    def test_unknown_format_is_400(self, client):
        assert client.get("/export", params={"module": "news", "format": "xml"}).status_code == 400


class TestResilience:
    def test_module_survives_a_total_warmup_failure(self, monkeypatch):
        """A dead upstream must degrade the response, not 500 it."""

        class Exploding:
            def __init__(self):
                from ig.store import MedallionStore

                self.store = MedallionStore()
                self.registry = None
                self.last_report = PipelineReport()

            async def ensure_ready(self):
                raise RuntimeError("simulated upstream meltdown")

            def news(self):
                return []

            def events(self):
                return []

            def cyber(self):
                return []

            def sanctions(self):
                return []

            def tracks(self):
                return []

            def coverage(self):
                return []

            def signals(self):
                return []

            def graph(self):
                return None

            def country_stats(self):
                return []

            def narrative(self):
                return []

            def backtest(self, days=30, detector="spike"):
                return {"caveats": []}

        monkeypatch.setitem(main._STATE, "pipeline", Exploding())
        client = TestClient(main.app)
        response = client.get("/coverage")
        assert response.status_code == 200
        body = response.json()
        assert "warmup_error" in body["meta"]
        assert "meltdown" in body["meta"]["warmup_error"]

    def test_unknown_article_is_404(self, client):
        assert client.get("/news/nonexistent-id").status_code == 404
