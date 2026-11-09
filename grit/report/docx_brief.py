"""Render a brief model as a Word document in the style of the reference brief (docs/reference)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from grit.core.source_bands import LEGEND as SOURCE_LEGEND
from grit.report.brief import text_only
from grit.report.notices import DISCLAIMER

NAVY = RGBColor(0x1F, 0x38, 0x64)
GREY = RGBColor(0x55, 0x55, 0x55)
LIGHT = RGBColor(0x77, 0x77, 0x77)
BOX_FILL = {"WATCHPOINT": "FFF2CC", "ELEVATED": "FFF2CC", "STABLE": "E2EFDA", "NORMAL": "E2EFDA", "RED": "F8CBAD"}
# Format v2 (spec 44): while a brief renders, every cited source gets a number, [S1], [S2], ..., and its link goes to the
# source register at the end. None when a renderer is not inside render_docx.
_REGISTER: dict[str, Any] | None = None
# Defaults. A pack's scan plan can rename any section through sectionTitles.
SECTION_TITLES = {
    "ORDER_BOOK": "Order-book baseline and context",
    "PROJECTS": "Direct disruption to named projects",
    "MANAGEMENT_REQUESTS": "Management-requested topics",
    "ENTITY_PACKS": "Entity reader packs",
    "ENTITIES": "STE entities",
    "SUPPLIERS": "Disruption to named suppliers",
    "CUSTOMERS": "Customer relationships and upcoming award decisions",
    "COMPETITORS": "Competitor and industry watch",
    "HAZARDS": "Natural disasters and public health",
    "CONDITIONS": "Findings mapped to the standing items",
    "SCENARIOS": "Scenario outlook: best, medium and worst case for STE",
    "PRIORITY_FINDINGS": "Priority findings for this reader",
    "OTHER_SIGNALS": "Other signals in this reader's scope",
    "QA_LOG": "QA and correction log",
    "WATCHPOINTS": "Bottom line and watchpoints",
}



RED_LINES = {
    "CANDIDATE": "RED candidate: awaiting R&A impact confirmation.",
    "CONFIRMED": "RED impact confirmed by R&A.",
    "NOT_CONFIRMED": "RED candidate: R&A did not confirm impact.",
}

def section_title(meta: Mapping[str, Any], key: str) -> str:
    return (meta.get("section_titles") or {}).get(key) or SECTION_TITLES[key]
NOTHING = "No new item found this week in the checked sources."

# Format v2 report layout (URS methodology, sections 15, 22 and 24) -------------------------------------------------------
# The scan is wide and the report narrow: audit material goes to an appendix after the source register.
V2_APPENDIX = frozenset({"QA_LOG", "ENTITIES", "OTHER_SIGNALS"})
APPENDIX_LETTERS = "ABCDEFGH"
POSTURE_TITLE = "Recommended action posture"
NO_ASK = "Nothing new this week independently warrants escalation before the next weekly cycle."
FOLLOW_UPS_LABEL = "Follow-ups by owner, without escalation. "
FOLLOW_UPS_NOTE = "Each named row carries its suggested response."
CORRECTIONS_HEADERS = ("ID", "Earlier statement", "Why it was wrong or incomplete", "Corrected fact", "Source", "Earlier reports to update")
CORRECTIONS_NOTE = ("Material corrections carried forward from earlier reports (URS methodology, section 22). R&A records them, and "
                    "closes them when no longer needed, with `grit corrections`.")
NO_CORRECTIONS = "The corrections ledger has no open correction for this business area."


def v2_title(key: str, meta: Mapping[str, Any]) -> str:
    """A reader section's title in a format v2 report, where the bottom line is at the top, not with the watchpoints."""
    return "Watchpoints" if key == "WATCHPOINTS" else section_title(meta, key)


def section_plan(brief: Mapping[str, Any], renders: Any) -> tuple[list[tuple[str, str, Any]], list[tuple[str, str, Any]]]:
    """The body sections and the appendix sections that render, in reading order, as (key, title, renderer).
    renders(key, title, renderer) says whether a section has content."""
    meta, v2 = brief["meta"], brief.get("v2")
    plan: list[tuple[str, str, Any]] = []
    appendix: list[tuple[str, str, Any]] = []
    if v2:
        plan += [(title, title, renderer) for title, renderer in V2_SECTIONS]
    for key in meta["sections"]:
        if key == "BOTTOM_LINE" or (v2 and key in V2_REPLACES):
            continue
        renderer = _v2_watchpoints if v2 and key == "WATCHPOINTS" else _SECTIONS.get(key)
        if renderer is None:
            continue
        (appendix if v2 and key in V2_APPENDIX else plan).append((key, v2_title(key, meta) if v2 else section_title(meta, key), renderer))
    if v2:
        plan += [(title, title, renderer) for title, renderer in V2_CLOSING]
    keep = lambda rows: [row for row in rows if renders(*row)]  # noqa: E731
    return keep(plan), keep(appendix)


def posture_pointer(plan: Sequence[tuple[str, str, Any]], first: int) -> str:
    number = next((first + index for index, (key, *_rest) in enumerate(plan) if key == POSTURE_TITLE), None)
    return f"The principal ask and when to raise to RED are in section {number}, {POSTURE_TITLE}." if number else ""


