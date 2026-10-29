"""Typed ontology graph over the pack's registers. Pure: no network, no model.

Classes and relations follow docs/design/schema.yaml. part_of, buys_from and competes_in are additions to its relation list,
and Theme is an addition to its class list. Every edge carries a status and a basis, so a path can be audited:

    SOURCE_STATED  the workbook says it.
    DERIVED        a named rule computed it from stated facts.
    PROPOSED       a hand-authored overlay row. It is not validated until a named human approves it.
    VALIDATED      an overlay row with an approver and a date.

The graph never invents an edge. A fact the workbook lacks is listed in gaps.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from grit.ontology.lob_hierarchy import resolve_lob
from grit.ontology.registry_loader import slugify

SOURCE_STATED = "SOURCE_STATED"
DERIVED = "DERIVED"
PROPOSED = "PROPOSED"
VALIDATED = "VALIDATED"
STRENGTH = {SOURCE_STATED: 3, VALIDATED: 3, DERIVED: 2, PROPOSED: 1}
RELATIONS = frozenset({"part_of", "operates_in", "located_in", "supplies", "buys_from", "competes_in", "focuses_on"})
TRANSIT_CLASSES = frozenset({"LOB", "Place"})  # a path may pass through these; a customer, a competitor, or a request is only a start
GROUP_ID = "STE-GROUP"
MAX_HOPS = 3

SHEET_ENTITIES = "2. Entity Address"
SHEET_CUSTOMERS = "4. Customers"
SHEET_SUPPLIERS = "5. Suppliers"
SHEET_COMPETITORS = "3. Competitors"
SHEET_MANAGEMENT = "10. Management Focus"

_LEGAL_TOKENS = frozenset(
    {"pte", "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation", "co", "company", "plc", "gmbh", "spa", "sa", "ag", "bv",
     "sdn", "bhd", "pty", "public", "branch", "office", "pcl", "lp", "llp", "nv", "oy", "ab", "se", "ltda"}
)
_GENERIC_SINGLE = frozenset(
    {"electric", "group", "technology", "technologies", "systems", "solutions", "services", "international", "engineering", "industries",
     "automation", "motors", "energy", "digital", "asia", "singapore", "global", "holdings", "logistics", "transport", "infrastructure",
     "networks", "security", "mobility", "smart", "power", "water", "rail", "road", "traffic", "management", "software", "electronics"}
)


@dataclass(frozen=True)
class Node:
    id: str
    cls: str  # schema.yaml class: STE, LOB, Customer, Supplier, Org, Place, Site, ManagementRequest, or Theme
    kind: str
    name: str
    aliases: tuple[str, ...] = ()
    alias_basis: Mapping[str, str] = field(default_factory=dict)
    attrs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    src: str
    rel: str
    dst: str
    status: str
    basis: str
    approved_by: str | None = None


@dataclass(frozen=True)
class Step:
    edge: Edge
    forward: bool  # True when the walk follows the edge from src to dst


@dataclass(frozen=True)
class Reach:
    """STE nodes reached from one start node through one chain of relations. targets share the last hop."""

    start: str
    steps: tuple[Step, ...]
    targets: tuple[str, ...]

    @property
    def weakest(self) -> str:
        if not self.steps:
            return SOURCE_STATED
        return min((step.edge.status for step in self.steps), key=STRENGTH.__getitem__)


def short_names(name: str, places: Iterable[str] = ()) -> list[str]:
    """Names a news item may use for a registered name: no brackets, no legal suffix, no trailing country. Guarded against generic words."""
    text = re.sub(r"\([^)]*\)", " ", name)
    tokens = [token for token in re.split(r"[\s,]+", text) if token]
    place_tokens = [place.casefold().split() for place in places]
    while tokens:
        bare = re.sub(r"[.]", "", tokens[-1]).casefold()
        if not re.search(r"\w", tokens[-1]) or bare in _LEGAL_TOKENS:
            tokens.pop()
            continue
        trimmed = next((tokens[: -len(parts)] for parts in place_tokens if len(parts) < len(tokens) and [t.casefold() for t in tokens[-len(parts):]] == parts), None)
        if trimmed is None:
            break
        tokens = trimmed
    candidate = " ".join(tokens).strip(" ,.-/")
    if not candidate or candidate.casefold() == name.casefold():
        return []
    return [candidate] if usable_name(candidate) else []


def usable_name(name: str) -> bool:
    words = name.split()
    if len(words) >= 2:
        return len(name) >= 6 and not all(word.casefold() in _GENERIC_SINGLE for word in words)
    word = words[0] if words else ""
    if word.isupper() and 3 <= len(word) <= 4 and word.isalpha():
        return True
    return len(word) >= 5 and word.casefold() not in _GENERIC_SINGLE


def _split_names(name: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s+/\s+", name) if part.strip()]


class OntologyGraph:
    def __init__(self, nodes: Iterable[Node], edges: Iterable[Edge], gaps: Iterable[Mapping[str, Any]] = ()):
        self.nodes: dict[str, Node] = {}
        for node in nodes:
            if node.id in self.nodes:
                raise ValueError(f"Duplicate ontology node id: {node.id}")
            self.nodes[node.id] = node
        self.edges: tuple[Edge, ...] = tuple(edges)
        self.gaps: tuple[Mapping[str, Any], ...] = tuple(gaps)
        self._adjacent: dict[str, list[tuple[Edge, str, bool]]] = defaultdict(list)
        for edge in self.edges:
            if edge.src not in self.nodes or edge.dst not in self.nodes:
                raise ValueError(f"Edge {edge.src} {edge.rel} {edge.dst} names a node that is not in the graph.")
            if edge.rel not in RELATIONS:
                raise ValueError(f"Edge {edge.src} {edge.rel} {edge.dst} uses an unknown relation.")
            self._adjacent[edge.src].append((edge, edge.dst, True))
            self._adjacent[edge.dst].append((edge, edge.src, False))

    def has_edge(self, src: str, rel: str, dst: str) -> bool:
        return any(edge.rel == rel and neighbour == dst and forward for edge, neighbour, forward in self._adjacent.get(src, ()))

    def of_class(self, cls: str) -> list[Node]:
        return [node for node in self.nodes.values() if node.cls == cls]

    def neighbours(self, node_id: str, rel: str | None = None) -> list[tuple[Edge, Node, bool]]:
        return [(edge, self.nodes[other], forward) for edge, other, forward in self._adjacent.get(node_id, ()) if rel in (None, edge.rel)]

    def reach(self, start: str, max_hops: int = MAX_HOPS) -> list[Reach]:
        """Every chain of relations from start to an STE node. Chains that share the last hop collapse into one Reach."""
        if start not in self.nodes:
            raise KeyError(start)
        if self.nodes[start].cls == "STE":
            return [Reach(start, (), (start,))]
        grouped: dict[tuple[Any, ...], tuple[tuple[Step, ...], list[str]]] = {}

        def walk(node_id: str, steps: tuple[Step, ...], seen: frozenset[str]) -> None:
            went_up = any(s.edge.rel == "part_of" and s.forward for s in steps)
            for edge, other, forward in self._adjacent.get(node_id, ()):
                if went_up and edge.rel == "part_of" and not forward:
                    continue  # up a hierarchy, then down again, would reach a sibling LOB
                step = Step(edge, forward)
                neighbour = self.nodes[other]
                if neighbour.cls == "STE":
                    chain = steps + (step,)
                    key = (tuple((s.edge.src, s.edge.rel, s.edge.dst) for s in chain[:-1]), edge.rel, forward, node_id, edge.status, edge.basis)
                    entry = grouped.setdefault(key, (chain, []))
                    entry[1].append(other)
                elif neighbour.cls in TRANSIT_CLASSES and other not in seen and len(steps) + 1 < max_hops:
                    walk(other, steps + (step,), seen | {other})

        walk(start, (), frozenset({start}))
        reaches = [Reach(start, chain, tuple(sorted(set(targets)))) for chain, targets in grouped.values()]
        reaches.sort(key=lambda r: (len(r.steps), -STRENGTH[r.weakest], len(r.targets)))
        return reaches

    def entities_in_lob(self, lob_id: str) -> set[str]:
        """Entities that operate in the LOB, or in the grouping the LOB belongs to."""
        homes = {lob_id} | {node.id for edge, node, forward in self.neighbours(lob_id, "part_of") if forward}
        return {node.id for home in homes for edge, node, forward in self.neighbours(home, "operates_in") if not forward}

    def entities_in_place(self, place_id: str) -> set[str]:
        return {node.id for edge, node, forward in self.neighbours(place_id, "located_in") if not forward}


def _name_key(name: str) -> str:
    """Letters and digits only, so 'ST Engineering Urban Solutions Ltd.' finds the entity 'ST Engineering Urban Solutions Ltd'."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").casefold())


