"""Graph and backtesting tests.

The graph tests assert that edges carry their evidence and that a single
outlet repeating a claim cannot manufacture a strong link. The backtest
tests assert the greedy one-to-one matching rules, because a detector that
fires five times around one event must be scored as one hit plus four false
positives -- not five hits.
"""

from __future__ import annotations

from datetime import timedelta

from ig.engine.backtest import (
    DEFAULT_CAVEATS,
    Detection,
    GroundTruthEvent,
    coverage_adjusted_recall,
    evaluate,
    ground_truth_from_events,
    sweep_thresholds,
)
from ig.engine.graph import (
    RelationshipGraph,
    build_cyber_graph,
    build_sanctions_graph,
    merge_graphs,
)
from ig.models import Evidence, Severity

from .conftest import NOW, make_cve, make_event


class TestRelationshipGraph:
    def test_degree_and_weight_recomputed(self):
        g = RelationshipGraph()
        g.add_node("country:NG", "Nigeria", "country")
        g.add_node("org:acme", "Acme", "org")
        g.add_edge("country:NG", "org:acme", "co_occurs")
        g.recompute_degrees()
        assert g.nodes["country:NG"].degree == 1
        assert g.nodes["country:NG"].weight > 0

    def test_self_edges_are_rejected(self):
        import pytest

        g = RelationshipGraph()
        g.add_node("a", "A", "org")
        with pytest.raises(ValueError):
            g.add_edge("a", "a", "co_occurs")

    def test_source_diversity_beats_repetition(self):
        """Twelve outlets once each is stronger evidence than one outlet twelve times."""
        diverse = RelationshipGraph()
        diverse.add_node("a", "A", "org")
        diverse.add_node("b", "B", "org")
        for i in range(12):
            diverse.add_edge(
                "a",
                "b",
                "co_occurs",
                evidence=Evidence(
                    source_url=f"https://x.test/{i}",
                    source_name=f"Outlet {i}",
                ),
            )
        repetitive = RelationshipGraph()
        repetitive.add_node("a", "A", "org")
        repetitive.add_node("b", "B", "org")
        for _ in range(12):
            repetitive.add_edge(
                "a",
                "b",
                "co_occurs",
                evidence=Evidence(source_url="https://x.test/1", source_name="One Outlet"),
            )
        assert diverse.top_edges(1)[0].weight > repetitive.top_edges(1)[0].weight
        assert diverse.top_edges(1)[0].distinct_sources > repetitive.top_edges(1)[0].distinct_sources

    def test_edges_retain_evidence(self):
        g = RelationshipGraph()
        g.add_node("a", "A", "org")
        g.add_node("b", "B", "org")
        g.add_edge(
            "a",
            "b",
            "co_occurs",
            evidence=Evidence(source_url="https://x.test/1", source_name="Wire"),
        )
        edge = g.top_edges(1)[0]
        assert edge.evidence
        assert edge.as_dict()["evidence"][0]["source_url"] == "https://x.test/1"

    def test_relations_are_distinct_edges(self):
        g = RelationshipGraph()
        g.add_node("c", "C", "country")
        g.add_node("s", "S", "sanction")
        g.add_edge("c", "s", "sanctioned_by")
        g.add_edge("c", "s", "mentions")
        assert len(g.all_edges()) == 2

    def test_subgraph_expands_by_depth(self):
        g = RelationshipGraph()
        for i in range(5):
            g.add_node(f"n{i}", f"N{i}", "org")
        g.add_edge("n0", "n1", "co_occurs")
        g.add_edge("n1", "n2", "co_occurs")
        g.add_edge("n3", "n4", "co_occurs")
        sub = g.subgraph(["n0"], depth=2)
        assert "n2" in sub.nodes
        assert "n3" not in sub.nodes

    def test_communities_find_components(self):
        g = RelationshipGraph()
        for i in range(4):
            g.add_node(f"a{i}", f"A{i}", "org")
        for i in range(3):
            g.add_edge(f"a{i}", f"a{i + 1}", "co_occurs")
        comps = g.communities(min_size=3)
        assert comps and len(comps[0]) == 4

    def test_stats_report_kinds_and_relations(self):
        g = RelationshipGraph()
        g.add_node("c", "C", "country")
        g.add_node("o", "O", "org")
        g.add_edge("c", "o", "mentions")
        stats = g.stats()
        assert stats["nodes"] == 2
        assert stats["edges"] == 1
        assert stats["nodes_by_kind"]["country"] == 1

    def test_empty_graph_payload_is_safe(self):
        payload = RelationshipGraph().as_payload()
        assert payload["nodes"] == []
        assert payload["edges"] == []


