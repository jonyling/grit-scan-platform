"""Workbook sheets that feed search: the keyword libraries, the competitor list, and management focus.

The parsers are pure functions over cell rows, so tests run without the workbook. A row the
parser cannot place goes to a dropped list with a reason. No term is added or reworded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from grit.ontology.lob_hierarchy import resolve_lob
from grit.ontology.xlsx_reader import read_worksheet, sheet_states

LIBRARY_SHEETS = (
    "SUI - SSU",
    "SUI - SSA",
    "SUI - HRA",
    "SM - Mobility Rail",
    "SM - Mobility Road",
    "SM - Mobility Services",
    "SM - TransCore",
)
COMPETITOR_SHEET = "3. Competitors"
MANAGEMENT_SHEET = "10. Management Focus"

_PRIORITIES = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "emerging": "Emerging",
    "critical for grit": "Critical for GRIT",
    "highest priority": "Highest Priority",
}
_NUMBERED = re.compile(r"^(\d+)\.\s+(.*\S)\s*$")
_TIER1 = re.compile(r"^(?:\d+\.\s+)?Tier 1\b", re.I)
_TOP_HEADING = re.compile(r"^(?:Top\s+(\d+)\b.*\bKeywords\b|GRIT Scan Keywords\s*\(Top\s+(\d+)\b)", re.I)
_TAG_HEADING = re.compile(r"\bRecommended GRIT Tag Clusters\b", re.I)
_EMERGING_HEADING = re.compile(r"^Highest-Value\b.*\bEmerging Risk Themes\b", re.I)
_WATCHLIST = re.compile(r"\bWatchlist\b", re.I)
_MAX_TERM_WORDS = 14
_MANAGEMENT_GROUP_HEADERS = {"opportunities", "challenges", "key risks"}


def _clean(value: object) -> str:
    text = str(value if value is not None else "").replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def _cells(rows: list[list[object | None]], width: int) -> list[list[str]]:
    return [[_clean(row[index]) if index < len(row) else "" for index in range(width)] for row in rows]


def _is_note(text: str) -> bool:
    """A citation, a sentence, or an instruction. It is not a term."""
    return ("[" in text and "]" in text) or text.endswith((".", ":")) or len(text.split()) > _MAX_TERM_WORDS


def _split_priority(heading: str) -> tuple[str, str | None]:
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", heading)
    if match and match.group(2).strip().casefold() in _PRIORITIES:
        return match.group(1).strip(), _PRIORITIES[match.group(2).strip().casefold()]
    return heading.strip(), None


def _blocks(rows: list[tuple[str, str]]) -> Iterator[list[tuple[int, str, str]]]:
    """Yield runs of consecutive non-empty rows. A blank row ends a run."""
    block: list[tuple[int, str, str]] = []
    for number, (first, second) in enumerate(rows, start=1):
        if first or second:
            block.append((number, first, second))
        elif block:
            yield block
            block = []
    if block:
        yield block


def _add_terms(groups: list[dict[str, Any]], name: str, terms: list[str]) -> None:
    group = next((item for item in groups if item["name"] == name), None)
    if group is None:
        group = {"name": name, "terms": []}
        groups.append(group)
    group["terms"].extend(term for term in terms if term not in group["terms"])


def _split_list(text: str) -> list[str]:
    text = re.split(r"\s*\[", text, maxsplit=1)[0]  # the author's source citations follow the list
    return list(dict.fromkeys(part.strip(" .;") for part in text.split(",") if part.strip(" .;")))


def parse_keyword_library_rows(rows: list[tuple[str, str]]) -> dict[str, Any]:
    """Parse one hidden LOB keyword sheet. rows holds (column A, column B) for every sheet row, blanks included.

    A blank row ends a block. A one-row block is a heading, a label, or a note. A list of terms needs two rows or more.
    """
    library: dict[str, Any] = {
        "tier1": [],
        "clusters": [],
        "watchlist": [],
        "tagClusters": [],
        "scanKeywords": None,
        "emergingThemes": [],
        "notesSkipped": 0,
        "dropped": [],
    }
    section: str | None = None
    cluster: dict[str, Any] | None = None
    group: str | None = None
    awaiting_scan_list = False
    for block in _blocks(rows):
        if len(block) == 1:
            number, first, second = block[0]
            text = first or second
            numbered = _NUMBERED.match(text)
            top = _TOP_HEADING.match(text)
            if _TIER1.match(text):
                section, cluster, group = "tier1", None, None
            elif numbered and _WATCHLIST.search(text):
                section, cluster, group = "watchlist", None, None
            elif numbered:
                name, priority = _split_priority(numbered.group(2))
                cluster = {"number": int(numbered.group(1)), "name": name, "priority": priority, "groups": []}
                library["clusters"].append(cluster)
                section, group = "cluster", None
            elif top:
                section, group, awaiting_scan_list = "scan", None, True
                library["scanKeywords"] = {"declared": int(top.group(1) or top.group(2)), "terms": []}
            elif _TAG_HEADING.search(text):
                section, group = "tags", None
            elif _EMERGING_HEADING.search(text):
                section, group = "emerging", None
            elif awaiting_scan_list and text.count(",") >= 4:
                library["scanKeywords"]["terms"] = _split_list(text)
                awaiting_scan_list = False
            elif _is_note(text):
                library["notesSkipped"] += 1
            else:
                group = text
            continue

        if all(_NUMBERED.match(first) for _, first, _ in block):
            library["emergingThemes"].extend(_NUMBERED.match(first).group(2) for _, first, _ in block)
            continue
        if block[0][1].casefold() == "cluster" and block[0][2].casefold() == "priority":
            for _, first, second in block[1:]:
                if _is_note(first):  # a note can sit directly under the table with no blank row
                    library["notesSkipped"] += 1
                else:
                    library["tagClusters"].append({"name": first, "priority": second or None})
            continue
        terms: list[str] = []
        for number, first, second in block:
            text = first or second
            if _is_note(text):
                library["dropped"].append({"row": number, "text": text[:120], "reason": "SENTENCE_IN_TERM_LIST"})
            else:
                terms.append(text.strip(" ,;"))
        if section == "tier1":
            _add_terms(library["tier1"], group or "Keywords", terms)
        elif section == "cluster" and cluster is not None:
            _add_terms(cluster["groups"], group or "Keywords", terms)
        elif section == "watchlist":
            _add_terms(library["watchlist"], group or "Keywords", terms)
        else:
            library["dropped"].append({"row": block[0][0], "text": ", ".join(terms)[:120], "reason": "TERMS_OUTSIDE_A_SECTION"})
    return library


def parse_competitor_rows(rows: list[list[str]]) -> dict[str, Any]:
    """Parse the competitor sheet. rows holds columns A to D for every row. A LOB label applies until the next one."""
    competitors: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    lob = ""
    category: str | None = None
    for number, (label, name, workscope, note) in enumerate(rows, start=1):
        if label.casefold() == "lob" and name.casefold() == "competitor":
            continue
        if label:
            lob = label
            category = None
        if not name:
            category = None
            if label and number > 1:
                continue
            if number > 1 and (workscope or note):
                dropped.append({"row": number, "text": (workscope or note)[:120], "reason": "NO_COMPETITOR_NAME"})
            continue
        following = rows[number] if number < len(rows) else None
        if not workscope and not note and following and following[1] and following[2]:
            # A name with no workscope that heads a block of named competitors is a product line, not a competitor.
            category = name
            dropped.append({"row": number, "text": name, "reason": "SUBHEADING"})
            continue
        if (lob, name.casefold()) in seen:
            continue
        seen.add((lob, name.casefold()))
        competitors.append(
            {"lobLabel": lob, "competitor": name, "workscope": workscope or None, "note": note or None, "category": category, "row": number}
        )
    return {"competitors": competitors, "dropped": dropped}


def parse_management_focus_rows(rows: list[list[str]]) -> dict[str, Any]:
    """Parse the management focus sheet: numbered requests first, then the LOB / Scope table. Fails loudly on another layout."""
    header = next((index for index, row in enumerate(rows) if row[0].casefold() == "lob / scope" and row[1].casefold() == "management focus"), None)
    if header is None:
        raise ValueError("Management focus sheet is missing its 'LOB / Scope | Management Focus' table.")
    requests: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    topic: dict[str, Any] | None = None
    for number, (index, scope, entity, text, _extra) in enumerate(rows[:header], start=1):
        if number == 1 and scope.casefold() == "lob":
            continue
        if index.isdigit():
            current = {"number": int(index), "scope": scope, "entity": entity or None, "text": text, "countries": [], "topics": [], "notes": []}
            requests.append(current)
            topic = None
            continue
        if current is None:
            continue
        if scope.isdigit() and entity:
            current["countries"].append(entity)
        elif scope:
            current["notes"].append(scope)
        if text:
            if text.casefold() in _MANAGEMENT_GROUP_HEADERS or text.endswith(":"):
                topic = {"name": text.rstrip(":"), "items": []}
                current["topics"].append(topic)
            elif topic is not None:
                topic["items"].append(text)
            else:
                dropped.append({"row": number, "text": text[:120], "reason": "ITEM_BEFORE_ANY_TOPIC_HEADING"})
    items = []
    for row in rows[header + 1 :]:
        scope, focus, why, indicators, status = row[:5]
        if not focus:
            continue
        items.append(
            {
                "scope": scope,
                "allLobs": scope.casefold() == "all lobs",
                "focus": focus,
                "whyItMatters": why or None,
                "indicators": [item.strip() for item in indicators.split(";") if item.strip()],
                "status": status or None,
            }
        )
    return {"requests": requests, "focusItems": items, "dropped": dropped}


def load_keyword_library(workbook: Path, hierarchy: dict[str, Any] | None) -> dict[str, Any]:
    states = sheet_states(workbook)
    libraries = []
    for sheet in LIBRARY_SHEETS:
        rows = [
            (_clean(row[0]) if row else "", _clean(row[1]) if len(row) > 1 else "")
            for row in read_worksheet(workbook, sheet, keep_blank_rows=True)
        ]
        lob = resolve_lob(sheet, hierarchy)
        libraries.append(
            {"sheet": sheet, "sheetState": states.get(sheet), "canonicalLob": lob["lobName"] if lob else None, **parse_keyword_library_rows(rows)}
        )
    return {
        "registerId": "URS-KEYWORD-LIBRARY",
        "workbook": workbook.name,
        "absence": (
            "The sheets are hidden in the workbook and are not validated. Their notes cite internal documents and are not copied. "
            "A row that is a sentence or an instruction is listed under dropped. No term was added or reworded. "
            "A priority appears only where the sheet states one."
        ),
        "libraries": libraries,
    }


def load_competitors(workbook: Path, hierarchy: dict[str, Any] | None) -> dict[str, Any]:
    parsed = parse_competitor_rows(_cells(read_worksheet(workbook, COMPETITOR_SHEET, keep_blank_rows=True), 4))
    for item in parsed["competitors"]:
        lob = resolve_lob(item["lobLabel"], hierarchy)
        item["canonicalLob"] = lob["lobName"] if lob else None
    return {
        "registerId": "URS-COMPETITORS",
        "workbook": workbook.name,
        "sheet": COMPETITOR_SHEET,
        "absence": "A LOB label that is not on the org chart has no canonicalLob. The same competitor can appear under more than one LOB.",
        **parsed,
    }


def load_management_focus(workbook: Path, hierarchy: dict[str, Any] | None) -> dict[str, Any]:
    parsed = parse_management_focus_rows(_cells(read_worksheet(workbook, MANAGEMENT_SHEET, keep_blank_rows=True), 5))
    for item in parsed["focusItems"]:
        lob = resolve_lob(item["scope"], hierarchy)
        item["canonicalLob"] = lob["lobName"] if lob else None
    return {
        "registerId": "URS-MANAGEMENT-FOCUS",
        "workbook": workbook.name,
        "sheet": MANAGEMENT_SHEET,
        "absence": "The status text is copied unchanged. A status that says research draft means a business area has not validated the item.",
        **parsed,
    }
