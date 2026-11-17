"""Deterministic screen for the adverse news scan (spec 46, section 4, stage 2). Pure: no model, no network.

A page is a candidate when its title or lead hits the lexicon. Its link to the merged graph sets its provisional section.
Candidates join into items by event, or by the same linked name and a similar title. Earlier items are matched the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from grit.adverse.people import PEOPLE, PEOPLE_RISK, countries_named, node_id
from grit.adverse.registers import LEAD_CHARS, Lexicon, LexiconHit
from grit.report.format_v2 import clean_title
from grit.ontology.link import COMPETITOR, COUNTERPARTY, DIRECT, PERIMETER, SECTOR, LinkIndex, link_candidate

CAPS = {"A": 200, "B": 80, "C": 60}
RUNG_WEIGHT = {DIRECT: 6, PEOPLE: 5, COUNTERPARTY: 4, COMPETITOR: 3, PERIMETER: 2, SECTOR: 1}
BAND_WEIGHT = {"T1": 3, "T2": 2, "T3": 1, "UNRATED": 0}
UNUSABLE_BANDS = frozenset({"T4", "CONTESTED"})
B_LANES = frozenset({"REGULATOR", "COMPETITOR"})
OWN_LANES = frozenset({"GROUP", "SUBSIDIARY", "CUSTOMER", "REGULATOR", "COMPETITOR"})
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by for from has have in into is it its of on or over the to was were with after amid says said new".split())


@dataclass
class Candidate:
    signal_id: str
    url: str
    title: str
    text: str
    publisher: str | None
    published_at: str | None
    first_seen_at: str
    rung: str | None
    involves: list[dict[str, Any]]
    hits: list[LexiconHit]
    lanes: list[str]
    band: str
    section: str
    score: float
    event_id: str | None = None
    previous: dict[str, Any] | None = None
    pinned: list[str] = field(default_factory=list)  # ST Engineering names the page states: always reported

    @property
    def day(self) -> str:
        return str(self.published_at or self.first_seen_at or "")[:10]

    @property
    def node_ids(self) -> list[str]:
        return [item["node_id"] for item in self.involves]

    def compact(self, key: str, lead: int = 700) -> dict[str, Any]:
        return {
            "id": key, "title": self.title, "outlet": self.publisher or "", "date": self.day or "not stated",
            "lead": " ".join(self.text.split())[:lead], "provisional_section": self.section, "link": self.rung or "none",
            "names": [f"{item['name']} ({item['cls']})" for item in self.involves][:4],
            "adverse_terms": sorted({f"{hit.category}: {hit.term}" for hit in self.hits if hit.where != "BODY"})[:6],
        }


@dataclass
class Item:
    """One event: the candidates that report it."""

    section: str
    members: list[Candidate] = field(default_factory=list)

    @property
    def lead(self) -> Candidate:
        return max(self.members, key=lambda c: c.score)

    @property
    def node_ids(self) -> list[str]:
        return list(dict.fromkeys(node for member in self.members for node in member.node_ids))

    @property
    def pinned(self) -> list[str]:
        return list(dict.fromkeys(name for member in self.members for name in member.pinned))


def in_window(day: str, start: date, end: date) -> bool:
    return bool(day) and start.isoformat() <= day[:10] <= end.isoformat()


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(word for word in _WORD.findall(title.casefold()) if word not in _STOP and len(word) > 2)


def similar(a: str, b: str) -> float:
    left, right = title_tokens(a), title_tokens(b)
    return len(left & right) / len(left | right) if left and right else 0.0


def own_named(title: str, lead: str, own: Mapping[str, Sequence[str]] | None) -> list[str]:
    """The ST Engineering names (group or subsidiary) the title or lead states, by name. own maps a name to its aliases.
    A short alias right after another capitalised word is a namesake: "Vilas Transcore" is not TransCore. A name that
    contains "ST Engineering" is never a namesake."""
    text = f"{title}\n{lead}"
    found: list[str] = []
    for name, aliases in (own or {}).items():
        for alias in aliases:
            for match in re.finditer(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", text, re.IGNORECASE):
                before = re.search(r"([A-Za-z][\w&.'-]*)\s+$", text[max(0, match.start() - 40):match.start()])
                if "st engineering" not in alias.casefold() and before and before.group(1)[0].isupper() and before.group(1) not in {"The", "A", "An"}:
                    continue
                found.append(name)
                break
            if name in found:
                break
    return found


def screen(
    rows: Iterable[Mapping[str, Any]],
    *,
    index: LinkIndex,
    lob_profiles: Mapping[str, Any],
    lexicon: Lexicon,
    lanes_by_signal: Mapping[str, Sequence[str]],
    band_of: Any,
    peer_ids: frozenset[str] = frozenset(),
    business_areas_of: Any = None,
    people: Mapping[str, Any] | None = None,
    own: Mapping[str, Sequence[str]] | None = None,
) -> tuple[dict[str, list[Candidate]], dict[str, int]]:
    """Candidates by provisional section, ranked and capped, and counts of what the screen dropped. people is the internal staff
    log (grit.adverse.people): a threat to people in a country where STE people are is a Section A candidate, rung PEOPLE.
    own maps ST Engineering names to aliases: a page that states one and hits the lexicon is pinned, Section A, and never capped."""
    out: dict[str, list[Candidate]] = {"A": [], "B": [], "C": []}
    counts = {"pages": 0, "no_lead_hit": 0, "unusable_source": 0, "unlinked": 0}
    for row in rows:
        counts["pages"] += 1
        title, text = str(row.get("title") or ""), str(row.get("text") or "")
        hits = lexicon.hits(title, text)
        lead_hits = [hit for hit in hits if hit.where != "BODY"]
        adverse_lead = [hit for hit in lead_hits if hit.category != "EMERGING"]
        if not lead_hits:
            counts["no_lead_hit"] += 1
            continue
        band = band_of(row.get("url"), row.get("publisher"))
        if band in UNUSABLE_BANDS:
            counts["unusable_source"] += 1
            continue
        link = link_candidate({"title": title, "text": text}, index, lob_profiles)
        lanes = sorted(set(lanes_by_signal.get(str(row["signal_id"]), ())))
        strong = [item for item in link.involves if item.strong and item.cls in {"STE", "Site", "Customer", "Supplier", "Org"}]
        rung = link.rung
        # A watchlist peer named in the title counts as a competitor link, for Section B only.
        if rung is None and any(item.node_id in peer_ids and item.where == "TITLE" for item in link.involves):
            rung = COMPETITOR
            strong = [item for item in link.involves if item.node_id in peer_ids and item.where == "TITLE"]
        strong_themes = [theme for theme in link.themes if theme.strong]
        # A threat to people, in the title or lead, in a country where the staff log places STE people.
        # The outlet suffix is not the story: "| The Times of Israel" names no place.
        staffed = countries_named(f"{clean_title(title)}\n{text[:LEAD_CHARS]}", people) if any(hit.category == PEOPLE_RISK for hit in lead_hits) else []
        if staffed and rung not in {DIRECT, COUNTERPARTY}:
            rung = PEOPLE
        pinned = own_named(title, text[:LEAD_CHARS], own)
        if pinned:
            rung, section = DIRECT, "A"
        elif rung in {DIRECT, COUNTERPARTY, PEOPLE} and adverse_lead:
            section = "A"
        elif (rung == COMPETITOR or set(lanes) & B_LANES) and adverse_lead:
            section = "B"
        elif rung in {PERIMETER, SECTOR} or strong_themes or set(lanes) & OWN_LANES:
            section = "C"
        else:
            counts["unlinked"] += 1
            continue
        involves = [
            {"node_id": item.node_id, "cls": item.cls, "name": item.name, "where": item.where,
             "business_areas": list(business_areas_of(item.node_id)) if business_areas_of else []}
            for item in strong
        ] + [{"node_id": node_id(country), "cls": "People", "name": f"ST Engineering people in {country}", "where": "LEAD", "business_areas": []}
             for country in staffed]
        title_name = any(item.where == "TITLE" for item in strong)
        score = (RUNG_WEIGHT.get(rung or "", 0) + BAND_WEIGHT.get(band, 0) + 1.5 * min(3, len({hit.category for hit in adverse_lead}))
                 + (1 if set(lanes) & OWN_LANES else 0) + (1 if any(hit.where == "TITLE" for hit in adverse_lead) else 0)
                 + (2 if title_name and any(hit.where == "TITLE" for hit in adverse_lead) else 0))
        out[section].append(Candidate(
            str(row["signal_id"]), str(row.get("url") or ""), title, text, row.get("publisher"), row.get("published_at"),
            str(row.get("first_seen_at") or ""), rung, involves, hits, lanes, band, section, score, row.get("event_id"), pinned=pinned,
        ))
    for section, items in out.items():
        items.sort(key=lambda c: (-c.score, c.day, c.signal_id))
        over = [c for c in items[CAPS[section]:] if not c.pinned]
        counts[f"over_cap_{section}"] = len(over)
        items[:] = [c for c in items if c not in over]
    counts["pinned"] = sum(1 for items in out.values() for c in items if c.pinned)
    return out, counts


def group(candidates: Sequence[Candidate], section: str) -> list[Item]:
    """One item per event: the nightly index's event, else a shared linked name and a similar title (no name: a closer title).
    Threat-to-people pages (rung PEOPLE) that share a staffed country join one item."""
    items: list[Item] = []
    for candidate in sorted(candidates, key=lambda c: -c.score):
        home = None
        for item in items:
            for member in item.members:
                if candidate.event_id and candidate.event_id == member.event_id:
                    home = item
                elif set(candidate.node_ids) & set(member.node_ids) and similar(candidate.title, member.title) >= 0.35:
                    home = item
                elif candidate.rung == PEOPLE == member.rung and {n for n in candidate.node_ids if n.startswith("PEOPLE-")} & set(member.node_ids):
                    home = item  # threats to people in one country are one security situation: one item, one assessment
                elif not candidate.node_ids and not member.node_ids and similar(candidate.title, member.title) >= 0.5:
                    home = item
                if home:
                    break
            if home:
                break
        if home is None:
            items.append(Item(section, [candidate]))
        else:
            home.members.append(candidate)
    return items


def match_previous(item: Item, previous: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The most recent earlier item that reports the same event: a shared page or event, or a shared name and a similar title."""
    signals = {m.signal_id for m in item.members}
    events = {m.event_id for m in item.members if m.event_id}
    names = set(item.node_ids)
    for earlier in previous:
        if signals & set(earlier.get("signal_ids") or ()) or events & set(earlier.get("event_ids") or ()):
            return earlier
        if names & set(earlier.get("entity_ids") or ()) and any(similar(m.title, str(earlier.get("headline") or "")) >= 0.3 for m in item.members):
            return earlier
    return None


def corroboration(item: Item, registrable: Any) -> dict[str, Any]:
    """Independent outlets and official sources among the item's members. Corroborated: an official source, or two outlets."""
    outlets = {registrable(member.url) for member in item.members if member.band not in UNUSABLE_BANDS}
    official = any(member.band == "T1" for member in item.members)
    return {"outlets": sorted(outlets), "official": official, "corroborated": official or len(outlets) >= 2}
