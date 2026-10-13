"""The ontology context for one finding. Pure: it reads the graph, the link, the vector pool, and the theme register. No model, no network.

The model sees only this context. Every entity id, path id, evidence id, and theme id it may cite is defined here,
so a card can be checked against it. The context carries no URL: a URL is added when the card is rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from grit.core.doc_type import NON_EVENT_TYPES, classify
from grit.ontology.link import LinkIndex
from grit.ontology.graph import STRENGTH, OntologyGraph, Reach
from grit.core.priority import WIRE_OUTLET, outlet_groups
from grit.retrieval.similarity import dot

MAX_TEXT = 2000
MAX_EVIDENCE_TEXT = 600
MAX_PATHS = 8
MAX_PER_START = 3
MAX_ENTITIES = 12
MAX_OUTLET_EVIDENCE = 4
MAX_REGISTER_FACTS = 12
MAX_FACTS_PER_PATH = 3
SCOPES = ("ENTITY", "LOB", "UNIT", "BUSINESS_AREA", "GROUP", "NONE")  # most specific first. LOB means the register ties the story to a line of business and to no entity.
START_ORDER = {"STE": 0, "Site": 0, "Customer": 1, "Supplier": 1, "Org": 2, "LOB": 3}  # a country is never a start: it links only through a LOB
NAMED = "NAMED"
PLACE_AND_LOB = "PLACE_AND_LOB"
PATH_ONLY = "PATH_ONLY"
SELECTABLE = frozenset({NAMED, PLACE_AND_LOB})  # the model may pick an entity only when the text or a named place supports it
_STE_KIND_SCOPE = {"ENTITY": "ENTITY", "UNIT": "UNIT", "BUSINESS_AREA": "BUSINESS_AREA", "GROUP": "GROUP"}


@dataclass
class Context:
    finding_id: str
    signal: dict[str, Any]
    view: dict[str, Any]  # what the model is sent
    entities: dict[str, dict[str, Any]]
    entities_total: int
    paths: dict[str, dict[str, Any]]
    evidence: dict[str, dict[str, Any]]
    themes: dict[str, dict[str, Any]]
    scope: str
    gaps: list[str]
    signal_text: str
    strong_nodes: set[str] = field(default_factory=set)
    signal_group: str = ""
    seen_urls: set[str] = field(default_factory=set)
    business_names: tuple[str, ...] = ("Urban Solutions", "URS")  # the business area's name and short label

    def independent_outlets(self) -> int:
        return len({item["group"] for item in self.evidence.values() if item["kind"] in {"OUTLET", "FOLLOW_UP"} and item["independent"]})

    def connection_support(self) -> int:
        return len({item["group"] for item in self.evidence.values() if item["kind"] in {"OUTLET", "FOLLOW_UP"} and item["independent"] and item["names_node"]})

    def register_facts(self) -> int:
        return sum(1 for item in self.evidence.values() if item["kind"] == "REGISTER_FACT")


def _date(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def render_path(graph: OntologyGraph, start: str, reach: Reach) -> str:
    parts = [graph.nodes[start].name]
    for position, step in enumerate(reach.steps):
        edge = step.edge
        nxt = edge.dst if step.forward else edge.src
        label = graph.nodes[nxt].name
        if position == len(reach.steps) - 1 and len(reach.targets) > 1:
            label = f"{len(reach.targets)} STE nodes, such as {graph.nodes[reach.targets[0]].name}"
        parts.append(f"—{edge.rel}→ {label}" if step.forward else f"←{edge.rel}— {label}")
    return " ".join(parts) if reach.steps else f"{parts[0]} (an STE node)"


def _scope_of(graph: OntologyGraph, reaches: Sequence[Reach], entities: Mapping[str, Mapping[str, Any]]) -> str:
    """The most specific level at which the register ties the story to STE."""
    if any(item["why"] in SELECTABLE for item in entities.values()):
        return "ENTITY"
    if any(graph.nodes[node].kind == "LOB" for reach in reaches for step in reach.steps for node in (step.edge.src, step.edge.dst)):
        return "LOB"
    present = {_STE_KIND_SCOPE[graph.nodes[target].kind] for reach in reaches for target in reach.targets if graph.nodes[target].kind in _STE_KIND_SCOPE}
    return next((scope for scope in SCOPES[2:] if scope in present), "NONE")


def _place_and_lob_path(graph: OntologyGraph, entity_id: str, place_id: str, lob_id: str) -> dict[str, Any] | None:
    """The two register edges that put an entity in a named country and in the story's LOB or grouping."""
    located = next((edge for edge, node, forward in graph.neighbours(entity_id, "located_in") if forward and node.id == place_id), None)
    homes = {lob_id} | {node.id for edge, node, forward in graph.neighbours(lob_id, "part_of") if forward}
    operates = next((edge for edge, node, forward in graph.neighbours(entity_id, "operates_in") if forward and node.id in homes), None)
    if located is None or operates is None:
        return None
    edges = [located, operates]
    return {
        "text": f"{graph.nodes[place_id].name} ←located_in— {graph.nodes[entity_id].name} —operates_in→ {graph.nodes[operates.dst].name}",
        "status": min((edge.status for edge in edges), key=STRENGTH.__getitem__), "hops": 2, "reaches": ["ENTITY"], "targets_total": 1, "targets": [entity_id],
        "edges": [{"src": edge.src, "rel": edge.rel, "dst": edge.dst, "status": edge.status, "basis": edge.basis} for edge in edges],
    }