class TestGraphBuilders:
    def test_sanctions_graph_links_country_to_entity(self):
        from ig.models import SanctionItem

        item = SanctionItem(
            id="s1",
            name="Test Entity",
            entity_type="entity",
            list_source="OFAC-SDN",
            countries=["IR"],
            programs=["IRAN"],
            source_url="https://treasury.gov/x",
        )
        g = build_sanctions_graph([item], country_lookup={"IR": "Iran"})
        assert "country:IR" in g.nodes
        assert "sanction:s1" in g.nodes
        assert any(e.relation == "sanctioned_by" for e in g.all_edges())

    def test_cyber_graph_marks_kev_exploitation(self):
        cves = [make_cve(i, in_kev=(i == 0), ransomware=(i == 0)) for i in range(3)]
        g = build_cyber_graph(cves)
        assert any(e.relation == "exploits" for e in g.all_edges())
        assert "cve:CVE-2026-1000" in g.nodes
        assert g.nodes["cve:CVE-2026-1000"].meta["in_kev"] is True

    def test_merge_sums_shared_edges(self):
        a = RelationshipGraph()
        a.add_node("x", "X", "org")
        a.add_node("y", "Y", "org")
        a.add_edge("x", "y", "co_occurs")
        b = RelationshipGraph()
        b.add_node("x", "X", "org")
        b.add_node("y", "Y", "org")
        b.add_edge("x", "y", "co_occurs")
        merged = merge_graphs(a, b)
        assert len(merged.all_edges()) == 1
        assert merged.all_edges()[0].occurrences == 2


class TestBacktestMatching:
    def test_detection_before_event_is_a_hit_with_lead_time(self):
        det = Detection(key="NG", fired_at=NOW - timedelta(hours=24), score=3.0)
        ev = GroundTruthEvent(key="NG", occurred_at=NOW, label="protest")
        result = evaluate([det], [ev], lead_window_hours=72.0)
        assert result.true_positives == 1
        assert result.matched[0][2] == 24.0
        assert result.precision == 1.0
        assert result.recall == 1.0

    def test_repeated_firing_around_one_event_is_one_hit_plus_false_positives(self):
        """The honest accounting: five alerts for one event is not five hits."""
        dets = [
            Detection(key="NG", fired_at=NOW - timedelta(hours=h), score=3.0)
            for h in (30, 25, 20, 15, 10)
        ]
        ev = GroundTruthEvent(key="NG", occurred_at=NOW)
        result = evaluate(dets, [ev], lead_window_hours=72.0)
        assert result.true_positives == 1
        assert len(result.false_positives) == 4
        assert result.precision == 0.2

    def test_event_with_no_detection_is_missed(self):
        ev = GroundTruthEvent(key="KE", occurred_at=NOW)
        result = evaluate([], [ev])
        assert result.recall == 0.0
        assert len(result.missed) == 1

    def test_detection_on_wrong_country_is_false_positive(self):
        det = Detection(key="GH", fired_at=NOW - timedelta(hours=5), score=3.0)
        ev = GroundTruthEvent(key="NG", occurred_at=NOW)
        result = evaluate([det], [ev])
        assert result.true_positives == 0
        assert len(result.false_positives) == 1
        assert len(result.missed) == 1

    def test_slightly_late_detection_still_counts(self):
        det = Detection(key="NG", fired_at=NOW + timedelta(hours=3), score=3.0)
        ev = GroundTruthEvent(key="NG", occurred_at=NOW)
        result = evaluate([det], [ev], late_tolerance_hours=6.0)
        assert result.true_positives == 1
        assert result.matched[0][2] == -3.0

    def test_too_late_detection_is_not_a_hit(self):
        det = Detection(key="NG", fired_at=NOW + timedelta(hours=30), score=3.0)
        ev = GroundTruthEvent(key="NG", occurred_at=NOW)
        assert evaluate([det], [ev], late_tolerance_hours=6.0).true_positives == 0

    def test_f1_of_perfect_run_is_one(self):
        det = Detection(key="NG", fired_at=NOW - timedelta(hours=1), score=3.0)
        ev = GroundTruthEvent(key="NG", occurred_at=NOW)
        assert evaluate([det], [ev]).f1 == 1.0

    def test_empty_inputs_do_not_divide_by_zero(self):
        result = evaluate([], [])
        assert result.precision == 0.0
        assert result.recall == 0.0
        assert result.f1 == 0.0

    def test_payload_always_carries_caveats(self):
        payload = evaluate([], []).as_dict()
        assert payload["caveats"] == DEFAULT_CAVEATS
        assert any("optimistic" in c for c in payload["caveats"])