def _alias_table(names: Iterable[str], basis: str, table: dict[str, str]) -> None:
    for name in names:
        table.setdefault(name, basis)


def build_graph(pack: Any, *, reviewed_aliases: Mapping[str, Any] | None = None, overlay: Mapping[str, Any] | None = None) -> OntologyGraph:
    exposure = pack.exposure
    hierarchy = pack.lob_hierarchy
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    gaps: list[dict[str, Any]] = []

    def add_node(node: Node) -> Node:
        nodes.setdefault(node.id, node)
        return nodes[node.id]

    def add_edge(src: str, rel: str, dst: str, status: str, basis: str) -> None:
        edges.append(Edge(src, rel, dst, status, basis))

    entity_rows = exposure.get("entities") or []
    countries = sorted({str(row.get("countryOfIncorporation") or "").strip() for row in entity_rows} - {""})

    group_alias = "ST Engineering"
    starts = sum(str(row["entityName"]).casefold().startswith(group_alias.casefold()) for row in entity_rows)
    add_node(
        Node(
            GROUP_ID, "STE", "GROUP", group_alias, (group_alias,) if starts else (), {group_alias: "DERIVED"},
            {"aliasRule": f"{starts} of {len(entity_rows)} register entity names begin with it"},
        )
    )
    ba_name = (hierarchy or {}).get("businessArea") or "Urban Solutions"
    ba_id = f"STE-BA-{slugify(ba_name)}"
    add_node(Node(ba_id, "STE", "BUSINESS_AREA", ba_name))  # no alias: "urban solutions" is an ordinary phrase in news
    add_edge(ba_id, "part_of", GROUP_ID, DERIVED, "the register lists the business area's entities")

    place_ids: dict[str, str] = {}

    def place(name: str) -> str:
        key = name.strip()
        if key not in place_ids:
            node = add_node(Node(f"PLACE-{slugify(key)}", "Place", "COUNTRY", key, (key,), {key: "REGISTER"}))
            place_ids[key] = node.id
        return place_ids[key]

    lob_ids: dict[str, str] = {}
    profile_ids = {str(profile.get("lobName")): str(profile["lobId"]) for profile in pack.lob_profiles.get("lobs") or []}
    grouping_ids: dict[str, str] = {}

    def grouping(label: str) -> str:
        if label not in grouping_ids:
            node = add_node(Node(f"GROUPING-{slugify(label)}", "LOB", "GROUPING", label, (label,), {label: "REGISTER"}))
            grouping_ids[label] = node.id
            add_edge(node.id, "part_of", ba_id, SOURCE_STATED, "LOB hierarchy file, transcribed from the org chart")
        return grouping_ids[label]

    for lob in (hierarchy or {}).get("lobs") or []:
        lob_id = profile_ids.get(lob["lobName"]) or f"LOB-{slugify(lob['lobName'])}"
        lob_ids[lob["lobName"]] = lob_id
        aliases = tuple(dict.fromkeys([lob["lobName"], *(lob.get("aliases") or [])]))
        add_node(Node(lob_id, "LOB", "LOB", lob["lobName"], aliases, {alias: "REGISTER" for alias in aliases}, {"lobGrouping": lob.get("lobGrouping")}))
        if lob.get("lobGrouping"):
            add_edge(lob_id, "part_of", grouping(lob["lobGrouping"]), SOURCE_STATED, "LOB hierarchy file, transcribed from the org chart")

    def resolve(label: str) -> str | None:
        found = resolve_lob(label, hierarchy)
        return lob_ids.get(found["lobName"]) if found else None

    reviewed: dict[str, list[str]] = defaultdict(list)
    for item in (reviewed_aliases or {}).get("aliases") or []:
        reviewed[str(item.get("entity_id"))].append(str(item["alias"]))

    def with_aliases(node_id: str, name: str, register_aliases: Iterable[str], *, generated: bool = True) -> tuple[tuple[str, ...], dict[str, str]]:
        table: dict[str, str] = {}
        _alias_table([name, *register_aliases], "REGISTER", table)
        _alias_table(reviewed.get(node_id, ()), "REVIEWED", table)
        if generated:
            for source in _split_names(name):
                _alias_table(short_names(source, countries), "GENERATED", table)
                if source != name:
                    _alias_table([source] if usable_name(source) else [], "GENERATED", table)
        return tuple(table), table

    unresolved: dict[str, list[str]] = defaultdict(list)
    for row in entity_rows:
        aliases, basis = with_aliases(row["entityId"], row["entityName"], row.get("aliases") or [])
        # The group name belongs to the group node, so a generated short name equal to it is dropped here.
        aliases = tuple(alias for alias in aliases if alias.casefold() != group_alias.casefold())
        basis = {alias: basis[alias] for alias in aliases}
        add_node(Node(row["entityId"], "STE", "ENTITY", row["entityName"], aliases, basis, {"country": row.get("countryOfIncorporation"), "lobLabel": row.get("lob"), "sourceEntityId": row.get("sourceEntityId")}))
        add_edge(row["entityId"], "part_of", ba_id, SOURCE_STATED, f"{SHEET_ENTITIES}: business area")
        if row.get("countryOfIncorporation"):
            add_edge(row["entityId"], "located_in", place(row["countryOfIncorporation"]), SOURCE_STATED, f"{SHEET_ENTITIES}: country of incorporation")
        for country in row.get("assetCountries") or []:
            if country != row.get("countryOfIncorporation"):
                add_edge(row["entityId"], "located_in", place(country), SOURCE_STATED, "asset listing: the entity holds an asset in this country")
        lob_id = resolve(row.get("lob") or "")
        if lob_id:
            add_edge(row["entityId"], "operates_in", lob_id, SOURCE_STATED, f"{SHEET_ENTITIES}: LOB")
        elif row.get("lobGrouping"):
            add_edge(row["entityId"], "operates_in", grouping(row["lobGrouping"]), SOURCE_STATED, f"{SHEET_ENTITIES}: LOB grouping. The LOB label '{row.get('lob')}' names no single LOB.")
            unresolved[str(row.get("lob"))].append(row["entityId"])
    for label, ids in sorted(unresolved.items()):
        gaps.append({"kind": "ENTITY_LOB_UNRESOLVED", "label": label, "count": len(ids), "ids": ids, "needs": "One org chart LOB per entity. The register gives only the grouping."})

    for collection, cls in (("projects", "Site"), ("sites", "Site")):
        for row in exposure.get(collection) or []:
            node_id = str(row.get("projectId") or row.get("siteId"))
            name = str(row.get("projectName") or row.get("siteName") or node_id)
            aliases, basis = with_aliases(node_id, name, row.get("aliases") or [], generated=False)
            add_node(Node(node_id, cls, collection[:-1].upper(), name, aliases, basis))

    entity_by_key = {_name_key(row["entityName"]): row["entityId"] for row in entity_rows}

    def stated_lobs(row: Mapping[str, Any]) -> list[str]:
        """The LOBs a customer or supplier row names. A label the hierarchy does not know adds no edge."""
        found = [resolve(part.strip()) for part in str(row.get("lob") or "").split(";") if part.strip()]
        return list(dict.fromkeys(lob for lob in found if lob))

    # A customer's or supplier's country stays a field. A located_in edge would count it as STE footprint.
    unlinked_customers = 0
    for row in exposure.get("customers") or []:
        aliases, basis = with_aliases(row["customerId"], row["customerName"], row.get("aliases") or [])
        details = {key: row[key] for key in ("country", "sector", "customerType", "customerGroup", "confidence") if row.get(key)}
        add_node(Node(row["customerId"], "Customer", "CUSTOMER", row["customerName"], aliases, basis, details))
        add_edge(row["customerId"], "buys_from", ba_id, SOURCE_STATED, f"{SHEET_CUSTOMERS}: listed as a customer of the business area")
        linked = False
        for lob_id in stated_lobs(row):
            add_edge(row["customerId"], "buys_from", lob_id, SOURCE_STATED, f"{SHEET_CUSTOMERS}: primary LOB column")
            linked = True
        entity_id = entity_by_key.get(_name_key(row.get("contractingEntity") or ""))
        if entity_id:
            add_edge(row["customerId"], "buys_from", entity_id, SOURCE_STATED, f"{SHEET_CUSTOMERS}: contracting entity column")
            linked = True
        unlinked_customers += 0 if linked else 1
    if unlinked_customers:
        gaps.append({"kind": "CUSTOMER_ENTITY_UNKNOWN", "count": unlinked_customers, "needs": "For each customer, the entity or LOB it buys from. The register stops at the business area."})

    unit_ids: dict[str, str] = {}
    unlinked_suppliers = 0
    for row in exposure.get("suppliers") or []:
        aliases, basis = with_aliases(row["supplierId"], row["supplierName"], row.get("aliases") or [])
        add_node(Node(row["supplierId"], "Supplier", "SUPPLIER", row["supplierName"], aliases, basis))
        for label in [part.strip() for part in str(row.get("businessArea") or "").split(";") if part.strip()]:
            if label not in unit_ids:
                unit = add_node(Node(f"STE-UNIT-{slugify(label)}", "STE", "UNIT", label))
                unit_ids[label] = unit.id
                add_edge(unit.id, "part_of", ba_id, DERIVED, "the supplier sheet's business-area column names this unit of the business area")
            add_edge(row["supplierId"], "supplies", unit_ids[label], SOURCE_STATED, f"{SHEET_SUPPLIERS}: business area column")
        supplied = stated_lobs(row)
        for lob_id in supplied:
            add_edge(row["supplierId"], "supplies", lob_id, SOURCE_STATED, f"{SHEET_SUPPLIERS}: primary LOB column")
        unlinked_suppliers += 0 if supplied else 1
    if unlinked_suppliers:
        gaps.append({"kind": "SUPPLIER_ENTITY_UNKNOWN", "count": unlinked_suppliers, "needs": "For each supplier, the entity it supplies. The register names a unit such as 'URS Singapore'."})

    competitor_unresolved = 0
    for row in (pack.competitors or {}).get("competitors") or []:
        node_id = f"ORG-{slugify(row['competitor'])}"
        if node_id not in nodes:
            names = _split_names(row["competitor"])
            table: dict[str, str] = {}
            _alias_table([row["competitor"]], "REGISTER", table)
            for name in names:
                _alias_table([name] if usable_name(name) else [], "GENERATED" if len(names) > 1 else "REGISTER", table)
                _alias_table(short_names(name, countries), "GENERATED", table)
            add_node(Node(node_id, "Org", "COMPETITOR", row["competitor"], tuple(table), table, {"workscope": row.get("workscope")}))
        lob_id = resolve(row.get("canonicalLob") or row.get("lobLabel") or "")
        if lob_id:
            add_edge(node_id, "competes_in", lob_id, SOURCE_STATED, f"{SHEET_COMPETITORS} row {row['row']}")
        else:
            competitor_unresolved += 1
    if competitor_unresolved:
        gaps.append({"kind": "COMPETITOR_LOB_UNRESOLVED", "count": competitor_unresolved, "needs": "An org chart LOB for each competitor row whose label matches none."})

    management = pack.management_focus or {}
    for request in management.get("requests") or []:
        node_id = f"MGMT-REQUEST-{request['number']}"
        add_node(Node(node_id, "ManagementRequest", "REQUEST", str(request["text"]), (), {}, {"scope": request.get("scope"), "entity": request.get("entity"), "topics": request.get("topics")}))
        lob_id = resolve(str(request.get("scope") or ""))
        if lob_id:
            add_edge(node_id, "focuses_on", lob_id, SOURCE_STATED, f"{SHEET_MANAGEMENT}: request {request['number']}")
        for country in request.get("countries") or []:
            add_edge(node_id, "focuses_on", place(str(country)), SOURCE_STATED, f"{SHEET_MANAGEMENT}: request {request['number']}")
    for number, item in enumerate(management.get("focusItems") or [], start=1):
        node_id = f"MGMT-FOCUS-{number}"
        add_node(Node(node_id, "ManagementRequest", "FOCUS_ITEM", str(item["focus"]), (), {}, {"scope": item.get("scope"), "indicators": item.get("indicators"), "whyItMatters": item.get("whyItMatters")}))
        lob_id = resolve(str(item.get("scope") or ""))
        if item.get("allLobs"):
            add_edge(node_id, "focuses_on", ba_id, SOURCE_STATED, f"{SHEET_MANAGEMENT}: scope table, all LOBs")
        elif lob_id:
            add_edge(node_id, "focuses_on", lob_id, SOURCE_STATED, f"{SHEET_MANAGEMENT}: scope table")

    for theme in pack.themes.get("themes") or []:
        add_node(Node(theme["themeId"], "Theme", theme["tier"], theme["themeName"], (), {}, {"keywords": theme.get("keywords") or []}))

    _apply_overlay(nodes, edges, overlay if overlay is not None else getattr(pack, "relationships", None))
    return OntologyGraph(nodes.values(), edges, gaps)


