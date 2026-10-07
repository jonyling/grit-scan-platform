"""Link a candidate to the ontology graph and place it on the transmission ladder. Pure: no I/O, no model, no score.

Rungs, strongest first:
  DIRECT        the text names an STE entity, the group, a project, or a site.
  COUNTERPARTY  it names a registered customer or supplier.
  COMPETITOR    it names a registered competitor.
  PERIMETER     it matches a LOB keyword and names a country where an entity of that LOB's grouping is registered.
  SECTOR        it matches a LOB keyword and nothing else.
  THEME         no register link, but it is a big event: it hits a KEY STE theme and meets the scale cues. grit_scan/priority.py sets it.
A name counts only when it is strong: in the title, in the first 400 characters, or named twice. One passing mention in the body is
recorded and does not raise the rung. A one-word name that is not an acronym ("Leonardo", "Alstom") also needs a second ontology cue in the
same text: a LOB keyword. A country or an STE theme is too weak: any story can mention a country, and a theme keyword such as "competitor" or
"margin" is a common word. Without a LOB keyword the name is recorded and does not raise the rung.
A country alone is never a link, and a theme alone is never a link.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from grit_scan.matchers.lob_matcher import lob_match
from grit_scan.ontology_graph import OntologyGraph

DIRECT = "DIRECT"
COUNTERPARTY = "COUNTERPARTY"
COMPETITOR = "COMPETITOR"
PERIMETER = "PERIMETER"
SECTOR = "SECTOR"
THEME = "THEME"
RUNGS = (THEME, SECTOR, PERIMETER, COMPETITOR, COUNTERPARTY, DIRECT)  # weakest first
LEAD_CHARS = 400
MIN_THEME_KEYWORD = 6
MATCHED_CLASSES = frozenset({"STE", "Customer", "Supplier", "Org", "Place", "Site"})
# Texts name a country in other ways than its register name. Only a country with a registered entity can matter.
PLACE_ALIASES = {"USA": ("United States", "U.S.", "U.S")}


@dataclass(frozen=True)
class Involvement:
    node_id: str
    cls: str
    name: str
    matched: str
    alias_basis: str
    where: str  # TITLE, LEAD, or BODY
    count: int
    strong: bool
    ambiguous: bool  # the matched text names more than one node
    context_required: bool = False  # a one-word name with no second ontology cue in the text


@dataclass(frozen=True)
class ThemeHit:
    theme_id: str
    tier: str
    matched: tuple[str, ...]
    where: str = "BODY"  # the earliest place any keyword matched: TITLE, LEAD, or BODY
    strong: bool = False  # a keyword in the title or lead, or one named twice. A body mention is recorded and does not count.


@dataclass(frozen=True)
class Link:
    rung: str | None
    involves: tuple[Involvement, ...]
    lob_id: str | None
    canonical_lob: str | None
    lob_keywords: tuple[str, ...]
    place_ids: tuple[str, ...]
    themes: tuple[ThemeHit, ...]
    reasons: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        for key in ("involves", "place_ids", "themes", "reasons", "lob_keywords"):
            record[key] = [dict(item) if isinstance(item, dict) else item for item in record[key]]
        return record


def _boundary(alias: str, *, case_sensitive: bool) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in alias.split())
    return re.compile(rf"(?<![\w]){body}(?![\w])", 0 if case_sensitive else re.IGNORECASE)


def _is_acronym(alias: str) -> bool:
    return alias.isupper() and alias.isalpha() and len(alias) <= 4


def _one_word_name(matched: str) -> bool:
    return len(matched.split()) == 1 and not _is_acronym(matched)


class LinkIndex:
    """Aliases and theme keywords from the graph, ready to search. Build it once per pack."""

    def __init__(self, graph: OntologyGraph, lob_profiles: Mapping[str, Any]):
        self.graph = graph
        found: dict[tuple[str, bool], dict[str, str]] = {}
        for node in graph.nodes.values():
            if node.cls not in MATCHED_CLASSES:
                continue
            extras = PLACE_ALIASES.get(node.name, ()) if node.cls == "Place" else ()
            for alias in (*node.aliases, *extras):
                key = (alias if _is_acronym(alias) else alias.casefold(), _is_acronym(alias))
                basis = node.alias_basis.get(alias, "REGISTER")
                kept = found.setdefault(key, {}).get(node.id)
                # One node found by one spelling is one involvement. Keep the firmer basis.
                if kept is None or (kept == "GENERATED" and basis != "GENERATED"):
                    found[key][node.id] = basis
        self._aliases: dict[tuple[str, bool], list[tuple[str, str]]] = {key: list(nodes.items()) for key, nodes in found.items()}
        self._patterns = {key: _boundary(key[0], case_sensitive=key[1]) for key in self._aliases}
        self._themes = [
            (node, [keyword for keyword in node.attrs.get("keywords") or [] if len(keyword) >= MIN_THEME_KEYWORD or " " in keyword])
            for node in graph.of_class("Theme")
        ]
        by_name = {node.name: node.id for node in graph.of_class("LOB") if node.kind == "LOB"}
        self._lob_node = {
            str(profile["lobId"]): by_name.get(str(profile.get("canonicalLob") or profile.get("lobName")))
            for profile in lob_profiles.get("lobs") or []
        }

    def involvements(self, title: str, text: str) -> list[Involvement]:
        whole = f"{title}\n{text}"
        folded = whole.casefold()
        found: list[tuple[int, int, str, str, str]] = []  # start, end, key, matched text
        for key, pattern in self._patterns.items():
            alias, sensitive = key
            if (alias not in whole) if sensitive else (alias not in folded):
                continue
            for match in pattern.finditer(whole):
                found.append((match.start(), match.end(), alias, match.group(0), sensitive))
        # The longest name wins where two overlap, so "ST Engineering Aethon" is not also "ST Engineering".
        found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        kept: list[tuple[int, int, str, str, bool]] = []
        for item in found:
            if not any(other[0] <= item[0] and item[1] <= other[1] for other in kept):
                kept.append(item)
        title_end = len(title)
        grouped: dict[tuple[str, bool], list[tuple[int, int, str]]] = {}
        for start, end, alias, matched, sensitive in kept:
            grouped.setdefault((alias, sensitive), []).append((start, end, matched))
        result: list[Involvement] = []
        for key, spans in grouped.items():
            nodes = self._aliases[key]
            first = min(spans)
            where = "TITLE" if first[0] < title_end else "LEAD" if first[0] - title_end - 1 < LEAD_CHARS else "BODY"
            strong = where != "BODY" or len(spans) >= 2
            for node_id, basis in nodes:
                node = self.graph.nodes[node_id]
                result.append(Involvement(node_id, node.cls, node.name, first[2], basis, where, len(spans), strong, len(nodes) > 1))
        result.sort(key=lambda item: (not item.strong, item.where != "TITLE", item.node_id))
        return result

    def themes(self, title: str, text: str) -> list[ThemeHit]:
        whole = f"{title}\n{text}"
        folded = whole.casefold()
        title_end = len(title)
        hits = []
        for node, keywords in self._themes:
            matched: list[str] = []
            places: list[str] = []
            strong = False
            for keyword in keywords:
                if keyword.casefold() not in folded:
                    continue
                starts = [match.start() for match in _boundary(keyword, case_sensitive=False).finditer(whole)]
                if not starts:
                    continue
                where = "TITLE" if starts[0] < title_end else "LEAD" if starts[0] - title_end - 1 < LEAD_CHARS else "BODY"
                matched.append(keyword)
                places.append(where)
                strong = strong or where != "BODY" or len(starts) >= 2
            if matched:
                hits.append(ThemeHit(node.id, node.kind, tuple(matched), min(places, key=("TITLE", "LEAD", "BODY").index), strong))
        return hits

    def lob_node(self, profile_id: str) -> str | None:
        return self._lob_node.get(profile_id)


def link_candidate(candidate: Mapping[str, Any], index: LinkIndex, lob_profiles: Mapping[str, Any]) -> Link:
    title = str(candidate.get("title") or "")
    text = str(candidate.get("text") or "")
    involves = index.involvements(title, text)
    themes = index.themes(title, text)
    lob = lob_match(f"{title}\n{text}", lob_profiles)
    chosen = lob["lob"] if lob["matched"] and lob["matchMethod"] in {"EXACT_NAME_OR_CODE", "KEYWORD_MATCH"} else None
    lob_id = index.lob_node(str(chosen["lobId"])) if chosen else None
    place_ids = tuple(dict.fromkeys(item.node_id for item in involves if item.cls == "Place" and item.strong))
    graph = index.graph
    second_cue = lob["matchMethod"] in {"KEYWORD_MATCH", "AMBIGUOUS_KEYWORD_MATCH", "EXACT_NAME_OR_CODE"}
    if not second_cue:
        involves = [
            dataclasses.replace(item, strong=False, context_required=True) if item.strong and item.cls != "Place" and _one_word_name(item.matched) else item
            for item in involves
        ]

    rung: str | None = None
    reasons: list[str] = []
    strong = [item for item in involves if item.strong]
    for cls_set, level, label in (({"STE", "Site"}, DIRECT, "an STE name"), ({"Customer", "Supplier"}, COUNTERPARTY, "a registered customer or supplier"), ({"Org"}, COMPETITOR, "a registered competitor")):
        hit = next((item for item in strong if item.cls in cls_set), None)
        if hit:
            rung = level
            reasons.append(f"{level}: names {label}, {hit.name}, as '{hit.matched}' in the {hit.where.lower()}.")
            break
    if rung is None and lob_id:
        near = [place for place in place_ids if graph.entities_in_place(place) & graph.entities_in_lob(lob_id)]
        if near:
            rung = PERIMETER
            reasons.append(f"PERIMETER: matches the {chosen.get('canonicalLob') or chosen['lobName']} LOB keywords and names {graph.nodes[near[0]].name}, where a registered entity of that grouping is located.")
        else:
            rung = SECTOR
            reasons.append(f"SECTOR: matches the {chosen.get('canonicalLob') or chosen['lobName']} LOB keywords only.")
    for item in involves:
        if item.context_required:
            reasons.append(f"Not a link: '{item.matched}' is a one-word name, and no LOB keyword appears with it.")
        elif not item.strong and item.cls != "Place":
            reasons.append(f"Passing mention, not a link: {item.name} appears once in the body.")
    keywords = tuple(dict.fromkeys(str(word) for word in (chosen or {}).get("keywords") or [] if str(word).casefold() in f"{title}\n{text}".casefold()))[:5]
    return Link(
        rung=rung,
        involves=tuple(involves),
        lob_id=lob_id,
        canonical_lob=(chosen.get("canonicalLob") or chosen.get("lobName")) if chosen else None,
        lob_keywords=keywords,
        place_ids=place_ids,
        themes=tuple(themes),
        reasons=tuple(reasons),
    )


def rung_rank(rung: str | None) -> int:
    return RUNGS.index(rung) + 1 if rung else 0