def add_outlet_evidence(
    context: Context,
    pool: Sequence[Mapping[str, Any]],
    vectors: Mapping[str, Sequence[float]],
    index: LinkIndex,
    *,
    same_event: float,
    kind: str,
    window_days: int = 7,
    limit: int = MAX_OUTLET_EVIDENCE,
) -> int:
    """Add pages that carry the same event from another outlet, by cosine to the signal. names_node records whether the page also names the party. Returns how many were added."""
    signal = context.signal
    own = vectors.get(signal["url"])
    if own is None:
        return 0
    when = _date(signal.get("published_at"))
    found: dict[str, dict[str, Any]] = {}
    for candidate in pool:
        url = candidate["url"]
        vector = vectors.get(url)
        if url == signal["url"] or url in context.seen_urls or vector is None or classify(candidate).doc_type in NON_EVENT_TYPES:
            continue
        group = (outlet_groups([candidate.get("publisher")]) or [""])[0]
        if not group or group == context.signal_group:
            continue
        score = dot(own, vector)
        other = _date(candidate.get("published_at"))
        if when and other and abs((when - other).total_seconds()) > window_days * 86400:
            continue
        text = f"{candidate.get('title') or ''}\n{candidate.get('text') or ''}"
        names = sorted({item.node_id for item in index.involvements(candidate.get("title") or "", candidate.get("text") or "") if item.strong and item.node_id in context.strong_nodes})
        if score < same_event:
            continue  # a page that only names the same party is another story, and it shows nothing about this event
        record = {
            "kind": kind, "publisher": candidate.get("publisher"), "group": group, "published_at": candidate.get("published_at"), "url": url,
            "text": text.strip()[:MAX_EVIDENCE_TEXT], "cosine": round(score, 3), "names_node": names, "independent": True, "reason": "SAME_EVENT",
        }
        best = found.get(group)
        if best is None or (bool(names), score) > (bool(best["names_node"]), best["cosine"]):
            found[group] = record
    chosen = sorted(found.values(), key=lambda item: (not item["names_node"], -item["cosine"], item["url"]))[:limit]
    taken = len([key for key in context.evidence if key.startswith("E")])
    for number, record in enumerate(chosen, start=taken + 1):
        context.evidence[f"E{number}"] = record
        context.seen_urls.add(record["url"])
    _refresh_view(context)
    return len(chosen)


def _refresh_view(context: Context) -> None:
    context.view["evidence"] = [
        {"evidence_id": key, **{name: value for name, value in item.items() if name not in {"url", "group", "names_node", "reason", "independent", "cosine"}},
         "independent_of_the_signal": item["independent"], "names_the_same_node": bool(item["names_node"]), "kind": item["kind"]}
        for key, item in context.evidence.items()
    ]
    context.view["evidence_summary"] = {
        "independent_outlets": context.independent_outlets(),
        "outlets_naming_the_same_node": context.connection_support(),
        "register_facts": context.register_facts(),
    }


def _register_details(graph: OntologyGraph, node_id: str) -> dict[str, Any]:
    """What the register states about a named party, so the model can judge whether the story is about the same organisation."""
    node = graph.nodes[node_id]
    details: dict[str, Any] = {"register_name": node.name}
    if node.cls == "STE" and node.attrs.get("country"):
        details |= {"country_of_incorporation": node.attrs["country"], "lob_label": node.attrs.get("lobLabel")}
    units = [other.name for edge, other, forward in graph.neighbours(node_id, "supplies") if forward]
    if units:
        details["supplies_unit"] = units
    if node.cls == "Customer":
        details["country"] = "not stated in the register"
    if node.cls == "Org":
        details["competes_in"] = [other.name for edge, other, forward in graph.neighbours(node_id, "competes_in") if forward]
    return details


