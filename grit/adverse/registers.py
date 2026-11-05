"""The adverse news scan's registers (spec 46): the watchlist, the lexicon and the scan plan, and the graph they bind with.

Pure apart from reading config files. The lexicon screens; it never decides.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.ontology.graph import Edge, Node, OntologyGraph, merge_graphs

LEAD_CHARS = 400
SECTIONS = ("A", "B", "C")


@dataclass(frozen=True)
class PlannedQuery:
    """The shape collect_plan expects, plus the lane and target the pool records."""

    query_id: str
    query: str
    lane: str
    exact: bool = False
    canonical_lob: str | None = None


@dataclass(frozen=True)
class LexiconHit:
    category: str
    term: str
    where: str  # TITLE, LEAD or BODY


@dataclass
class Lexicon:
    categories: list[dict[str, Any]]
    _patterns: list[tuple[str, str, re.Pattern[str]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for category in self.categories:
            for term in category.get("terms") or []:
                self._patterns.append((str(category["id"]), str(term), _term_pattern(str(term))))

    def label(self, category_id: str) -> str:
        return next((str(c.get("label")) for c in self.categories if c["id"] == category_id), category_id)

    def section_a(self, category_id: str) -> str | None:
        return next((c.get("sectionA") for c in self.categories if c["id"] == category_id), None)

    def hits(self, title: str, text: str) -> list[LexiconHit]:
        """The first match of each term, with where it fell. A body match is recorded and does not make a candidate."""
        whole = f"{title}\n{text}"
        title_end = len(title)
        found: list[LexiconHit] = []
        for category, term, pattern in self._patterns:
            match = pattern.search(whole)
            if not match:
                continue
            where = "TITLE" if match.start() < title_end else "LEAD" if match.start() - title_end - 1 < LEAD_CHARS else "BODY"
            found.append(LexiconHit(category, term, where))
        return found


def _term_pattern(term: str) -> re.Pattern[str]:
    stem = term.endswith("*")
    body = r"\s+".join(re.escape(part) for part in term.rstrip("*").split())
    return re.compile(rf"(?<![\w-]){body}{r'[\w-]*' if stem else ''}(?![\w])", re.IGNORECASE)


@dataclass
class Registers:
    watchlist: dict[str, Any]
    lexicon: Lexicon
    plan: dict[str, Any]

    @property
    def queries(self) -> list[PlannedQuery]:
        return [PlannedQuery(str(q["id"]), str(q["query"]), str(q["lane"])) for q in self.plan.get("queries") or []]

    @property
    def query_limit(self) -> int:
        return int(self.plan.get("queryLimit") or 40)

    def entry(self, node_id: str) -> Mapping[str, Any] | None:
        return next((e for e in self.watchlist.get("entries") or [] if e["id"] == node_id), None)


def adverse_dir(config_dir: Path | None = None) -> Path:
    from grit.settings import CONFIG_DIR

    return (config_dir or CONFIG_DIR) / "adverse"


def load_registers(config_dir: Path | None = None) -> Registers:
    folder = adverse_dir(config_dir)

    def read(name: str) -> dict[str, Any]:
        return json.loads((folder / name).read_text(encoding="utf-8-sig"))

    watchlist = read("Adverse-Watchlist.json")
    lexicon = read("Adverse-Lexicon.json")
    plan = read("Adverse-Scan-Plan.json")
    ids = [e["id"] for e in watchlist.get("entries") or []]
    if len(ids) != len(set(ids)):
        raise ValueError("Adverse-Watchlist.json repeats an id.")
    for entry in watchlist.get("entries") or []:
        if entry.get("cls") not in {"STE", "Site", "Customer", "Supplier", "Org"}:
            raise ValueError(f"Watchlist entry {entry.get('id')} has cls {entry.get('cls')}; use STE, Site, Customer, Supplier or Org.")
    query_ids = [q["id"] for q in plan.get("queries") or []]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("Adverse-Scan-Plan.json repeats a query id.")
    return Registers(watchlist, Lexicon(list(lexicon.get("categories") or [])), plan)


def watchlist_graph(watchlist: Mapping[str, Any]) -> OntologyGraph:
    """The watchlist as graph nodes. Each STE entity and programme is part of the group, so it binds on the DIRECT rung."""
    from grit.ontology.graph import GROUP_ID, VALIDATED

    nodes = [Node(GROUP_ID, "STE", "GROUP", "ST Engineering", ("ST Engineering",), {"ST Engineering": "DERIVED"})]
    edges: list[Edge] = []
    basis = str(watchlist.get("basis") or "adverse watchlist")
    for entry in watchlist.get("entries") or []:
        aliases = tuple(dict.fromkeys(str(a) for a in [*(entry.get("aliases") or []), entry["name"]]))
        attrs = {key: entry[key] for key in ("businessArea", "relationship") if entry.get(key)}
        nodes.append(Node(str(entry["id"]), str(entry["cls"]), str(entry.get("kind") or entry["cls"]).upper(), str(entry["name"]),
                          aliases, {alias: "WATCHLIST" for alias in aliases}, attrs))
        if entry["cls"] == "STE":
            edges.append(Edge(str(entry["id"]), "part_of", GROUP_ID, VALIDATED, basis, str(watchlist.get("recordedBy") or "")))
    return OntologyGraph(nodes, edges)


def group_graph(registers: Registers) -> tuple[OntologyGraph, dict[str, Any]]:
    """The Group pack's merged business-area graph with the watchlist added, and the joined LOB profiles."""
    from grit.pipeline.reader_scan import _load_pack, graph_pack, pack_graph

    ste = _load_pack("ste")
    graph = merge_graphs([pack_graph(ste), watchlist_graph(registers.watchlist)], themes=(ste.themes or {}).get("themes") or [])
    return graph, graph_pack(ste).lob_profiles


def business_areas(graph: OntologyGraph, node_id: str) -> list[str]:
    """The business areas a registered name belongs to or deals with, from the register edges or the watchlist."""
    node = graph.nodes.get(node_id)
    if node is None:
        return []
    found: list[str] = []
    if node.attrs.get("businessArea"):
        found.append(str(node.attrs["businessArea"]))
    try:
        reaches = graph.reach(node_id)
    except KeyError:
        reaches = []
    for reach in reaches:
        for target in reach.targets:
            target_node = graph.nodes[target]
            if target_node.kind == "BUSINESS_AREA":
                found.append(target_node.name)
            if target_node.attrs.get("businessArea"):
                found.append(str(target_node.attrs["businessArea"]))
            for _edge, parent, forward in graph.neighbours(target, "part_of"):
                if forward and parent.kind == "BUSINESS_AREA":
                    found.append(parent.name)
    return list(dict.fromkeys(found))


def anchors(graph: OntologyGraph, lob_profiles: Mapping[str, Any]) -> list[str]:
    """Names a Section C connection may cite: business areas, lines of business and the group."""
    names = ["ST Engineering"]
    names += [node.name for node in graph.nodes.values() if node.kind == "BUSINESS_AREA"]
    names += ["Defence & Public Security", "Commercial Aerospace", "Urban Solutions", "Digital Systems & Cybersecurity"]
    names += [str(lob.get("lobName")) for lob in lob_profiles.get("lobs") or [] if lob.get("lobName")]
    return list(dict.fromkeys(names))


def query_lanes(plan_queries: Sequence[PlannedQuery]) -> dict[str, str]:
    return {q.query_id: q.lane for q in plan_queries}