def posture_lines(brief: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The action posture's principal ask and RED rule, as (label, text)."""
    lines = [("Principal ask. ", brief["bottom_line"].get("escalation") or NO_ASK)]
    triggers = [label[:1].lower() + label[1:] for label in (brief.get("red_triggers") or {}).values()]
    if triggers:
        lines.append(("When to raise to RED. ", f"The status is {brief['v2']['status']}. It becomes RED only when R&A confirms that one of these "
                                                 f"has happened to the business: {'; '.join(triggers)}."))
    return lines


def follow_ups(brief: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """Owners, each with the rows that suggest them a response: escalating items first, then standing items with an option."""
    from grit.report.format_v2 import short_text

    owners: dict[str, list[str]] = {}

    def add(owner: Any, ref: str) -> None:
        name = " ".join(str(owner or "").split())
        if name and name.casefold() not in {"to assign", "unassigned"} and ref not in owners.setdefault(name, []):
            owners[name].append(ref)

    for number, row in enumerate((brief.get("v2") or {}).get("escalating") or [], start=1):
        response = row.get("response") or {}
        if response.get("action"):
            add(response.get("owner"), f"escalating item {number}, {short_text(row['item'], 80)}")
    label = brief["meta"].get("standing_label") or "condition"
    for row in brief.get("conditions") or []:
        for option in row.get("options") or []:
            add(option.get("owner"), f"{label} {row['number']}, {row['name']}")
    return list(owners.items())


def correction_cells(row: Mapping[str, Any]) -> list[str]:
    return [row["id"], row["earlier"], row["why"], row["corrected"], row["source"], row.get("update_old") or "None stated"]


def _shade(cell: Any, fill: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shading)


def _hyperlink(paragraph: Any, text: str, url: str, *, size: float | None = None) -> None:
    part = paragraph.part
    rel = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1F3864")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(color)
    props.append(underline)
    if size:
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(int(size * 2)))
        props.append(sz)
    run.append(props)
    node = OxmlElement("w:t")
    node.text = text
    node.set(qn("xml:space"), "preserve")
    run.append(node)
    link.append(run)
    paragraph._p.append(link)


def _number(ref: Mapping[str, Any]) -> str:
    """The source's number in the register, given on first use."""
    numbers, refs = _REGISTER["numbers"], _REGISTER["refs"]
    key = str(ref.get("signal_id") or ref.get("url") or ref.get("title"))
    if key not in numbers:
        numbers[key] = f"S{len(numbers) + 1}"
        refs[key] = dict(ref)
    return numbers[key]


def _source_link(paragraph: Any, text: str, ref: Mapping[str, Any], *, size: float | None = None) -> None:
    """A title or outlet with its source: [S#] when the brief keeps links in its source register, else a hyperlink."""
    if _REGISTER is not None:
        run = paragraph.add_run(f"{text} [{_number(ref)}]")
        if size:
            run.font.size = Pt(size)
    elif ref.get("url"):
        _hyperlink(paragraph, text, str(ref["url"]), size=size)
    else:
        run = paragraph.add_run(text)
        if size:
            run.font.size = Pt(size)


def _marks(refs: Sequence[Mapping[str, Any]]) -> str:
    return ", ".join(_number(ref) for ref in refs) if _REGISTER is not None else ""


def _runs(paragraph: Any, rendered: Mapping[str, Any] | None, *, size: float | None = None, fallback: str = NOTHING) -> None:
    """Write text with its citations as linked '(Outlet, 1 Oct 2026)' references."""
    if not rendered:
        run = paragraph.add_run(fallback)
        if size:
            run.font.size = Pt(size)
        return
    for item in rendered["runs"]:
        if "text" in item:
            run = paragraph.add_run(item["text"].rstrip() if item is rendered["runs"][-1] else item["text"])
            if size:
                run.font.size = Pt(size)
            continue
        run = paragraph.add_run(" (" if not paragraph.text.endswith(" ") else "(")
        if size:
            run.font.size = Pt(size)
        for number, ref in enumerate(item["cite"]):
            if number:
                sep = paragraph.add_run("; ")
                if size:
                    sep.font.size = Pt(size)
            label = f"{ref['outlet']}, {ref['date']}" if ref.get("date") else str(ref["outlet"])
            _source_link(paragraph, label, ref, size=size)
        close = paragraph.add_run(")")
        if size:
            close.font.size = Pt(size)


def _heading(document: Any, text: str, level: int = 1) -> None:
    heading = document.add_heading(text, level=level)
    for run in heading.runs:
        run.font.color.rgb = NAVY


def _bullet(document: Any, label: str | None, rendered: Mapping[str, Any] | None, *, fallback: str = NOTHING) -> None:
    paragraph = document.add_paragraph(style="List Bullet")
    if label:
        paragraph.add_run(f"{label}: ").bold = True
    _runs(paragraph, rendered, fallback=fallback)


def _table(document: Any, headers: Sequence[str], widths: Sequence[float]) -> Any:
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, text, width in zip(table.rows[0].cells, headers, widths):
        cell.width = Cm(width)
        _shade(cell, "1F3864")
        run = cell.paragraphs[0].add_run(text)
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    return table


def _row(table: Any, values: Sequence[Any], widths: Sequence[float]) -> None:
    cells = table.add_row().cells
    for cell, value, width in zip(cells, values, widths):
        cell.width = Cm(width)
        paragraph = cell.paragraphs[0]
        if isinstance(value, Mapping) and "runs" in value:
            _runs(paragraph, value, size=9)
        else:
            run = paragraph.add_run(str(value if value is not None else ""))
            run.font.size = Pt(9)


def render_docx(brief: Mapping[str, Any], path: Path) -> Path:
    from grit.report.script import latin_copy

    brief = latin_copy(brief)
    meta = brief["meta"]
    document = Document()
    for section in document.sections:
        section.left_margin = section.right_margin = Cm(2.0)
        section.top_margin = section.bottom_margin = Cm(1.8)
    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    title = document.add_paragraph()
    run = title.add_run(meta["title"])
    run.bold, run.font.size, run.font.color.rgb = True, Pt(22), NAVY
    subtitle = document.add_paragraph()
    run = subtitle.add_run(meta["subtitle"])
    run.font.size, run.font.color.rgb = Pt(12), GREY
    line = document.add_paragraph()
    grounded = ", ".join(meta["grounded_in"])
    run = line.add_run(f"Scan window: {meta['window']}   |   Prepared for: {meta['prepared_for']}   |   Grounded in: {grounded}")
    run.italic, run.font.size, run.font.color.rgb = True, Pt(10), GREY
    status = document.add_paragraph()
    review = "Status: draft for review, not approved. " if meta.get("show_review_status", True) else ""
    run = status.add_run(f"{review}Reader profile: {meta['reader']}. Run {meta['run_id']}.")
    run.font.size, run.font.color.rgb = Pt(9), LIGHT
    notice = document.add_paragraph()
    run = notice.add_run(DISCLAIMER)
    run.italic, run.font.size, run.font.color.rgb = True, Pt(9), LIGHT

    global _REGISTER
    v2 = brief.get("v2")
    _REGISTER = {"numbers": {}, "refs": {}, "tiers": (v2 or {}).get("tiers") or {}} if v2 else None
    if v2:
        line = document.add_paragraph()
        run = line.add_run(f"STATUS: {v2['status']}")
        run.bold, run.font.size, run.font.color.rgb = True, Pt(12), NAVY
        detail = []
        if (v2.get("status_basis") or {}).get("basis"):
            detail.append(v2["status_basis"]["basis"])
        if v2["arrows"]:
            detail.append("Trend: " + "; ".join(v2["arrows"]) + ".")
        if v2["risk_window"]:
            detail.append("Next dated items: " + "; ".join(v2["risk_window"]) + ".")
        if detail:
            run = line.add_run("  " + " ".join(detail))
            run.font.size = Pt(9.5)

    plan, appendix = section_plan(brief, lambda key, title, renderer: renderer(document, brief, title, dry=True))
    number = 1 if v2 else 0
    bottom = brief["bottom_line"]
    box = document.add_table(rows=1, cols=1)
    box.style = "Table Grid"
    cell = box.rows[0].cells[0]
    _shade(cell, BOX_FILL.get(bottom["label"], "FFF2CC"))
    head = cell.paragraphs[0]
    run = head.add_run(f"{bottom['label']} — Bottom Line")
    run.bold, run.font.color.rgb = True, NAVY
    body = cell.add_paragraph()
    body.add_run(bottom["text"] or "")
    if v2:
        body.add_run(" " + posture_pointer(plan, number))
    elif bottom.get("escalation"):
        body.add_run(" " + bottom["escalation"])
    note = cell.add_paragraph()
    run = note.add_run(f"Drafted by the {bottom['drafted_by']}.")
    run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT

    for key, title, renderer in plan:
        renderer(document, brief, f"{number}. {title}", dry=False)
        number += 1
    if v2:
        _quality(document, brief, f"{number}. Quality and completeness check")
        number += 1
    _method(document, brief)
    if v2:
        _source_register(document, brief, f"{number}. Source register")
    for letter, (key, title, renderer) in zip(APPENDIX_LETTERS, appendix):
        renderer(document, brief, f"Appendix {letter}. {title}", dry=False)
    _REGISTER = None
    _apply_first_use(document)
    _footers(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    return path


def _order_book(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    book = brief.get("order_book")
    if not book:
        return False
    if dry:
        return True
    _heading(document, title)
    document.add_paragraph(f"Per the {book['source']}, the internal materiality anchors for Taiwan rail concentration risk are:")
    for anchor in book.get("anchors") or []:
        text = f"{anchor['label']}: {anchor['value']}" + (f" — {anchor['note']}" if anchor.get("note") else "")
        document.add_paragraph(text, style="List Bullet")
    if book.get("contracts"):
        consortium = f" ({book['consortium']})" if book.get("consortium") else ""
        document.add_paragraph(f"Confirmed existing turnkey contracts{consortium}:")
        for contract in book["contracts"]:
            awarded = f" (awarded {contract['awarded']})" if contract.get("awarded") else ""
            document.add_paragraph(f"{contract['label']} — {contract['value']}{awarded}", style="List Bullet")
    note = document.add_paragraph()
    run = note.add_run(
        "These figures anchor materiality for any finding below. A development is escalated only where it credibly connects to one of these named lines, "
        f"suppliers, or figures. Register status: {book.get('status')}; a named BA owner must confirm the figures."
    )
    run.font.size = Pt(9)
    return True


def _projects(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return bool(brief.get("projects"))
    _heading(document, title)
    for group in brief["projects"]:
        _heading(document, group["heading"], level=3)
        found = [item for item in group["items"] if item["text"]]
        for item in found:
            _bullet(document, f"{item['name']} ({item['tier']})", item["text"])
        quiet = [item["name"] for item in group["items"] if not item["text"]]
        if quiet:
            document.add_paragraph(f"{', '.join(quiet)}: no new disruption found this week.", style="List Bullet")
    return True


def _named(document: Any, items: Sequence[Mapping[str, Any]], title: str, intro: str) -> None:
    _heading(document, title)
    document.add_paragraph(intro)
    found = [item for item in items if item["text"]]
    for item in found:
        _bullet(document, item["name"], item["text"])
    quiet = [item["name"] for item in items if not item["text"]]
    if quiet:
        document.add_paragraph(f"{', '.join(quiet)}: nil adverse change found this week.", style="List Bullet")


def _suppliers(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return bool(brief.get("suppliers"))
    names = ", ".join(item["name"] for item in brief["suppliers"])
    _named(document, brief["suppliers"], title, f"Checked individually against the reader pack's supplier list ({names}).")
    note = document.add_paragraph()
    run = note.add_run("Smaller suppliers are thinly covered in English-language press. Cross-check Sphera supplier-risk alerts directly.")
    run.font.size = Pt(9)
    return True


def _customers(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return bool(brief.get("customers"))
    _named(document, brief["customers"], title, "Checked for award decisions, payment, corruption or fraud news, and operating incidents.")
    return True


def _hazards(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return bool(brief.get("hazards"))
    _heading(document, title)
    for item in brief["hazards"]:
        _heading(document, item["name"], level=3)
        _bullet(document, None, item["text"], fallback="No item found this week that places this hazard near a project site or warehouse.")
    return True


OPTIONS_NOTE = "Proposed by GRIT-Scan for management consideration. Not reviewed advice."
OPTION_KINDS = {
    "STRENGTHEN_CONTROL": "Strengthens a key control", "ACCELERATE_ACTION_PLAN": "Brings forward an action plan",
    "ADDRESS_GAP": "Covers a gap in the controls", "MONITOR": "Continue monitoring",
}


def option_line(option: Mapping[str, Any]) -> str:
    """The plain metadata after an option: its kind, owner, what it builds on, and its trigger."""
    parts = [OPTION_KINDS.get(str(option.get("kind")), str(option.get("kind"))), f"Owner: {option.get('owner')}"]
    if option.get("builds_on"):
        parts.append(f"Builds on: \u201c{option['builds_on']}\u201d")
    if option.get("trigger"):
        parts.append(f"Trigger: {option['trigger']}")
    return ". ".join(parts) + "."


def key_risk_headers(meta: Mapping[str, Any], *, options: bool) -> tuple[str, ...]:
    """The key-risk table's columns, in the R&A reviewer's wording. The response column needs RESPONSE_OPTIONS in the profile."""
    short = meta.get("pack_short") or meta.get("pack_label") or "the business area"
    headers = ("News/Developments", "News/Information Source", "Date and Time", f"Relevance/Potential Impact to {short}")
    return headers + (("Proposed Response/Management Consideration",) if options else ()) + ("Priority/Trend",)


def priority_trend(row: Mapping[str, Any]) -> list[str]:
    lines = [row.get("group") or ("Group condition" if row.get("radar") else "Standing item")]
    if row.get("priority"):
        lines.append(f"Priority: {row['priority']}")
    return lines + [f"Trend: {row['trend']}", f"Confidence: {row['confidence']}"]


def implication_text(row: Mapping[str, Any]) -> str:
    if row.get("implication"):
        return text_only(row["implication"]["runs"])
    if row.get("tier") == "KEY" or row.get("radar"):
        return "Not established this period: no implication passed its checks."
    return "Not drafted: implications are written for key risks only."


def opportunity_text(row: Mapping[str, Any]) -> str:
    return text_only(row["opportunity"]["runs"]) if row.get("opportunity") else "None evident in this period's sources."


RADAR_HEADERS = ("Theme and what changed", "Sources, date and time", "Risk and opportunity for STE", "Business areas", "Counter-indicator")


def radar_headers(*, options: bool) -> tuple[str, ...]:
    """The Group Strategic Radar's columns (spec 37). The response column needs RESPONSE_OPTIONS in the profile."""
    return RADAR_HEADERS + (("Proposed response",) if options else ()) + ("Priority and trend",)


def radar_cells(row: Mapping[str, Any], *, options: bool) -> list[list[str]]:
    """One radar row as lists of lines, one list per column."""
    refs = row.get("sources") or []
    cells = [
        [f"{row['number']}. {row['name']}", row["development"]],
        [f"{number}. {ref['outlet']} ({ref.get('when') or ref.get('date') or 'date not stated'})" for number, ref in enumerate(refs, start=1)] or ["No source this period."],
        [f"Risk: {implication_text(row)}", f"Opportunity: {opportunity_text(row)}"],
        [", ".join(row.get("business_areas") or []) or "Not mapped yet."],
        [row.get("counter_indicator") or "Not established this period."],
    ]
    if options:
        cells.append([f"\u2022 {option} ({detail})" for option, detail in response_lines(row)] or ["No option proposed: no escalation and no material source."])
    cells.append(priority_trend(row))
    return cells


def response_lines(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Each option as (the option, its kind, owner, what it builds on, and its trigger)."""
    return [(text_only(option["text"]["runs"]), option_line(option)) for option in row.get("options") or []]


def _cell_lines(cell: Any, lines: Sequence[str], *, size: float = 8.5, bold_first: bool = False) -> None:
    for number, line in enumerate(lines):
        paragraph = cell.paragraphs[0] if number == 0 else cell.add_paragraph()
        run = paragraph.add_run(line)
        run.font.size = Pt(size)
        run.bold = bold_first and number == 0


def _orient(document: Any, landscape: bool) -> None:
    """Start a new page in landscape or portrait. The key-risk table needs the width of a landscape page."""
    current = document.sections[-1]
    section = document.add_section(WD_SECTION.NEW_PAGE)
    width, height = sorted((current.page_width, current.page_height), reverse=landscape)
    section.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = width, height


def _key_risk_table(document: Any, brief: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> None:
    options = any(row.get("options") for row in rows) or "RESPONSE_OPTIONS" in (brief["meta"].get("sections") or [])
    headers = key_risk_headers(brief["meta"], options=options)
    widths = (5.6, 3.6, 2.6, 5.0, 6.0, 2.9) if options else (7.4, 4.2, 2.8, 7.6, 3.7)
    table = _table(document, headers, widths)
    for row in rows:
        cells = table.add_row().cells
        for cell, width in zip(cells, widths):
            cell.width = Cm(width)
        _cell_lines(cells[0], [f"{row['number']}. {row['name']}", row["development"]], bold_first=True)
        for number, ref in enumerate(row.get("sources") or [], start=1):
            paragraph = cells[1].paragraphs[0] if number == 1 else cells[1].add_paragraph()
            paragraph.add_run(f"{number}. ").font.size = Pt(8.5)
            _source_link(paragraph, str(ref["outlet"]), ref, size=8.5)
            if ref.get("title"):
                title = paragraph.add_run(f": {str(ref['title'])[:90]}")
                title.font.size, title.italic = Pt(8), True
        if not row.get("sources"):
            _cell_lines(cells[1], ["No source this week."])
        _cell_lines(cells[2], [f"{number}. {ref.get('when') or ref.get('date') or 'Date not stated'}" for number, ref in enumerate(row.get("sources") or [], start=1)] or ["Not applicable."])
        _cell_lines(cells[3], [implication_text(row)])
        if options:
            lines = response_lines(row)
            for number, (option, detail) in enumerate(lines):
                paragraph = cells[4].paragraphs[0] if number == 0 else cells[4].add_paragraph()
                paragraph.add_run(f"\u2022 {option} ").font.size = Pt(8.5)
                meta = paragraph.add_run(detail)
                meta.font.size, meta.font.color.rgb = Pt(7.5), GREY
            if not lines:
                _cell_lines(cells[4], ["No option proposed: the risk is not escalating and cites no material source." if row.get("tier") == "KEY" else "Options are proposed for key risks only."])
        _cell_lines(cells[-1], priority_trend(row))
    if options:
        note = document.add_paragraph()
        run = note.add_run(OPTIONS_NOTE)
        run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT


def _radar_table(document: Any, brief: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> None:
    options = any(row.get("options") for row in rows) or "RESPONSE_OPTIONS" in (brief["meta"].get("sections") or [])
    headers = radar_headers(options=options)
    widths = (5.0, 3.4, 5.2, 2.4, 3.6, 4.4, 2.3) if options else (6.0, 3.8, 6.4, 2.8, 4.4, 2.6)
    table = _table(document, headers, widths)
    for row in rows:
        cells = table.add_row().cells
        for cell, width, lines in zip(cells, widths, radar_cells(row, options=options)):
            cell.width = Cm(width)
            _cell_lines(cell, lines, bold_first=cell is cells[0])
    note = document.add_paragraph()
    run = note.add_run(
        "Movement is measured against the signed-off Group baseline, or the previous period until Wilson signs it off. Business areas are proposed. "
        + (OPTIONS_NOTE if options else "")
    )
    run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT


def _conditions(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return bool(brief.get("conditions"))
    if brief["meta"].get("level") == "GROUP":
        _orient(document, landscape=True)
        _heading(document, title)
        _radar_table(document, brief, brief["conditions"])
        _orient(document, landscape=False)
        return True
    prior = brief["meta"].get("prior_run_date")
    widths = (0.8, 3.6, 2.2, 2.0, 8.6)
    themes = brief["meta"].get("standing_label") == "risk theme"
    full = [row for row in brief["conditions"] if row.get("full")] if themes else []
    rest = [row for row in brief["conditions"] if row not in full]
    if full:
        _orient(document, landscape=True)
    _heading(document, title)
    if full:
        _heading(document, "Key risks" if all(row.get("tier") == "KEY" for row in full) else "Key risks and escalating themes", level=2)
        _key_risk_table(document, brief, full)
    if rest:
        if full:
            _heading(document, "Other risk themes", level=2)
        label = "Risk theme" if themes else "Condition"
        table = _table(document, ("#", label, f"Trend vs {prior}" if prior else "Trend", "Confidence", "Basis / source and date"), widths)
        for row in rest:
            name = f"{row['name']} ({row['group']})" if row.get("group") else row["name"]
            if row.get("covered_by"):
                name += f". Draws on the {row['covered_by']} entity pack."
            _row(table, (row["number"], name, row["trend"], row["confidence"], row["basis"]), widths)
    note = document.add_paragraph()
    run = note.add_run("Trend is the Assessor's proposal, validated against the cited sources. Confidence is computed by rule from the cited outlets and their register ratings.")
    run.font.size, run.font.color.rgb = Pt(8), LIGHT
    if full:
        _orient(document, landscape=False)
    return True


def _requests(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    rows = brief.get("management_requests") or []
    if dry:
        return bool(rows)
    _heading(document, title)
    document.add_paragraph("A signal matches a request by its approved keywords in the title or lead. A match is news priority P1 (spec 29).")
    for row in [item for item in rows if item["status"] == "ACTIVE"]:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.add_run(row["title"]).bold = True
        paragraph.add_run(f" ({row['source'] or row['requested_by']})")
        if not row["signals"]:
            paragraph.add_run(": no signal matched this week.")
            continue
        for item in row["signals"]:
            line = document.add_paragraph()
            line.paragraph_format.left_indent = Cm(1.0)
            _source_link(line, item["title"], item, size=10)
            line.add_run(f" ({item['outlet']}, {item['date'] or 'date not stated'}{', ' + item['via'] if item.get('via') else ''})").font.size = Pt(10)
    proposed = [row["title"] for row in rows if row["status"] == "PROPOSED"]
    if proposed:
        note = document.add_paragraph()
        run = note.add_run("Proposed and not yet active, so not searched and no P1: " + "; ".join(proposed) + ". A reviewer activates a request on the dashboard.")
        run.font.size = Pt(9)
    return True


def _entity_packs(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    rows = brief.get("entity_packs") or []
    if dry:
        return bool(rows)
    _heading(document, title)
    for row in rows:
        _heading(document, row["label"], level=3)
        if not row["run_id"]:
            document.add_paragraph(f"No {row['label']} run on this date.")
            continue
        document.add_paragraph(row["bottom_line"] or "The entity pack's bottom line was withheld this week. See its brief.")
        if row.get("moved"):
            document.add_paragraph("Moved this week: " + "; ".join(row["moved"]) + ".", style="List Bullet")
        note = document.add_paragraph()
        run = note.add_run(f"The full detail is in the {row['label']} weekly scan, run {row['run_id']}.")
        run.font.size = Pt(9)
    return True


def _entities(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    groups = brief.get("entities") or []
    if dry:
        return bool(groups)
    _heading(document, title)
    for group in groups:
        _heading(document, group["heading"], level=3)
        for item in [entry for entry in group["items"] if entry["text"]]:
            _bullet(document, f"{item['name']}" + (f" ({item['lob']})" if item.get("lob") else ""), item["text"])
        quiet = [entry["name"] for entry in group["items"] if not entry["text"]]
        if quiet:
            paragraph = document.add_paragraph(style="List Bullet")
            run = paragraph.add_run(f"No new item found this week: {', '.join(quiet)}.")
            run.font.size = Pt(10)
    return True


def _competitors(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    rows = brief.get("competitors") or []
    if dry:
        return True
    _heading(document, title)
    found = [row for row in rows if row["status"] == "FOUND"]
    if not rows:
        document.add_paragraph("No registered competitor was named in this week's signals. Competitor names are searched on an eight-week rotation.")
        return True
    for row in found:
        _bullet(document, row["name"], row["text"])
    passing = [row["name"] for row in rows if row["status"] != "FOUND"]
    if passing:
        document.add_paragraph(f"Named in passing only: {', '.join(passing)}.")
    return True


def _findings(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return bool(brief.get("findings"))
    _heading(document, title)
    document.add_paragraph("Ordered by this reader's news priority (P1 management request, P2 direct exposure, P3 competitor or sector, P4 customer), then briefing tier.")
    for item in brief["findings"]:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.add_run((f"[{item['priority']} · tier {item['tier']}] " if item['priority'] else f"[tier {item['tier']}] ")).bold = True
        _source_link(paragraph, item["title"], item)
        paragraph.add_run(f" ({item['outlet']}, {item['date'] or 'date not stated'})")
        detail = document.add_paragraph()
        detail.paragraph_format.left_indent = Cm(1.0)
        lines = [line for line in item["why"] if line]
        if item.get("card") and item["card"].get("what_happened"):
            lines.insert(0, str(item["card"]["what_happened"]))
        if item.get("card") and item["card"].get("hypothesis"):
            lines.append(f"Hypothesis, not a forecast: {item['card']['hypothesis']} Link confidence: {item['card']['confidence']}.")
        from grit.report.suggestions import suggestions_for_signal

        lines.extend(suggestions_for_signal(brief, item["signal_id"]))
        if item["risks"]:
            lines.append("Risk register: " + "; ".join(item["risks"]) + ".")
        lines.append(item["harm"])
        if item.get("red_status"):
            lines.append(RED_LINES[item["red_status"]])
        run = detail.add_run(" ".join(lines))
        run.font.size = Pt(9)
    return True


def _others(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return bool(brief.get("other_signals"))
    _heading(document, title)
    for item in brief["other_signals"]:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.add_run((f"[{item['priority']}] " if item['priority'] else ""))
        _source_link(paragraph, item["title"], item, size=10)
        paragraph.add_run(f" ({item['outlet']}, {item['date'] or 'date not stated'})").font.size = Pt(10)
    return True


def _qa(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    """Per-signal QA lines and the research-coverage list: appendix only, after the source register."""
    if dry:
        return True
    _heading(document, title)
    if not brief["qa"]:
        document.add_paragraph("The Critic raised no supported issue this week, and every draft passed its checks.")
        return True
    document.add_paragraph(
        "What changed or was withheld as a direct result of this week's checks and the Critic's review, "
        "including research-coverage notes.")
    widths = (4.0, 5.0, 8.2)
    table = _table(document, ("Item", "Prior version stated", "Correction after this QA pass"), widths)
    for row in brief["qa"]:
        _row(table, (row["item"], row["prior"] or "", row["correction"]), widths)
    return True


SCENARIO_HEADERS = ("Strategic shift", "Where the signposts lean", "Signposts seen, with sources", "Counter-indicators seen", "Responses now triggered", "Where STE could gain")
SCENARIO_NOTE = ("Each shift has a reviewed best, medium and worst case for STE. A signpost counts as seen when a kept signal in the period touches the "
                 "shift and names its keywords. These are counts of signposts, not odds.")


def _ref_text(item: Mapping[str, Any]) -> str:
    refs = "; ".join(f"{ref['outlet']}, {ref['date'] or 'date not stated'}" for ref in item.get("sources") or [])
    more = f"; and {item['more']} more" if item.get("more") else ""
    return f"({refs}{more})" if refs else ""


def scenario_cells(reading: Mapping[str, Any]) -> list[list[str]]:
    """One row of the scenario outlook, as lines per cell: shared by the Word, Markdown, Excel and deck renderers."""
    from grit.pipeline.scenarios import lean_text

    def line(case: Mapping[str, Any], item: Mapping[str, Any]) -> str:
        quote = f" Evidence: \u201c{item['quotes'][0]}\u201d" if item.get("quotes") else ""
        return f"{case['title'].split(' case')[0]}: {item['text']}{quote} {_ref_text(item)}".strip()

    seen = [line(case, item) for case in reading["cases"] for item in case["signposts_seen"]]
    against = [line(case, item) for case in reading["cases"] for item in case["counter_indicators_seen"]]
    from grit.report.notices import HYPOTHESIS_LABEL, suggestion_line

    triggered = [line for case in reading["cases"] for response in case["responses_triggered"] if (line := suggestion_line(f"{response['action']} Owner: {response['owner']}."))]
    gains = [f"{case['title'].split(' case')[0]}: {HYPOTHESIS_LABEL} {case['where_ste_could_gain']}" for case in reading["cases"] if case.get("where_ste_could_gain")]
    names = [f"{case['title'].split(' for')[0]}: {case['name']}" for case in reading["cases"]]
    return [[reading["name"], *names], [lean_text(reading)], seen or ["None this period."], against or ["None this period."], triggered or ["None."], gains or ["None in the reviewed scenarios."]]


def _scenarios(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    outlook = brief.get("scenarios")
    if dry:
        return bool(outlook and outlook.get("shifts"))
    _orient(document, landscape=True)
    _heading(document, title)
    widths = (4.6, 3.2, 6.2, 3.8, 3.8, 4.0)
    table = _table(document, SCENARIO_HEADERS, widths)
    for reading in outlook["shifts"]:
        cells = table.add_row().cells
        for cell, width, lines in zip(cells, widths, scenario_cells(reading)):
            cell.width = Cm(width)
            _cell_lines(cell, lines, bold_first=cell is cells[0])
    if outlook.get("indicators"):
        from grit.pipeline.indicators import indicator_line

        document.add_paragraph("Leading indicators. Green: no action. Amber: BAs review exposure and plans. Red: BAs act on the planned responses. The bounds are proposed by GRIT-Scan until Wilson confirms them.")
        for item in outlook["indicators"]:
            document.add_paragraph(f"{item['name']}: {indicator_line(item)} Applies to {', '.join(item['applies_to'])}.", style="List Bullet")
    if outlook.get("risk_links"):
        document.add_paragraph("Group risks and the business-area and LOB risks under them. A blank LOB cell means the register does not name one. The crosswalk is proposed until a named person confirms it.")
        link_widths = (6.0, 8.0, 6.0, 3.0)
        links = _table(document, ("Group risk", "BA risks", "LOB risks", "Status"), link_widths)
        for row in outlook["risk_links"]:
            _row(links, (
                f"{row['group_risk']} {row['name']}".strip(),
                "; ".join(f"{item['id']} {item['name']}" for item in row["ba"]) or "",
                "; ".join(item["name"] for item in row["lob"]) or "",
                row["status"],
            ), link_widths)
    note = document.add_paragraph()
    run = note.add_run(f"{SCENARIO_NOTE} Scenarios v{outlook['version']}, reviewed by {outlook['reviewed_by']} on {outlook['reviewed_on']}.")
    run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT
    _orient(document, landscape=False)
    return True


def red_line(alert: Mapping[str, Any], triggers: Mapping[str, str]) -> tuple[str, str]:
    """A RED candidate and the R&A decision on it, as (label, text)."""
    status = alert.get("status") or "CANDIDATE"
    decision = alert.get("decision") or {}
    label = {"CONFIRMED": "RED confirmed: ", "NOT_CONFIRMED": "RED not confirmed: "}.get(status, "RED candidate: ")
    rule = f" — rule {(alert.get('rule') or {}).get('rule_id')}"
    if status == "CANDIDATE":
        return label, f"{alert['title']}{rule}. Awaiting R&A impact confirmation."
    trigger = triggers.get(str(decision.get("trigger_id")))
    return label, f"{alert['title']}{rule}. {decision.get('decided_by')}, R&A: {decision.get('impact')}" + (f" Trigger: {trigger}." if trigger else "")


def _watchpoint_bullets(document: Any, rows: Sequence[Mapping[str, Any]]) -> None:
    if rows:
        document.add_paragraph("Concrete watchpoints carried into next cycle:")
    for row in rows:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.add_run(f"{row['title']}").bold = True
        paragraph.add_run(f" ({row['status'].lower()}): {row['detail']} ")
        _runs(paragraph, row["update"], fallback="")


def _watchpoints(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return True
    _heading(document, title)
    document.add_paragraph(brief["bottom_line"]["text"] or "")
    _watchpoint_bullets(document, [row for row in brief["watchpoints"] if row["status"] != "CLOSED"])
    for alert in brief.get("red") or []:
        label, text = red_line(alert, brief.get("red_triggers") or {})
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.add_run(label).bold = True
        paragraph.add_run(text)
    if brief.get("red") and brief.get("red_triggers"):
        note = document.add_paragraph()
        run = note.add_run("A rule match is a RED candidate, not RED. RED needs R&A to confirm that one of these happened to the business: "
                           + "; ".join(brief["red_triggers"].values()) + ".")
        run.font.size = Pt(9)
    document.add_paragraph(f"Recommended action posture: {brief['bottom_line']['escalation']}")
    return True


def _method(document: Any, brief: Mapping[str, Any]) -> None:
    method = brief["method"]
    _heading(document, "How this brief was produced", level=2)
    lines = [
        f"{method['queries']} {method.get('queries_label') or 'queries'}, {method['signals']} pages, {method['kept']} kept by the deterministic keep rule, {method['shown_to_reader']} in this reader's view "
        f"({method['hidden_by_routing']} outside the aperture). {method['profile']} The profile reordered {method['moved_by_profile']} signal(s) within their tiers and hid none.",
        "Agents proposed the text. Deterministic checks rejected any sentence with an unquoted claim, a number not in its source, or a governed score word. "
        "Trend labels, confidence, priority, the harm verdict and RED status are computed by rule. Nothing here is approved or published.",
    ]
    if method["agents"]:
        lines.append("Agent calls: " + "; ".join(f"{row['actor']} {row['version'] or ''} {row['status']} ×{row['calls']}".replace("  ", " ") for row in method["agents"]) + ".")
    for line in lines:
        paragraph = document.add_paragraph()
        run = paragraph.add_run(line)
        run.font.size, run.font.color.rgb = Pt(8), LIGHT
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run(f"Content hash {brief['content_hash'][:16]}")
    run.font.size, run.font.color.rgb = Pt(7), LIGHT


def _apply_first_use(document: Any) -> None:
    """Record claims in table cells, then drop a later paragraph that repeats one. A hyperlink stays as written."""
    from docx.text.paragraph import Paragraph

    from grit.report.repetition import FirstUse

    state = FirstUse()
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:tbl"):
            for node in child.iter(qn("w:p")):
                state.observe(Paragraph(node, document).text)
            continue
        if child.tag != qn("w:p"):
            continue
        paragraph = Paragraph(child, document)
        if paragraph._p.find(".//" + qn("w:hyperlink")) is not None:
            state.observe(paragraph.text)
            continue
        updated = state.filter(paragraph.text)
        if updated != paragraph.text and paragraph.runs:
            paragraph.runs[0].text = updated
            for run in paragraph.runs[1:]:
                run.text = ""


def _footers(document: Any) -> None:
    for section in document.sections:
        footer = section.footer
        footer.is_linked_to_previous = False
        paragraph = footer.paragraphs[0]
        paragraph.text = DISCLAIMER
        for run in paragraph.runs:
            run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT


_SECTIONS = {
    "ORDER_BOOK": _order_book,
    "MANAGEMENT_REQUESTS": _requests,
    "ENTITY_PACKS": _entity_packs,
    "ENTITIES": _entities,
    "COMPETITORS": _competitors,
    "PROJECTS": _projects,
    "SUPPLIERS": _suppliers,
    "CUSTOMERS": _customers,
    "HAZARDS": _hazards,
    "CONDITIONS": _conditions,
    "SCENARIOS": _scenarios,
    "PRIORITY_FINDINGS": _findings,
    "OTHER_SIGNALS": _others,
    "QA_LOG": _qa,
    "WATCHPOINTS": _watchpoints,
}


# Format v2 sections (spec 44) -----------------------------------------------------------------------------------------

V2_NOTE = "Rows are AI-drafted suggestions for consideration, checked by rule against their sources. A named person reviews them before anyone acts."


def _text_cell(cell: Any, text: str, *, size: float = 8.5, bold: bool = False) -> None:
    run = cell.paragraphs[0].add_run(text)
    run.font.size, run.bold = Pt(size), bold


def _marks_cell(cell: Any, refs: Sequence[Mapping[str, Any]], *, size: float = 8.5) -> None:
    _text_cell(cell, _marks(refs) or "No source", size=size)


def _note(document: Any, text: str) -> None:
    run = document.add_paragraph().add_run(text)
    run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT


def _exposure(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    """The approved exposure snapshot (URS methodology, section 15.2), or a statement that there was none."""
    from grit.report.markdown import EXPOSURE_TABLES

    if dry:
        return True
    _heading(document, title)
    snapshot = brief["v2"].get("exposure")
    if not snapshot:
        document.add_paragraph("No approved internal exposure snapshot this week. Staff, site and project exposure rests on public sources only.")
        return True
    document.add_paragraph(f"Approved internal data as of {snapshot.get('asOf')}, approved by {snapshot.get('approvedBy')}. "
                           f"Sources: {'; '.join(snapshot.get('sources') or []) or 'not stated'}.")
    for key, label, headers, fields in EXPOSURE_TABLES:
        if not snapshot.get(key):
            continue
        document.add_paragraph().add_run(label).bold = True
        widths = tuple(round(16.0 / len(headers), 1) for _ in headers)
        table = _table(document, headers, widths)
        for row in snapshot[key]:
            cells = table.add_row().cells
            for cell, field in zip(cells, fields):
                _text_cell(cell, str(row.get(field) if row.get(field) not in (None, "") else "Not recorded"))
    return True


def _escalating(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    rows = brief["v2"]["escalating"]
    if dry:
        return True
    _heading(document, title)
    if not rows:
        document.add_paragraph("No event met the escalation rule this week (spec 44, section 2).")
        return True
    short = brief["v2"]["short"]
    widths = (0.7, 3.0, 4.6, 4.0, 3.4, 1.4)
    table = _table(document, ("#", "Item and risk", "Summary", f"Implications for {short}", "Suggested response (owner)", "Sources"), widths)
    for number, row in enumerate(rows, start=1):
        cells = table.add_row().cells
        for cell, width in zip(cells, widths):
            cell.width = Cm(width)
        _text_cell(cells[0], f"{number}")
        _cell_lines(cells[1], [f"{row['band']} {row['item']}", f"[{row['risk']}]" if row["risk"] else "No register risk matched."], bold_first=True)
        if row.get("summary"):
            _runs(cells[2].paragraphs[0], row["summary"], size=8.5)
            _runs(cells[3].paragraphs[0], row["implications"], size=8.5)
            response = row.get("response") or {}
            _cell_lines(cells[4], [str(response.get("action") or ""), f"Owner: {response.get('owner') or 'to assign'}."])
        else:
            _cell_lines(cells[2], ["The drafted row failed its checks, so only the lead report is shown. See the QA log."])
            _cell_lines(cells[3], ["Not drafted."])
            _cell_lines(cells[4], ["Not drafted."])
        _marks_cell(cells[5], row.get("sources") or [])
    _note(document, V2_NOTE)
    return True


def _changes(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    rows = brief["v2"]["changes"]
    if dry:
        return bool(rows)
    _heading(document, title)
    prior = brief["meta"].get("prior_run_date") or "the last report"
    widths = (4.6, 4.6, 5.0, 2.9)
    table = _table(document, ("Item", f"Last report ({prior})", "This report", "Direction"), widths)
    for row in rows:
        _row(table, [f"{row['item']}" + (f" [{row['risk']}]" if row["risk"] else ""), row["before"], row["now"] + (f" [{_marks(row['sources'])}]" if row.get("sources") and _REGISTER is not None else ""), row["direction"]], widths)
    return True


def _look_ahead(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    rows = brief["v2"]["look_ahead"]
    if dry:
        return True
    _heading(document, title)
    if rows:
        widths = (2.2, 9.4, 3.8, 1.7)
        table = _table(document, ("Date", "Event, as the source states it", f"Relevance to {brief['v2']['short']}", "Sources"), widths)
        for row in rows:
            when = date.fromisoformat(row["date"])
            _row(table, [f"{when.strftime('%a')} {when.day} {when.strftime('%b')}", row["event"], row["relevance"] or "Linked by the scan", _marks(row["sources"])], widths)
        _note(document, "Dates come only from the week's sources, as stated. Nothing here is a forecast.")
    else:
        document.add_paragraph("No source linked to the business area states a dated event in the next 30 days.")
    indicators = (brief.get("scenarios") or {}).get("indicators") or []
    if indicators:
        from grit.pipeline.indicators import indicator_line

        document.add_paragraph("Leading indicators:")
        for item in indicators:
            document.add_paragraph(f"{item['name']}: {indicator_line(item)}", style="List Bullet")
    return True


def _priorities(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    rows = brief["v2"]["priorities"]
    if dry:
        return bool(rows)
    _heading(document, title)
    widths = (0.7, 7.0, 2.6, 3.4, 3.4)
    table = _table(document, ("#", "Item", "Trend", "Risk linkage", "Owner or forum"), widths)
    for number, row in enumerate(rows, start=1):
        item = row["item"] + (f" [{_marks(row['sources'])}]" if row.get("sources") and _REGISTER is not None else "")
        _row(table, [number, item, row["trend"], row["risk"] or "No register risk matched", row["owner"]], widths)
    return True


def _coverage(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    v2 = brief["v2"]
    if dry:
        return bool(v2["coverage"])
    _heading(document, title)
    document.add_paragraph("Each cell is a finding; a nil from a search aimed at that line of business; a nil from business-area-wide searches only; "
                           "or not searched this week. A nil is never claimed without a search, and its label says how wide that search was.")
    widths = (3.0, *(2.82 for _ in v2["categories"]))
    table = _table(document, ("Line of business", *v2["categories"]), widths)
    for line in v2["coverage"]:
        values = []
        for category in v2["categories"]:
            cell = line["cells"][category]
            text = cell["text"] + (f" [{_marks(cell['sources'])}]" if cell.get("sources") and _REGISTER is not None else "")
            values.append(text + (f" (and {cell['more']} more)" if cell.get("more") else ""))
        _row(table, [line["lob"], *values], widths)
    return True


def _jurisdictions(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    rows = brief["v2"]["jurisdictions"]
    if dry:
        return bool(rows)
    _heading(document, title)
    widths = (2.6, 5.4, 7.6, 1.5)
    table = _table(document, ("Jurisdiction", f"{brief['v2']['short']} entities", "In-window finding", "Trend"), widths)
    for row in rows:
        entities = "; ".join(row["entities"]) + (f"; and {row['more_entities']} more" if row["more_entities"] else "")
        finding = row["finding"] + (f" [{_marks(row['sources'])}]" if row.get("sources") and _REGISTER is not None else "")
        _row(table, [row["country"], entities, finding, row["trend"]], widths)
    return True


def _watch(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    v2 = brief["v2"]
    if dry:
        return bool(v2["watch"]) or bool(v2["watch_note"]["total"])
    _heading(document, title)
    if v2["watch"]:
        widths = (4.4, 2.2, 10.5)
        table = _table(document, ("Name", "Type", "This week"), widths)
        for row in v2["watch"]:
            _row(table, [row["name"], row["type"], row["text"]], widths)
    note = v2["watch_note"]
    document.add_paragraph(f"{note['total'] - note['not_searched']} of {note['total']} named suppliers, customers and competitors were searched or reported this week. "
                           "The others rotate on the scan plan's cadence and were not searched this week.")
    return True


def _v2_watchpoints(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    """Open watchpoints only. A format v2 report opens with its bottom line and keeps RED in the action posture."""
    rows = [row for row in brief["watchpoints"] if row["status"] != "CLOSED"]
    if dry:
        return bool(rows)
    _heading(document, title)
    _watchpoint_bullets(document, rows)
    return True


def _posture(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    """The recommended action posture (URS methodology, section 15): one principal ask, when the status becomes RED,
    and the follow-ups by owner."""
    if dry:
        return True
    _heading(document, title)
    for label, text in posture_lines(brief):
        paragraph = document.add_paragraph()
        paragraph.add_run(label).bold = True
        paragraph.add_run(text)
    for alert in brief.get("red") or []:
        label, text = red_line(alert, brief.get("red_triggers") or {})
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.add_run(label).bold = True
        paragraph.add_run(text)
    owners = follow_ups(brief)
    if owners:
        paragraph = document.add_paragraph()
        paragraph.add_run(FOLLOW_UPS_LABEL).bold = True
        paragraph.add_run(FOLLOW_UPS_NOTE)
        for owner, refs in owners:
            paragraph = document.add_paragraph(style="List Bullet")
            paragraph.add_run(f"{owner}: ").bold = True
            paragraph.add_run("; ".join(refs) + ".")
    return True


def _corrections(document: Any, brief: Mapping[str, Any]) -> None:
    _heading(document, "Corrections ledger", level=3)
    rows = brief.get("corrections") or []
    if not rows:
        document.add_paragraph(NO_CORRECTIONS)
        return
    _note(document, CORRECTIONS_NOTE)
    widths = (1.6, 3.4, 3.4, 3.4, 2.7, 2.6)
    table = _table(document, CORRECTIONS_HEADERS, widths)
    for row in rows:
        _row(table, correction_cells(row), widths)


def _quality(document: Any, brief: Mapping[str, Any], title: str) -> None:
    v2 = brief["v2"]
    _heading(document, title)
    _heading(document, "Completeness gate", level=3)
    widths = (7.0, 1.8, 8.3)
    table = _table(document, ("Question", "Answer", "Note"), widths)
    for row in v2["gate"]:
        _row(table, [row["question"] + (" (critical)" if row.get("critical") else ""), row["answer"], row["note"]], widths)
    blocking = (v2.get("completeness") or {}).get("blocking") or []
    if blocking:
        document.add_paragraph(f"Not ready for release: {len(blocking)} critical completeness questions were answered No.")
    if v2["conflicts"]:
        _heading(document, "Conflicts between sources", level=3)
        widths = (4.0, 7.0, 6.1)
        table = _table(document, ("Item", "What the sources say", "Treatment"), widths)
        for row in v2["conflicts"]:
            _row(table, [row["item"], row["said"], row["treatment"]], widths)
    _corrections(document, brief)


def _source_register(document: Any, brief: Mapping[str, Any], title: str) -> None:
    refs = _REGISTER["refs"] if _REGISTER else {}
    if not refs:
        return
    _heading(document, title)
    _note(document, SOURCE_LEGEND)
    tiers = _REGISTER["tiers"]
    for key, number in _REGISTER["numbers"].items():
        ref = refs[key]
        paragraph = document.add_paragraph()
        run = paragraph.add_run(f"[{number}] {ref.get('outlet') or 'source'}, {ref.get('title') or ''}, {ref.get('when') or ref.get('date') or 'date not stated'} ({tiers.get(str(ref.get('signal_id')), 'T4')}) ")
        run.font.size = Pt(8.5)
        if ref.get("url"):
            _hyperlink(paragraph, str(ref["url"]), str(ref["url"]), size=8)


# The named watch table replaces these prose sections in a format v2 brief.
V2_REPLACES = frozenset({"SUPPLIERS", "CUSTOMERS", "COMPETITORS"})
# A format v2 report follows the URS methodology's order (section 15), as Wilson's report does: these sections, then the
# reader's own sections, then V2_CLOSING, the quality check and the source register. Audit material goes to an appendix.
V2_SECTIONS = (
    ("Exposure snapshot", _exposure),
    ("What changed since the last report", _changes),
    ("Look-ahead: the next 30 days", _look_ahead),
    ("Escalating and elevated items", _escalating),
    ("Prioritisation", _priorities),
    ("Coverage matrix", _coverage),
    ("Jurisdiction coverage", _jurisdictions),
)
V2_CLOSING = (
    ("Named supplier, customer and competitor watch", _watch),
    (POSTURE_TITLE, _posture),
)
