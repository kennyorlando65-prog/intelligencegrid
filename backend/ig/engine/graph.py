"""Entity and relationship graph.

Built as an adjacency structure over heterogeneous node types: countries,
organisations, people, locations, vessels, aircraft, sanctioned entities,
CVEs and news stories all live in one graph. Two design choices matter:

* **Edges carry provenance.** An edge is not just "A relates to B"; it is
  "A relates to B because these N documents said so", with the evidence
  retained. An unattributed edge in an intelligence graph is an assertion
  with no way to audit it.

* **Co-occurrence is a candidate signal, not a fact.** Two names appearing
  in the same article might be genuinely related, or the article might just
  be a roundup. The graph therefore weights edges by both frequency and
  source diversity -- a connection asserted by twelve independent outlets is
  far stronger than one asserted twelve times by a single wire.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..models import Evidence


@dataclass
class Node:
    """One entity in the graph."""

    id: str
    label: str
    kind: str  # country | org | person | location | vessel | aircraft | sanction | cve | story
    meta: dict[str, Any] = field(default_factory=dict)
    degree: int = 0
    weight: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "degree": self.degree,
            "weight": round(self.weight, 3),
            "meta": self.meta,
        }


@dataclass
class Edge:
    """A weighted relationship, always carrying its evidence."""

    source: str
    target: str
    relation: str  # co_occurs | sanctioned_by | exploits | located_in | mentions
    weight: float = 1.0
    distinct_sources: int = 1
    occurrences: int = 1
    evidence: list[Evidence] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "weight": round(self.weight, 3),
            "distinct_sources": self.distinct_sources,
            "occurrences": self.occurrences,
            "evidence": [e.model_dump(mode="json") for e in self.evidence[:5]],
        }


class RelationshipGraph:
    """Sparse graph with provenance-carrying edges.

    The edge key includes the relation type, so ``A co_occurs B`` and
    ``A sanctioned_by B`` are distinct edges between the same pair rather
    than one edge with ambiguous meaning.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}
        self._adjacency: dict[str, set[str]] = defaultdict(set)

    # -- construction ------------------------------------------------------

    def add_node(
        self,
        node_id: str,
        label: str,
        kind: str,
        meta: dict[str, Any] | None = None,
    ) -> Node:
        existing = self.nodes.get(node_id)
        if existing is not None:
            if meta:
                existing.meta.update(meta)
            return existing
        node = Node(id=node_id, label=label, kind=kind, meta=meta or {})
        self.nodes[node_id] = node
        return node

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        *,
        evidence: Evidence | None = None,
        weight: float = 1.0,
        source_name: str = "",
    ) -> Edge:
        """Add or reinforce an edge.

        Source diversity is tracked separately from raw occurrence count so
        a single outlet repeating a claim cannot manufacture a strong link.
        """
        if source == target:
            # Self-loops carry no relational information here.
            raise ValueError("self-edges are not meaningful in this graph")
        key = (source, target, relation)
        edge = self._edges.get(key)
        if edge is None:
            edge = Edge(source=source, target=target, relation=relation, weight=weight)
            self._edges[key] = edge
            self._adjacency[source].add(target)
            self._adjacency[target].add(source)
        else:
            edge.occurrences += 1
        if evidence is not None:
            edge.evidence.append(evidence)
            names = {e.source_name for e in edge.evidence}
            edge.distinct_sources = max(1, len(names))
        elif source_name:
            names = {e.source_name for e in edge.evidence} | {source_name}
            edge.distinct_sources = max(1, len(names))
        edge.weight = _edge_weight(edge)
        return edge

    # -- queries -----------------------------------------------------------

    def neighbours(self, node_id: str) -> list[str]:
        return sorted(self._adjacency.get(node_id, set()))

    def edges_of(self, node_id: str) -> list[Edge]:
        return [
            e
            for e in self._edges.values()
            if e.source == node_id or e.target == node_id
        ]

    def all_edges(self) -> list[Edge]:
        return list(self._edges.values())

    def top_edges(self, n: int = 50) -> list[Edge]:
        return sorted(self._edges.values(), key=lambda e: -e.weight)[:n]

    def top_nodes(self, n: int = 30, kind: str | None = None) -> list[Node]:
        nodes = [x for x in self.nodes.values() if kind is None or x.kind == kind]
        return sorted(nodes, key=lambda x: (-x.weight, x.label))[:n]

    def subgraph(self, node_ids: Iterable[str], depth: int = 1) -> RelationshipGraph:
        """Extract a neighbourhood, expanding ``depth`` hops from the seeds."""
        seeds = {n for n in node_ids if n in self.nodes}
        frontier = set(seeds)
        included = set(seeds)
        for _ in range(max(1, depth)):
            nxt: set[str] = set()
            for node in frontier:
                for nb in self._adjacency.get(node, set()):
                    if nb not in included:
                        nxt.add(nb)
            included |= nxt
            frontier = nxt
        out = RelationshipGraph()
        for nid in included:
            node = self.nodes[nid]
            out.add_node(nid, node.label, node.kind, dict(node.meta))
        for edge in self._edges.values():
            if edge.source in included and edge.target in included:
                out._edges[(edge.source, edge.target, edge.relation)] = edge
                out._adjacency[edge.source].add(edge.target)
                out._adjacency[edge.target].add(edge.source)
        out.recompute_degrees()
        return out

    def recompute_degrees(self) -> None:
        for node in self.nodes.values():
            node.degree = len(self._adjacency.get(node.id, set()))
            node.weight = (
                sum(e.weight for e in self.edges_of(node.id)) if node.degree else 0.0
            )

    def communities(self, min_size: int = 3) -> list[list[str]]:
        """Connected components above a size floor.

        Components, not modularity-optimised communities: on this graph size
        a component is the honest unit, and claiming fine-grained community
        structure from co-occurrence alone would overstate what the data
        supports.
        """
        seen: set[str] = set()
        out: list[list[str]] = []
        for node_id in self.nodes:
            if node_id in seen:
                continue
            stack = [node_id]
            comp: list[str] = []
            seen.add(node_id)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nb in self._adjacency.get(cur, set()):
                    if nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            if len(comp) >= min_size:
                out.append(sorted(comp))
        out.sort(key=len, reverse=True)
        return out

    def stats(self) -> dict[str, Any]:
        by_kind: dict[str, int] = defaultdict(int)
        for node in self.nodes.values():
            by_kind[node.kind] += 1
        by_relation: dict[str, int] = defaultdict(int)
        for edge in self._edges.values():
            by_relation[edge.relation] += 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self._edges),
            "nodes_by_kind": dict(by_kind),
            "edges_by_relation": dict(by_relation),
            "components": len(self.communities()),
        }

    def as_payload(self, *, nodes: int = 60, edges: int = 120) -> dict[str, Any]:
        """Serialisable slice for the frontend graph view."""
        keep_nodes = {n.id for n in self.top_nodes(nodes)}
        out_edges = [
            e.as_dict()
            for e in sorted(self._edges.values(), key=lambda x: -x.weight)
            if e.source in keep_nodes and e.target in keep_nodes
        ][:edges]
        return {
            "nodes": [
                self.nodes[nid].as_dict() for nid in keep_nodes if nid in self.nodes
            ],
            "edges": out_edges,
            "stats": self.stats(),
        }


