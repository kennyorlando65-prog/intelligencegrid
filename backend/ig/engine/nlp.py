"""Lexicon sentiment, narrative extraction and lightweight entity recognition.

Deliberately dependency-free. A transformer sentiment model would be more
accurate on benchmark data and would also blow the serverless bundle limit
and require a multi-hundred-megabyte download on cold start. For an OSINT
triage tool the requirement is *relative* movement -- is this country's
coverage getting more negative this week than last -- and a transparent
lexicon answers that with no black box.

The lexicon approach is also auditable: every score can be traced to the
exact tokens that produced it, which matters when a signal is shown to an
analyst alongside its evidence.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from ..quality import normalize_text

# -- lexicon -----------------------------------------------------------------
# Weighted rather than binary: "catastrophic" should outweigh "concern".
# Negative terms are weighted harder because OSINT copy is written in a
# neutral register; the presence of strongly negative language is more
# informative than the presence of mildly positive language.

_POSITIVE: dict[str, float] = {
    "agreement": 1.5, "agreed": 1.2, "aid": 0.8, "approved": 1.2, "boost": 1.0,
    "breakthrough": 2.0, "ceasefire": 2.2, "cooperation": 1.3, "deal": 0.9,
    "decline": 0.4, "eased": 1.4, "election": 0.6, "growth": 1.2, "improved": 1.5,
    "improving": 1.4, "investment": 0.9, "landmark": 1.5, "peace": 2.2,
    "progress": 1.4, "recovery": 1.5, "reform": 1.0, "relief": 1.3, "rescue": 1.4,
    "resolved": 1.5, "resumed": 1.0, "reunited": 1.5, "safe": 1.1, "stabilised": 1.3,
    "stabilized": 1.3, "success": 1.5, "successful": 1.5, "surplus": 1.0,
    "truce": 2.0, "victory": 1.3, "welcomed": 1.1, "win": 1.2, "wins": 1.2,
}

_NEGATIVE: dict[str, float] = {
    "abuse": 1.8, "ambush": 2.0, "arrest": 1.2, "arrested": 1.2, "attacked": 2.0,
    "attack": 1.8, "blast": 2.0, "blockade": 1.8, "bomb": 2.2, "bombing": 2.2,
    "casualties": 2.1, "catastrophe": 2.6, "catastrophic": 2.6, "clash": 1.8,
    "clashes": 1.8, "collapse": 2.2, "collapsed": 2.2, "conflict": 1.6,
    "crackdown": 2.0, "crisis": 2.2, "dead": 2.0, "deaths": 2.2, "coup": 2.4,
    "crash": 1.9, "damage": 1.4, "danger": 1.5, "deadly": 2.2, "decline": 0.3,
    "deficit": 1.2, "deported": 1.4, "destroyed": 2.2, "disaster": 2.4,
    "drought": 1.9, "earthquake": 2.0, "escalation": 1.9, "evacuate": 1.7,
    "explosion": 2.2, "famine": 2.6, "fatalities": 2.2, "fled": 1.5, "flood": 1.8,
    "fraud": 1.7, "hacked": 1.8, "hostage": 2.2, "hunger": 1.7, "injured": 1.6,
    "killed": 2.3, "killing": 2.2, "kidnapped": 2.3, "layoffs": 1.5, "massacre": 2.8,
    "militant": 1.7, "missile": 2.0, "outbreak": 2.0, "pandemic": 2.2,
    "panic": 1.8, "plunge": 1.6, "protest": 1.2, "protesters": 1.3, "raid": 1.6,
    "recession": 1.9, "refugee": 1.5, "refugees": 1.5, "rejected": 0.8,
    "riot": 1.9, "sanction": 1.5, "sanctions": 1.6, "seized": 1.4, "shooting": 2.2,
    "shortage": 1.5, "shot": 1.7, "slump": 1.6, "strike": 1.1, "strikes": 1.1,
    "storm": 1.3, "strike action": 1.3, "surge": 0.5, "suspended": 1.0,
    "tension": 1.5, "terror": 2.4, "threat": 1.6, "threatened": 1.7,
    "unrest": 1.7, "violence": 2.0, "violent": 1.9, "war": 2.3, "warning": 1.2,
    "wounded": 1.7,
}

_NEGATORS = {"not", "no", "never", "without", "denied", "denies", "refused", "fails", "failed"}
_INTENSIFIERS = {"very": 1.4, "extremely": 1.7, "massive": 1.6, "major": 1.4, "severe": 1.6}


@dataclass
class SentimentResult:
    score: float  # -1..1
    positive_hits: list[str]
    negative_hits: list[str]
    tokens: int

    @property
    def label(self) -> str:
        if self.score <= -0.25:
            return "negative"
        if self.score >= 0.25:
            return "positive"
        return "neutral"

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "label": self.label,
            "positive_terms": self.positive_hits[:10],
            "negative_terms": self.negative_hits[:10],
            "tokens": self.tokens,
        }


def analyze_sentiment(text: str) -> SentimentResult:
    """Weighted lexicon polarity with negation and intensifier handling.

    The final score is normalised by the square root of document length so a
    long article is not automatically more extreme than a short one -- the
    naive divide-by-length would badly flatten long-form reporting.
    """
    if not text:
        return SentimentResult(0.0, [], [], 0)
    raw_tokens = normalize_text(text).split()
    if not raw_tokens:
        return SentimentResult(0.0, [], [], 0)

    total = 0.0
    pos: list[str] = []
    neg: list[str] = []

    for i, token in enumerate(raw_tokens):
        weight = _POSITIVE.get(token)
        sign = 1.0
        if weight is None:
            weight = _NEGATIVE.get(token)
            sign = -1.0
        if weight is None:
            continue

        # Look back up to two tokens for a negator or intensifier.
        multiplier = 1.0
        for back in (1, 2):
            if i - back < 0:
                break
            prev = raw_tokens[i - back]
            if prev in _NEGATORS:
                multiplier *= -0.8
            elif prev in _INTENSIFIERS:
                multiplier *= _INTENSIFIERS[prev]
        value = sign * weight * multiplier
        total += value
        if value > 0:
            pos.append(token)
        elif value < 0:
            neg.append(token)

    norm = math.sqrt(len(raw_tokens))
    score = max(-1.0, min(1.0, total / (norm * 1.6) if norm else 0.0))
    return SentimentResult(score, pos, neg, len(raw_tokens))


def sentiment_trend(
    scores_by_day: Iterable[tuple[str, list[float]]],
) -> list[dict]:
    """Per-day mean sentiment with counts. Used for the Signal Desk trend view."""
    out = []
    for day, scores in scores_by_day:
        if not scores:
            out.append({"date": day, "mean": 0.0, "count": 0})
            continue
        out.append(
            {
                "date": day,
                "mean": round(sum(scores) / len(scores), 4),
                "count": len(scores),
            }
        )
    return out


# -- keyword / narrative extraction -----------------------------------------

_STOPWORDS = frozenset(
    """