def merge_graphs(graphs: Iterable[OntologyGraph], themes: Iterable[Mapping[str, Any]] = ()) -> OntologyGraph:
    """One graph over several business areas, for a Group pack. A node two graphs share (the group, a country, a customer)
    becomes one node with both alias tables. The business areas' themes are replaced by the Group pack's own themes."""
    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str, str], Edge] = {}
    gaps: list[Mapping[str, Any]] = []
    for graph in graphs:
        for node in graph.nodes.values():
            if node.cls == "Theme":
                continue
            kept = nodes.get(node.id)
            if kept is None:
                nodes[node.id] = node
                continue
            aliases = tuple(dict.fromkeys((*kept.aliases, *node.aliases)))
            nodes[node.id] = Node(kept.id, kept.cls, kept.kind, kept.name, aliases, {**node.alias_basis, **kept.alias_basis}, {**node.attrs, **kept.attrs})
        for edge in graph.edges:
            if edge.src in nodes and edge.dst in nodes:
                edges.setdefault((edge.src, edge.rel, edge.dst), edge)
        gaps.extend(graph.gaps)
    for theme in themes:
        nodes[theme["themeId"]] = Node(theme["themeId"], "Theme", theme["tier"], theme["themeName"], (), {}, {"keywords": theme.get("keywords") or []})
    return OntologyGraph(nodes.values(), edges.values(), gaps)