def _edge_weight(edge: Edge) -> float:
    """Combine frequency with source diversity, sub-linearly.

    Log-scaled so a story repeated two hundred times does not drown out a
    genuinely multi-source connection. Diversity is the dominant term.
    """
    import math

    freq = math.log1p(edge.occurrences)
    diversity = math.log1p(edge.distinct_sources) * 2.0
    return round(freq + diversity, 4)


# -- graph builders ---------------------------------------------------------


def build_news_graph(
    items: Iterable[Any],
    *,
    country_lookup: dict[str, str] | None = None,
    max_items: int = 300,
) -> RelationshipGraph:
    """Build a story/country/entity graph from news items."""
    from .nlp import extract_entities

    graph = RelationshipGraph()
    country_lookup = country_lookup or {}

    for item in list(items)[:max_items]:
        story_id = f"story:{item.id}"
        graph.add_node(
            story_id,
            item.title[:90] or item.url,
            "story",
            {
                "url": item.url,
                "source": item.source_name,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "sentiment": round(getattr(item, "sentiment", 0.0), 3),
            },
        )
        evidence = Evidence(
            source_url=item.url,
            source_name=item.source_name,
            published_at=item.published_at,
            excerpt=(item.title or "")[:200],
        )

        for iso in getattr(item, "countries", []) or []:
            cid = f"country:{iso}"
            graph.add_node(cid, country_lookup.get(iso, iso), "country", {"iso2": iso})
            graph.add_edge(story_id, cid, "mentions", evidence=evidence)

        entities = extract_entities(f"{item.title} {getattr(item, 'summary', '')}")
        for kind, key in (("org", "orgs"), ("person", "people"), ("location", "places")):
            for name in entities.get(key, [])[:8]:
                eid = f"{kind}:{name.lower()}"
                graph.add_node(eid, name, kind)
                graph.add_edge(story_id, eid, "mentions", evidence=evidence)

    graph.recompute_degrees()
    return graph


