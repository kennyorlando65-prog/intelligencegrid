"""Coverage-confidence tests.

This module exists to keep the platform honest about the difference between
"nothing happened" and "we cannot see". The tests therefore assert the
counter-intuitive behaviours: that unobserved territories are still present
in the output, and that population does not drive the score.
"""

from __future__ import annotations

from ig.coverage import (
    africa_check,
    compute_coverage,
    continent_summary,
    coverage_index,
    focus_regions,
)
from ig.geo import african_iso_codes, all_countries, get

from .conftest import NOW, make_event, make_news


class TestGeoReference:
    def test_all_fifty_four_african_states_present(self):
        """The Africa pack is a stated requirement, so it is measured."""
        codes = african_iso_codes()
        assert len(codes) == 54, f"expected 54 African states, found {len(codes)}"

    def test_reference_table_covers_all_seven_continents(self):
        continents = {c.continent for c in all_countries()}
        assert continents == {"AF", "AS", "EU", "NA", "SA", "OC", "AN"}

    def test_reference_table_is_complete_per_continent(self):
        """Asserted per continent rather than against one loose total: a wrong
        total is ambiguous, whereas a wrong continent count names the gap."""
        from collections import Counter

        counts = Counter(c.continent for c in all_countries())
        assert counts["AF"] == 54, "Africa must be complete -- the pack requirement"
        assert counts["AS"] >= 45
        assert counts["EU"] >= 44
        assert counts["NA"] >= 20
        assert counts["SA"] >= 12
        assert counts["OC"] >= 14
        assert counts["AN"] >= 1
        assert sum(counts.values()) >= 190

    def test_codes_are_unique(self):
        codes = [c.iso2 for c in all_countries()]
        assert len(codes) == len(set(codes))

    def test_lookup_by_iso(self):
        assert get("ng").name == "Nigeria"
        assert get("ZZ") is None


class TestCoverageConfidence:
    def test_every_territory_appears_even_with_zero_records(self):
        """The entire point: an all-countries denominator makes quiet visible."""
        cells = compute_coverage(news_items=[], events=[], now=NOW)
        assert len(cells) == len(all_countries())
        assert all(c.coverage_confidence == 0.0 for c in cells)
        assert all(c.data_state == "no_data" for c in cells)

    def test_unobserved_territory_explains_itself_honestly(self):
        cells = compute_coverage(news_items=[], events=[], now=NOW)
        nigeria = next(c for c in cells if c.country_iso2 == "NG")
        assert "feed reach" in nigeria.explanation or "unobserved" in nigeria.explanation
        assert "not about events on the ground" in nigeria.explanation

    def test_more_distinct_sources_raises_confidence(self):
        one_source = [make_news(i, countries=["NG"], source_name="Only Wire") for i in range(8)]
        many_sources = [
            make_news(i, countries=["NG"], source_name=f"Outlet {i}") for i in range(8)
        ]
        low = _confidence_for("NG", one_source)
        high = _confidence_for("NG", many_sources)
        assert high > low, "source diversity must be the dominant term"

    def test_volume_alone_with_poor_diversity_scores_lower(self):
        spread = [
            make_news(i, countries=["KE"], source_name=f"Outlet {i}") for i in range(6)
        ]
        concentrated = [
            make_news(i, countries=["KE"], source_name="Single Outlet") for i in range(6)
        ]
        assert _confidence_for("KE", spread) > _confidence_for("KE", concentrated)

    def test_events_contribute_to_coverage(self):
        events = [make_event(i, country="GH", days_ago=0.2) for i in range(10)]
        assert _confidence_for("GH", [], events) > 0.0

    def test_population_does_not_drive_the_score(self):
        """A large population must not by itself raise confidence, or the score
        would encode a bias instead of measuring one."""
        tiny = _confidence_for("SC", [make_news(i, countries=["SC"]) for i in range(10)])
        large = _confidence_for("NG", [make_news(i, countries=["NG"]) for i in range(10)])
        # Same input volume, wildly different populations -> comparable scores.
        assert abs(tiny - large) < 0.05

    def test_stale_coverage_loses_recency_weight(self):
        fresh = _confidence_for("ZA", [make_news(i, countries=["ZA"], hours_ago=2) for i in range(6)])
        stale = _confidence_for(
            "ZA", [make_news(i, countries=["ZA"], hours_ago=24 * 30) for i in range(6)]
        )
        assert fresh > stale

    def test_confidence_is_bounded_unit_interval(self):
        huge = [
            make_news(i, countries=["US"], hours_ago=1, pack="global") for i in range(400)
        ]
        score = _confidence_for("US", huge)
        assert 0.0 <= score <= 1.0

    def test_data_states_are_labelled_distinctly(self):
        cells = compute_coverage(
            news_items=[make_news(i, countries=["EG"], hours_ago=1) for i in range(40)],
            events=[],
            now=NOW,
        )
        states = {c.data_state for c in cells}
        assert "no_data" in states
        assert any(s in states for s in ("good", "thin", "low_data"))


class TestContinentSummary:
    def test_all_continents_reported(self):
        cells = compute_coverage(news_items=[], events=[], now=NOW)
        summary = continent_summary(cells)
        assert len(summary) == 7
        names = {row["continent_name"] for row in summary}
        assert "Africa" in names and "Antarctica" in names

    def test_thin_continent_says_so(self):
        cells = compute_coverage(news_items=[], events=[], now=NOW)
        africa = next(r for r in continent_summary(cells) if r["continent"] == "AF")
        assert africa["countries"] == 54
        assert africa["countries_no_data"] == 54
        assert "floor" in africa["interpretation"]

    def test_observation_rate_is_unit_interval(self):
        cells = compute_coverage(
            news_items=[make_news(i, countries=["NG"], hours_ago=1) for i in range(10)],
            events=[],
            now=NOW,
        )
        for row in continent_summary(cells):
            assert 0.0 <= row["observation_rate"] <= 1.0


class TestFocusAndAfrica:
    def test_africa_check_counts_the_full_reference_set(self):
        cells = compute_coverage(news_items=[], events=[], now=NOW)
        report = africa_check(cells)
        assert report["countries_in_reference"] == 54
        assert report["countries_with_records"] == 0
        assert "absence is measurable" in report["note"]

    def test_focus_regions_ranks_by_unobserved_population(self):
        cells = compute_coverage(news_items=[], events=[], now=NOW)
        focus = focus_regions(cells, limit=5)
        assert len(focus) == 5
        pops = [row["population_millions"] for row in focus]
        assert pops == sorted(pops, reverse=True), "largest blind spots first"

    def test_coverage_index_maps_iso_to_float(self):
        cells = compute_coverage(news_items=[], events=[], now=NOW)
        index = coverage_index(cells)
        assert isinstance(index["NG"], float)
        assert len(index) == len(all_countries())


def _confidence_for(iso: str, news, events=()) -> float:
    cells = compute_coverage(news_items=news, events=events, now=NOW)
    cell = next((c for c in cells if c.country_iso2 == iso), None)
    assert cell is not None, f"{iso} missing from coverage output entirely"
    return cell.coverage_confidence