a about above after again against all am an and any are aren as at be because been before being
below between both but by can cannot could couldn did didn do does doesn doing don down during
each few for from further had hadn has hasn have haven having he her here hers herself him himself
his how i if in into is isn it its itself let me more most mustn my myself no nor not of off on
once only or other ought our ours ourselves out over own same shan she should shouldn so some such
than that the their theirs them themselves then there these they this those through to too under
until up very was wasn we were weren what when where which while who whom why with won would
wouldn you your yours yourself yourselves also said says say will one two three new news
report reports reported according told tell year years day days week weeks month months
"""
.split()
)

_PHRASE_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){1,3}\b")
_LAUNCH_NOISE = re.compile(r"^(The|This|That|These|Those|There|Their|But|And|For|With|From|After|Before)\b")


def extract_keywords(text: str, top_n: int = 12) -> list[dict]:
    """Rank terms by frequency with a crude but effective relevance lift.

    Multi-word capitalised phrases are scored higher because in news copy a
    repeated proper-noun phrase ("Red Sea", "Central Bank") is far more
    likely to be the actual subject than a repeated common word.
    """
    if not text:
        return []
    norm = normalize_text(text)
    tokens = [t for t in norm.split() if len(t) > 2 and t not in _STOPWORDS]
    if not tokens:
        return []

    uni = Counter(tokens)
    bi = Counter(f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1))

    scored: dict[str, float] = {}
    for term, count in uni.items():
        if count >= 2:
            scored[term] = float(count)
    for phrase, count in bi.items():
        if count >= 2:
            scored[phrase] = float(count) * 2.2

    for phrase in _PHRASE_RE.findall(text):
        key = phrase.lower()
        if _LAUNCH_NOISE.match(phrase):
            continue
        if key in scored:
            scored[key] += 3.0
        else:
            scored[key] = 1.5

    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return [{"term": t, "weight": round(w, 3)} for t, w in ranked]


def extract_entities(text: str, known_countries: Iterable[str] = ()) -> dict[str, list[str]]:
    """Pattern-based entity extraction: organisations, places, people.

    Regex and heuristics, not a NER model. It over-extracts (any two
    capitalised words look like a name), so the output is presented as
    *candidate* entities to be confirmed by co-occurrence across many
    documents -- which is how the entity graph uses it.
    """
    if not text:
        return {"orgs": [], "places": [], "people": []}

    orgs, places, people = [], [], []
    org_markers = (
        "Ministry", "Agency", "Authority", "Bank", "Committee", "Council", "Court",
        "Department", "Commission", "Union", "Organization", "Organisation", "Party",
        "University", "Institute", "Force", "Army", "Police", "Corp", "Group", "Ltd",
        "Inc", "Brigade", "Coalition", "Alliance", "Fund", "Bureau",
    )
    place_markers = (
        "Province", "State", "District", "County", "City", "Republic", "Region",
        "Capital", "Port", "Island", "Peninsula", "Valley", "Delta", "Sea", "Ocean",
        "River", "Strait", "Border", "Gulf", "Bay",
    )
    for phrase in _PHRASE_RE.findall(text):
        if _LAUNCH_NOISE.match(phrase):
            continue
        if any(m in phrase for m in org_markers):
            orgs.append(phrase)
        elif any(m in phrase for m in place_markers):
            places.append(phrase)
        else:
            people.append(phrase)

    known = {k.lower() for k in known_countries}
    for phrase in _PHRASE_RE.findall(text):
        if phrase.lower() in known and phrase not in places:
            places.append(phrase)

    return {
        "orgs": _uniq(orgs, 20),
        "places": _uniq(places, 20),
        "people": _uniq(people, 20),
    }


def _uniq(values: Iterable[str], limit: int) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
        if len(out) >= limit:
            break
    return out


def narrative_keywords(items: list[object], top_n: int = 20) -> list[dict]:
    """Aggregate keywords across many items to surface a shared narrative."""
    counter: Counter[str] = Counter()
    for item in items:
        text = " ".join(
            [
                str(getattr(item, "title", "") or ""),
                str(getattr(item, "summary", "") or ""),
            ]
        )
        for kw in extract_keywords(text, top_n=8):
            counter[kw["term"]] += 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return [{"term": t, "documents": c} for t, c in ranked]


def narrative_divergence(groups: dict[str, list[str]]) -> dict[str, object]:
    """Compare keyword sets between two coverage populations.

    This is the anti-bias instrument: it shows which terms a region's own
    outlets emphasise that the global wire does not, and vice versa. Without
    it, a Western-volume-heavy feed set makes every region look like it is
    covered by the same handful of storylines.
    """
    sets: dict[str, set[str]] = {}
    for name, texts in groups.items():
        toks: set[str] = set()
        for t in texts:
            for kw in extract_keywords(t, top_n=15):
                toks.add(kw["term"])
        sets[name] = toks

    names = list(sets)
    divergence: dict[str, object] = {"overlap": {}, "exclusive": {}}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            inter = sets[a] & sets[b]
            union = sets[a] | sets[b]
            key = f"{a}|{b}"
            divergence["overlap"][key] = {
                "shared": len(inter),
                "jaccard": round(len(inter) / len(union), 4) if union else 0.0,
                "examples": sorted(inter)[:10],
            }
            divergence["exclusive"][f"{a}_only"] = sorted(sets[a] - sets[b])[:15]
            divergence["exclusive"][f"{b}_only"] = sorted(sets[b] - sets[a])[:15]
    return divergence
