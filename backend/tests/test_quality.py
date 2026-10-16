"""Data-quality tests: deduplication, syndication and quality scoring.

The syndication test is the important one. Counting forty republished copies
of one wire story as forty independent events is the single most common way
an OSINT aggregate produces a confidently wrong answer, and it biases the
map toward whichever region's outlets syndicate most.
"""

from __future__ import annotations

from ig.quality import (
    content_hash,
    deduplicate,
    hamming,
    normalize_text,
    score_news_item,
    simhash,
    similarity,
    strip_html,
    url_fingerprint,
)

from .conftest import make_news


class TestTextNormalisation:
    def test_strip_html_unescapes_entities(self):
        assert strip_html("<p>Fish &amp; chips</p>") == " Fish & chips "

    def test_normalize_removes_punctuation_digits_and_accents(self):
        out = normalize_text("Café CRASH — 12 dead!!!")
        assert "cafe" in out
        assert "12" not in out
        assert "!" not in out

    def test_normalize_drops_boilerplate(self):
        out = normalize_text("Read more about the deal. Subscribe now.")
        assert "read more" not in out
        assert "subscribe" not in out


class TestUrlFingerprint:
    def test_tracking_params_removed(self):
        bare = url_fingerprint("https://example.com/a/b")
        tracked = url_fingerprint(
            "https://example.com/a/b?utm_source=x&utm_medium=y&fbclid=z"
        )
        assert bare == tracked

    def test_scheme_www_and_trailing_slash_normalised(self):
        assert url_fingerprint("http://www.example.com/x/") == url_fingerprint(
            "https://example.com/x"
        )

    def test_meaningful_query_preserved(self):
        a = url_fingerprint("https://example.com/x?id=1")
        b = url_fingerprint("https://example.com/x?id=2")
        assert a != b


class TestSimhash:
    def test_identical_text_is_identical(self):
        assert simhash("the quick brown fox jumps") == simhash("the quick brown fox jumps")

    def test_reordered_copy_stays_similar(self):
        a = simhash("one two three four five six seven eight nine ten")
        b = simhash("ten nine eight seven six five four three two one")
        assert similarity(a, b) > 0.5

    def test_unrelated_text_scores_low(self):
        a = simhash("quarterly earnings beat expectations in the energy sector")
        b = simhash("flooding displaced thousands across the river delta region")
        assert similarity(a, b) < 0.6

    def test_empty_text_yields_zero_and_no_similarity(self):
        assert simhash("") == 0
        assert similarity(0, simhash("anything")) == 0.0

    def test_hamming_of_equal_hashes_is_zero(self):
        h = simhash("some representative sentence for hashing")
        assert hamming(h, h) == 0


class TestDeduplication:
    def test_exact_url_duplicate_is_marked(self):
        a = make_news(1, url="https://example.test/a")
        b = make_news(2, url="https://example.test/a?utm_source=twitter")
        items, stats = deduplicate([a, b])
        assert stats.exact_duplicates == 1
        assert b.duplicate_of == a.id

    def test_syndicated_wire_copy_forms_one_group(self):
        body = (
            "Heavy flooding across the delta region has displaced thousands of "
            "residents and cut off several main roads, officials said on Thursday."
        )
        items = [
            make_news(i, title="Flooding displaces thousands", summary=body, source_name=f"Outlet {i}")
            for i in range(6)
        ]
        for i, item in enumerate(items):
            item.simhash = simhash(f"{item.title} {item.summary}")
            item.url = f"https://example.test/wire/{i}"

        _, stats = deduplicate(items, similarity_threshold=0.92)
        groups = {i.syndication_group for i in items if i.syndication_group}
        assert len(groups) == 1
        assert stats.syndication_groups == 1
        assert stats.syndicated_records == 5
        assert stats.as_dict()["syndication_ratio"] > 0.8

    def test_distinct_stories_are_not_grouped(self):
        items = [
            make_news(1, title="Election result announced", summary="Voters went to the polls in record numbers."),
            make_news(2, title="Currency slides further", summary="The central bank raised rates again on Tuesday."),
            make_news(3, title="Drought hits harvest", summary="Farmers report the worst season in a decade."),
        ]
        for item in items:
            item.simhash = simhash(f"{item.title} {item.summary}")
        _, stats = deduplicate(items)
        assert stats.syndication_groups == 0
        assert stats.exact_duplicates == 0

    def test_empty_input_is_safe(self):
        items, stats = deduplicate([])
        assert items == []
        assert stats.total == 0
        assert stats.as_dict()["syndication_ratio"] == 0.0


class TestQualityScoring:
    def test_missing_url_is_penalised_and_flagged(self):
        item = make_news(1)
        item.url = ""
        report = score_news_item(item, now=item.collected_at)
        assert "no_source_url" in report.flags
        assert report.score < 0.7
        assert report.reasons

    def test_missing_publication_time_is_flagged(self):
        item = make_news(1, published=False)
        report = score_news_item(item, now=item.collected_at)
        assert "no_published_at" in report.flags

    def test_future_dated_record_is_flagged(self):
        item = make_news(1, hours_ago=-48)
        report = score_news_item(item, now=item.collected_at)
        assert "future_dated" in report.flags

    def test_clean_record_scores_high(self):
        item = make_news(1, countries=["NG"])
        report = score_news_item(item, now=item.collected_at)
        assert report.score >= 0.9
        assert "no_source_url" not in report.flags

    def test_score_is_bounded(self):
        item = make_news(1)
        item.url = ""
        item.title = ""
        report = score_news_item(item, now=item.collected_at)
        assert 0.0 <= report.score <= 1.0


class TestContentHash:
    def test_stable_across_calls(self):
        assert content_hash("a", "b") == content_hash("a", "b")

    def test_order_matters(self):
        assert content_hash("a", "b") != content_hash("b", "a")