def build_context(
    finding_id: str,
    signal: Mapping[str, Any],
    *,
    graph: OntologyGraph,
    index: LinkIndex,
    themes: Sequence[Mapping[str, Any]],
    pool: Sequence[Mapping[str, Any]],
    vectors: Mapping[str, Sequence[float]],
    same_event: float,
    business_names: Sequence[str] = ("Urban Solutions", "URS"),
) -> Context:
    link = signal["link"]
    involves = link["involves"]
    strong = [item for item in involves if item["strong"]]
    starts: list[str] = [item["node_id"] for item in strong if item["cls"] in START_ORDER]
    if link.get("lob_id"):
        starts.append(link["lob_id"])
    starts = sorted(dict.fromkeys(starts), key=lambda node_id: (START_ORDER[graph.nodes[node_id].cls], node_id))

    paths: dict[str, dict[str, Any]] = {}
    reaches: list[Reach] = []
    ordered: list[tuple[str, Reach]] = []
    for start in starts:
        found = graph.reach(start)
        reaches.extend(found)
        ordered.extend((start, reach) for reach in found[:MAX_PER_START])
    for start, reach in ordered[:MAX_PATHS]:
        path_id = f"P{len(paths) + 1}"
        kinds = sorted({graph.nodes[target].kind for target in reach.targets})
        paths[path_id] = {
            "path_id": path_id, "start": {"id": start, "name": graph.nodes[start].name, "class": graph.nodes[start].cls},
            "text": render_path(graph, start, reach), "status": reach.weakest, "hops": len(reach.steps),
            "reaches": kinds, "targets_total": len(reach.targets), "targets": list(reach.targets),
            "edges": [{"src": step.edge.src, "rel": step.edge.rel, "dst": step.edge.dst, "status": step.edge.status, "basis": step.edge.basis} for step in reach.steps],
        }

    named = {item["node_id"] for item in strong if item["cls"] in {"STE", "Site"}}
    places = [item["node_id"] for item in strong if item["cls"] == "Place"]
    lob_entities = graph.entities_in_lob(link["lob_id"]) if link.get("lob_id") else set()
    perimeter = {entity for place in places for entity in graph.entities_in_place(place)} & lob_entities
    for place in places:
        for entity_id in sorted(graph.entities_in_place(place) & lob_entities)[:MAX_PER_START]:
            built = _place_and_lob_path(graph, entity_id, place, link["lob_id"])
            if built and len(paths) < MAX_PATHS + MAX_PER_START:
                path_id = f"P{len(paths) + 1}"
                paths[path_id] = {"path_id": path_id, "start": {"id": place, "name": graph.nodes[place].name, "class": "Place"}, **built}
    reached = {target: path_id for path_id, path in paths.items() for target in path["targets"] if graph.nodes[target].kind == "ENTITY"}
    entities: dict[str, dict[str, Any]] = {}
    for entity_id in sorted(set(reached) | named | perimeter):
        node = graph.nodes[entity_id]
        if node.kind != "ENTITY":
            continue
        why = NAMED if entity_id in named else PLACE_AND_LOB if entity_id in perimeter else PATH_ONLY
        entities[entity_id] = {"entity_id": entity_id, "name": node.name, "country": node.attrs.get("country"), "lob_label": node.attrs.get("lobLabel"), "why": why, "path_id": reached.get(entity_id)}
    ranked = sorted(entities.values(), key=lambda item: ({NAMED: 0, PLACE_AND_LOB: 1, PATH_ONLY: 2}[item["why"]], item["name"]))
    total = len(ranked)
    shown = {item["entity_id"]: item for item in ranked[:MAX_ENTITIES]}

    gaps: list[str] = []
    classes = {graph.nodes[start].cls for start in starts}
    if "Customer" in classes:
        gaps.append("The register does not say which STE entity or line of business this customer buys from.")
    if "Supplier" in classes:
        gaps.append("The register names this supplier's unit, not the STE entity it supplies.")
    if "Org" in classes and not any(path["reaches"] for path in paths.values()):
        gaps.append("This competitor's line of business matches no org chart LOB in the register.")
    unresolved = [gap for gap in graph.gaps if gap["kind"] == "ENTITY_LOB_UNRESOLVED" and set(gap.get("ids") or []) & set(shown)]
    if unresolved:
        gaps.append("Some candidate entities carry a LOB label that names no single org chart LOB, so the register places them by grouping only.")

    theme_rows = {row["themeId"]: row for row in themes}
    hit_themes = {}
    for hit in signal["link"].get("themes") or []:
        row = theme_rows.get(hit["theme_id"])
        if row:
            hit_themes[hit["theme_id"]] = {
                "theme_id": row["themeId"], "tier": row["tier"], "name": row["themeName"], "matched": hit["matched"], "where": hit["where"], "strong": hit["strong"],
                "register_detail": row.get("sheetDetail"),
            }
    text = (signal.get("text") or "")[:MAX_TEXT]
    group = (outlet_groups([signal.get("publisher")]) or [""])[0]
    priority = signal.get("priority") or {}
    context = Context(
        finding_id=finding_id, signal=dict(signal), view={}, entities=shown, entities_total=total, paths=paths, evidence={}, themes=hit_themes,
        scope=_scope_of(graph, reaches, entities), gaps=gaps, signal_text=f"{signal['title']}\n{text}", strong_nodes={item["node_id"] for item in strong if item["cls"] != "Place"},
        signal_group=group, seen_urls={signal["url"]}, business_names=tuple(name for name in business_names if name),
    )
    seen_facts: set[tuple[str, str, str]] = set()
    for path in paths.values():
        added = 0  # a cap per path, so a later path is not starved by an earlier one
        for edge in path["edges"]:
            triple = (edge["src"], edge["rel"], edge["dst"])
            if edge["status"] in {"SOURCE_STATED", "VALIDATED"} and triple not in seen_facts and added < MAX_FACTS_PER_PATH and len(seen_facts) < MAX_REGISTER_FACTS:
                seen_facts.add(triple)
                added += 1
                context.evidence[f"R{len(seen_facts)}"] = {
                    "kind": "REGISTER_FACT", "publisher": "STE register", "group": "register", "published_at": None, "url": None, "cosine": None,
                    "text": f"{graph.nodes[edge['src']].name} {edge['rel']} {graph.nodes[edge['dst']].name}. Source: {edge['basis'].rstrip('.')}.", "names_node": [], "independent": True, "reason": edge["status"],
                    "edge": triple,
                }
    context.view = {
        "finding_id": finding_id,
        "signal": {"title": signal["title"], "publisher": signal.get("publisher"), "published_at": signal.get("published_at"), "text": text, "document_type": signal.get("doc_type")},
        "why_it_ranks": {"tier": priority.get("tier"), "big_event": priority.get("big"), "reasons": priority.get("reasons") or []},
        "link": {"rung": signal.get("rung"), "lob": link.get("canonical_lob"), "reasons": link.get("reasons") or [], "named": [
            {"node_id": item["node_id"], "name": item["name"], "class": item["cls"], "matched": item["matched"], "where": item["where"], "alias_basis": item["alias_basis"], "ambiguous": item["ambiguous"],
             "register_details": _register_details(graph, item["node_id"])}
            for item in strong if item["cls"] != "Place"]},
        "register_scope": context.scope,
        "candidate_entities": [{k: v for k, v in item.items() if k != "path_id"} | {"selectable": item["why"] in SELECTABLE} for item in shown.values()],
        "candidate_entities_total": total,
        "paths": [{key: path[key] for key in ("path_id", "text", "status", "hops", "reaches", "targets_total")} for path in paths.values()],
        "known_gaps": gaps,
        "themes_hit": list(hit_themes.values()),
        "all_themes": [{"theme_id": row["themeId"], "tier": row["tier"], "name": row["themeName"]} for row in themes],
    }
    add_outlet_evidence(context, pool, vectors, index, same_event=same_event, kind="OUTLET")
    _refresh_view(context)
    return context


def best_status(context: Context) -> int:
    """The strength of the best path: its weakest edge, compared across paths. 0 when no path reaches an STE node."""
    levels = [STRENGTH[path["status"]] for path in context.paths.values() if path["targets"]]
    return max(levels, default=0)


def normalise_text(value: str) -> str:
    """For a verbatim check: unify quotes, dashes, and spaces, and ignore case. The check also needs a minimum length: see grit/agents/loop/card.py."""
    value = value.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "\u00a0": " "}))
    return re.sub(r"\s+", " ", value).strip().casefold()
