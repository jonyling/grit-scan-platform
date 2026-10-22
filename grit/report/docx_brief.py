"""Render a brief model as a Word document in the style of the reference brief (docs/reference)."""

from __future__ import annotations

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

from grit.report.brief import text_only

NAVY = RGBColor(0x1F, 0x38, 0x64)
GREY = RGBColor(0x55, 0x55, 0x55)
LIGHT = RGBColor(0x77, 0x77, 0x77)
BOX_FILL = {"WATCHPOINT": "FFF2CC", "STABLE": "E2EFDA", "RED": "F8CBAD"}
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


def section_title(meta: Mapping[str, Any], key: str) -> str:
    return (meta.get("section_titles") or {}).get(key) or SECTION_TITLES[key]
NOTHING = "No new item found this week in the checked sources."


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
            if ref.get("url"):
                _hyperlink(paragraph, label, ref["url"], size=size)
            else:
                paragraph.add_run(label)
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
    if bottom.get("escalation"):
        body.add_run(" " + bottom["escalation"])
    note = cell.add_paragraph()
    run = note.add_run(f"Drafted by the {bottom['drafted_by']}.")
    run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT

    number = 0
    for key in meta["sections"]:
        if key == "BOTTOM_LINE":
            continue
        renderer = _SECTIONS.get(key)
        if renderer is None:
            continue
        title = f"{number}. {section_title(meta, key)}"
        if not renderer(document, brief, title, dry=True):
            continue
        renderer(document, brief, title, dry=False)
        number += 1
    _method(document, brief)
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
            if ref.get("url"):
                _hyperlink(paragraph, str(ref["outlet"]), str(ref["url"]), size=8.5)
            else:
                paragraph.add_run(str(ref["outlet"])).font.size = Pt(8.5)
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
            if item["url"]:
                _hyperlink(line, item["title"], item["url"], size=10)
            else:
                line.add_run(item["title"]).font.size = Pt(10)
            line.add_run(f" ({item['outlet']}, {item['date'] or 'date not stated'})").font.size = Pt(10)
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
        if item["url"]:
            _hyperlink(paragraph, item["title"], item["url"])
        else:
            paragraph.add_run(item["title"])
        paragraph.add_run(f" ({item['outlet']}, {item['date'] or 'date not stated'})")
        detail = document.add_paragraph()
        detail.paragraph_format.left_indent = Cm(1.0)
        lines = [line for line in item["why"] if line]
        if item.get("card") and item["card"].get("what_happened"):
            lines.insert(0, str(item["card"]["what_happened"]))
        if item.get("card") and item["card"].get("hypothesis"):
            lines.append(f"Hypothesis, not a forecast: {item['card']['hypothesis']} Link confidence: {item['card']['confidence']}.")
        if item["risks"]:
            lines.append("Risk register: " + "; ".join(item["risks"]) + ".")
        lines.append(item["harm"])
        if item["red"]:
            lines.append("RED alert draft raised for a named sender.")
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
        if item["url"]:
            _hyperlink(paragraph, item["title"], item["url"], size=10)
        paragraph.add_run(f" ({item['outlet']}, {item['date'] or 'date not stated'})").font.size = Pt(10)
    return True


def _qa(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return True
    _heading(document, title)
    if not brief["qa"]:
        document.add_paragraph("The Critic raised no supported issue this week, and every draft passed its checks.")
        return True
    document.add_paragraph("What changed or was withheld as a direct result of this week's checks and the Critic's review.")
    widths = (4.0, 5.0, 8.2)
    table = _table(document, ("Item", "Prior version stated", "Correction after this QA pass"), widths)
    for row in brief["qa"]:
        _row(table, (row["item"], row["prior"] or "", row["correction"]), widths)
    return True


SCENARIO_HEADERS = ("Strategic shift", "Where the signposts lean", "Signposts seen, with sources", "Counter-indicators seen", "Responses now triggered")
SCENARIO_NOTE = ("Each shift has a reviewed best, medium and worst case for STE. A signpost counts as seen when a kept signal in the period touches the "
                 "shift and names its keywords. These are counts of signposts, not odds.")


def _ref_text(item: Mapping[str, Any]) -> str:
    refs = "; ".join(f"{ref['outlet']}, {ref['date'] or 'date not stated'}" for ref in item.get("sources") or [])
    more = f"; and {item['more']} more" if item.get("more") else ""
    return f"({refs}{more})" if refs else ""


def scenario_cells(reading: Mapping[str, Any]) -> list[list[str]]:
    """One row of the scenario outlook, as lines per cell: shared by the Word, Markdown, Excel and deck renderers."""
    from grit.pipeline.scenarios import lean_text

    seen = [f"{case['title'].split(' case')[0]}: {item['text']} {_ref_text(item)}".strip() for case in reading["cases"] for item in case["signposts_seen"]]
    against = [f"{case['title'].split(' case')[0]}: {item['text']} {_ref_text(item)}".strip() for case in reading["cases"] for item in case["counter_indicators_seen"]]
    triggered = [f"{response['action']} Owner: {response['owner']}." for case in reading["cases"] for response in case["responses_triggered"]]
    names = [f"{case['title'].split(' for')[0]}: {case['name']}" for case in reading["cases"]]
    return [[reading["name"], *names], [lean_text(reading)], seen or ["None this period."], against or ["None this period."], triggered or ["None."]]


def _scenarios(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    outlook = brief.get("scenarios")
    if dry:
        return bool(outlook and outlook.get("shifts"))
    _orient(document, landscape=True)
    _heading(document, title)
    widths = (5.2, 3.6, 7.6, 4.4, 4.4)
    table = _table(document, SCENARIO_HEADERS, widths)
    for reading in outlook["shifts"]:
        cells = table.add_row().cells
        for cell, width, lines in zip(cells, widths, scenario_cells(reading)):
            cell.width = Cm(width)
            _cell_lines(cell, lines, bold_first=cell is cells[0])
    note = document.add_paragraph()
    run = note.add_run(f"{SCENARIO_NOTE} Scenarios v{outlook['version']}, reviewed by {outlook['reviewed_by']} on {outlook['reviewed_on']}.")
    run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT
    _orient(document, landscape=False)
    return True


def _watchpoints(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return True
    _heading(document, title)
    document.add_paragraph(brief["bottom_line"]["text"] or "")
    open_rows = [row for row in brief["watchpoints"] if row["status"] != "CLOSED"]
    if open_rows:
        document.add_paragraph("Concrete watchpoints carried into next cycle:")
    for row in open_rows:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.add_run(f"{row['title']}").bold = True
        paragraph.add_run(f" ({row['status'].lower()}): {row['detail']} ")
        _runs(paragraph, row["update"], fallback="")
    for alert in brief.get("red") or []:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.add_run("RED draft: ").bold = True
        paragraph.add_run(f"{alert['title']} — rule {(alert.get('rule') or {}).get('rule_id')}. A named person must send or dismiss it.")
    document.add_paragraph(f"Escalation assessment: {brief['bottom_line']['escalation']}")
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