def build_sanctions_graph(
    sanctions: Iterable[Any],
    *,
    country_lookup: dict[str, str] | None = None,
    max_items: int = 400,
) -> RelationshipGraph:
    """Build an entity/country/program graph from sanctions records."""
    graph = RelationshipGraph()
    country_lookup = country_lookup or {}

    for item in list(sanctions)[:max_items]:
        sid = f"sanction:{item.id}"
        graph.add_node(
            sid,
            item.name,
            "sanction",
            {
                "entity_type": item.entity_type,
                "list": item.list_source,
                "aliases": (item.aliases or [])[:5],
            },
        )
        evidence = Evidence(
            source_url=item.source_url,
            source_name=item.list_source or "sanctions list",
            excerpt=f"{item.name} listed by {item.list_source}",
        )
        for iso in item.countries or []:
            cid = f"country:{iso}"
            graph.add_node(cid, country_lookup.get(iso, iso), "country", {"iso2": iso})
            graph.add_edge(cid, sid, "sanctioned_by", evidence=evidence)
        for program in (item.programs or [])[:5]:
            pid = f"program:{program.lower()}"
            graph.add_node(pid, program, "org", {"type": "sanctions_program"})
            graph.add_edge(sid, pid, "listed_under", evidence=evidence)

    graph.recompute_degrees()
    return graph


def build_cyber_graph(cves: Iterable[Any], max_items: int = 300) -> RelationshipGraph:
    """Vendor/product/exploitation graph from vulnerability records."""
    graph = RelationshipGraph()
    for item in list(cves)[:max_items]:
        cid = f"cve:{item.cve_id}"
        graph.add_node(
            cid,
            item.cve_id,
            "cve",
            {
                "cvss": item.cvss_score,
                "in_kev": item.in_kev,
                "ransomware": item.ransomware_used,
                "severity": item.severity.value,
            },
        )
        evidence = Evidence(
            source_url=item.source_url,
            source_name="CISA KEV" if item.in_kev else "NVD",
            excerpt=item.title or item.cve_id,
        )
        if item.vendor:
            vid = f"org:{item.vendor.lower()}"
            graph.add_node(vid, item.vendor, "org", {"type": "vendor"})
            graph.add_edge(vid, cid, "affected_by", evidence=evidence)
        if item.product:
            pid = f"product:{item.product.lower()}"
            graph.add_node(pid, item.product, "org", {"type": "product"})
            graph.add_edge(pid, cid, "affected_by", evidence=evidence)
        if item.in_kev:
            graph.add_node("org:cisa", "CISA", "org", {"type": "agency"})
            graph.add_edge("org:cisa", cid, "exploits", evidence=evidence)
    graph.recompute_degrees()
    return graph


def merge_graphs(*graphs: RelationshipGraph) -> RelationshipGraph:
    """Union several graphs, summing shared edge evidence."""
    out = RelationshipGraph()
    for g in graphs:
        for node in g.nodes.values():
            out.add_node(node.id, node.label, node.kind, dict(node.meta))
        for edge in g._edges.values():
            key = (edge.source, edge.target, edge.relation)
            existing = out._edges.get(key)
            if existing is None:
                new_edge = Edge(
                    source=edge.source,
                    target=edge.target,
                    relation=edge.relation,
                    weight=edge.weight,
                    distinct_sources=edge.distinct_sources,
                    occurrences=edge.occurrences,
                    evidence=list(edge.evidence),
                )
                out._edges[key] = new_edge
                out._adjacency[edge.source].add(edge.target)
                out._adjacency[edge.target].add(edge.source)
            else:
                existing.occurrences += edge.occurrences
                existing.evidence.extend(edge.evidence)
                existing.distinct_sources = max(
                    existing.distinct_sources, edge.distinct_sources
                )
                existing.weight = _edge_weight(existing)
    out.recompute_degrees()
    return out