class TestThresholdSweep:
    def test_sweep_hits_every_threshold(self):
        dets = [
            Detection(key="NG", fired_at=NOW - timedelta(hours=20), score=4.2),
            Detection(key="GH", fired_at=NOW - timedelta(hours=10), score=1.8),
        ]
        ev = GroundTruthEvent(key="NG", occurred_at=NOW)
        sweep = sweep_thresholds(dets, [ev], thresholds=(1.5, 2.0, 3.0, 4.0, 5.0))
        assert [row["threshold"] for row in sweep] == [1.5, 2.0, 3.0, 4.0, 5.0]
        assert sweep[0]["detections"] == 2
        assert sweep[-1]["detections"] == 0

    def test_raising_threshold_trades_recall_for_precision(self):
        dets = [
            Detection(key="NG", fired_at=NOW - timedelta(hours=20), score=4.5),
            Detection(key="GH", fired_at=NOW - timedelta(hours=8), score=1.6),
        ]
        ev = GroundTruthEvent(key="NG", occurred_at=NOW)
        sweep = sweep_thresholds(dets, [ev], thresholds=(1.5, 4.0))
        assert sweep[0]["precision"] < sweep[1]["precision"]


class TestGroundTruthAndCoverageAdjustment:
    def test_only_medium_and_above_become_ground_truth(self):
        events = [
            make_event(1, severity=Severity.INFO),
            make_event(2, severity=Severity.LOW),
            make_event(3, severity=Severity.MEDIUM),
            make_event(4, severity=Severity.HIGH),
            make_event(5, severity=Severity.CRITICAL),
        ]
        truth = ground_truth_from_events(events)
        assert len(truth) == 3, "the baseline tick of the feed is not an event"

    def test_events_without_country_are_excluded(self):
        ev = make_event(1, severity=Severity.HIGH)
        ev.country_iso2 = None
        assert ground_truth_from_events([ev]) == []

    def test_thin_coverage_discounts_low_recall(self):
        result = evaluate([], [GroundTruthEvent(key="TD", occurred_at=NOW)])
        raw = result.recall
        adjusted = coverage_adjusted_recall(result, {"TD": 0.02})
        assert adjusted > raw, "a miss in an unobserved territory is not a detector failure"

    def test_perfect_recall_is_unaffected_by_adjustment(self):
        det = Detection(key="NG", fired_at=NOW, score=3.0)
        result = evaluate([det], [GroundTruthEvent(key="NG", occurred_at=NOW)])
        assert coverage_adjusted_recall(result, {"NG": 0.9}) == 1.0