def _apply_overlay(nodes: dict[str, Node], edges: list[Edge], overlay: Mapping[str, Any] | None) -> None:
    """Add hand-authored rows. An unknown node, an unknown relation, or a VALIDATED row with no approver fails loudly."""
    for item in (overlay or {}).get("edges") or []:
        status = item.get("status")
        if status not in {PROPOSED, VALIDATED}:
            raise ValueError(f"Overlay edge {item.get('src')} {item.get('rel')} {item.get('dst')} needs status PROPOSED or VALIDATED.")
        if status == VALIDATED and not (item.get("approvedBy") and item.get("approvedOn")):
            raise ValueError(f"Overlay edge {item.get('src')} {item.get('rel')} {item.get('dst')} is VALIDATED without approvedBy and approvedOn.")
        if item.get("src") not in nodes or item.get("dst") not in nodes:
            raise ValueError(f"Overlay edge {item.get('src')} {item.get('rel')} {item.get('dst')} names a node that does not exist.")
        if not item.get("basis"):
            raise ValueError(f"Overlay edge {item['src']} {item.get('rel')} {item['dst']} needs a basis.")
        edges.append(Edge(item["src"], item["rel"], item["dst"], status, str(item["basis"]), item.get("approvedBy")))
    # An accepted alias is firm, so it is applied first. A rejection is applied last and wins, and it removes every spelling of the name.
    for item in (overlay or {}).get("aliases") or []:
        node = nodes.get(item.get("node"))
        if node is None:
            raise ValueError(f"Overlay alias '{item.get('alias')}' names node {item.get('node')}, which does not exist.")
        alias = item["alias"]
        if alias in node.aliases:
            basis = {**node.alias_basis, alias: "OVERLAY" if node.alias_basis.get(alias) in {None, "GENERATED"} else node.alias_basis[alias]}
            nodes[node.id] = Node(node.id, node.cls, node.kind, node.name, node.aliases, basis, node.attrs)
        else:
            nodes[node.id] = Node(node.id, node.cls, node.kind, node.name, node.aliases + (alias,), {**node.alias_basis, alias: "OVERLAY"}, node.attrs)
    for item in (overlay or {}).get("rejectedAliases") or []:
        node = nodes.get(item.get("node"))
        if node is None:
            raise ValueError(f"Overlay rejects alias '{item.get('alias')}' on node {item.get('node')}, which does not exist.")
        kept = tuple(alias for alias in node.aliases if alias.casefold() != str(item["alias"]).casefold())  # an alias that no longer exists needs no removal
        nodes[node.id] = Node(node.id, node.cls, node.kind, node.name, kept, {alias: basis for alias, basis in node.alias_basis.items() if alias in kept}, node.attrs)
