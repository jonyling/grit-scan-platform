"""Render a brief model as a Word document in the style of the reference brief (docs/reference)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from grit.core.source_bands import LEGEND as SOURCE_LEGEND
from grit.report.brief import text_only
from grit.report.notices import DISCLAIMER
from grit.report.table_style import NAVY, header_row, shade_cell
GREY = RGBColor(0x55, 0x55, 0x55)
LIGHT = RGBColor(0x77, 0x77, 0x77)
BOX_FILL = {"WATCHPOINT": "FFF2CC", "ELEVATED": "FFF2CC", "STABLE": "E2EFDA", "NORMAL": "E2EFDA", "RED": "F8CBAD"}
# Format v2 (spec 44): while a brief renders, every cited source gets a number: [S1], [S2], and so on. Its link goes to the
# source register at the end. None when a renderer is not inside render_docx.
_REGISTER: dict[str, Any] | None = None
# Paragraphs that list a source by its headline. The first-use filter reads them but never cuts them, since a headline
# such as "Fixes for Nov. 11" is not a pair of sentences.
_WHOLE: set[Any] = set()
# Defaults. A pack's scan plan can rename any section through sectionTitles.
SECTION_TITLES = {
    "CONDITIONS": "Findings mapped to the standing items",
    "SCENARIOS": "Scenario outlook: best, medium and worst case for STE",
    "PRIORITY_FINDINGS": "Priority findings for this reader",
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


def section_plan(brief: Mapping[str, Any], renders: Any) -> list[tuple[str, str, Any]]:
    """The Group radar's sections that render, in reading order, as (key, title, renderer).
    renders(key, title, renderer) says whether a section has content."""
    meta, v2 = brief["meta"], brief.get("v2")
    plan: list[tuple[str, str, Any]] = []
    if v2:
        plan += [(title, title, renderer) for title, renderer in V2_SECTIONS]
    for key in meta["sections"]:
        if key == "BOTTOM_LINE":
            continue
        renderer = _v2_watchpoints if v2 and key == "WATCHPOINTS" else _SECTIONS.get(key)
        if renderer is None:
            continue
        plan.append((key, v2_title(key, meta) if v2 else section_title(meta, key), renderer))
    if v2:
        plan += [(title, title, renderer) for title, renderer in V2_CLOSING]
    return [row for row in plan if renders(*row)]


def posture_pointer(plan: Sequence[tuple[str, str, Any]], first: int) -> str:
    number = next((first + index for index, (key, *_rest) in enumerate(plan) if key == POSTURE_TITLE), None)
    return f"The principal ask and when to raise to RED are in section {number}, {POSTURE_TITLE}." if number else ""


def posture_lines(brief: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The action posture's principal ask and RED rule, as (label, text)."""
    lines = [("Principal ask. ", brief["bottom_line"].get("escalation") or NO_ASK)]
    basis = brief["bottom_line"].get("ask_basis")
    if isinstance(basis, Mapping):
        lines.append(("Why this ask. ", f"It rests on \"{basis.get('title')}\", which {'; and '.join(basis.get('links') or [])}."))
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

    for row in (brief.get("v2") or {}).get("escalating") or []:
        response = row.get("response") or {}
        if response.get("action"):
            band = row.get("reader_band") or "Escalating"
            add(response.get("owner"), f"{band} item, {short_text(row['item'], 80)}")
    label = brief["meta"].get("standing_label") or "condition"
    for row in brief.get("conditions") or []:
        for option in row.get("options") or []:
            add(option.get("owner"), f"{label} {row['number']}, {row['name']}")
    return list(owners.items())


def correction_cells(row: Mapping[str, Any]) -> list[str]:
    return [row["id"], row["earlier"], row["why"], row["corrected"], row["source"], row.get("update_old") or "None stated"]


def _shade(cell: Any, fill: str) -> None:
    shade_cell(cell, fill)


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
    return ", ".join(dict.fromkeys(_number(ref) for ref in refs)) if _REGISTER is not None else ""


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
    return header_row(document, headers, widths)


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


def _document() -> Any:
    document = Document()
    for section in document.sections:
        section.left_margin = section.right_margin = Cm(2.0)
        section.top_margin = section.bottom_margin = Cm(1.8)
    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    return document


def _masthead(document: Any, meta: Mapping[str, Any]) -> None:
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


def render_docx(brief: Mapping[str, Any], path: Path) -> Path:
    from grit.report.script import latin_copy

    brief = latin_copy(brief)
    if brief["meta"].get("layout") == "BA":
        return _render_business_area(brief, path)
    meta = brief["meta"]
    document = _document()
    _masthead(document, meta)

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

    plan = section_plan(brief, lambda key, title, renderer: renderer(document, brief, title, dry=True))
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
    _REGISTER = None
    _apply_first_use(document)
    _WHOLE.clear()
    _footers(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    return path


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
    """The Group Strategic Radar's conditions table (spec 37)."""
    if dry:
        return bool(brief.get("conditions"))
    _orient(document, landscape=True)
    _heading(document, title)
    _radar_table(document, brief, brief["conditions"])
    _orient(document, landscape=False)
    return True


def _findings(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return bool(brief.get("findings"))
    _heading(document, title)
    document.add_paragraph("Ordered by this reader's news priority (P1 management request, P2 direct exposure, P3 competitor or sector, P4 customer), then reader band (Escalating / Elevated / Watch).")
    for item in brief["findings"]:
        paragraph = document.add_paragraph(style="List Bullet")
        band = item.get("band") or "Watch"
        label = f"{item['priority']} · {band}" if item.get("priority") else band
        paragraph.add_run(f"[{label}] ").bold = True
        _WHOLE.add(paragraph._p)
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
        if item["harm"]:
            lines.append(item["harm"])
        if item.get("red_status"):
            lines.append(RED_LINES[item["red_status"]])
        run = detail.add_run(" ".join(lines))
        run.font.size = Pt(9)
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
    from grit.report.format_v2 import clean_title

    def line(case: Mapping[str, Any], item: Mapping[str, Any]) -> str:
        quote = f" Evidence: \u201c{item['quotes'][0]}\u201d" if item.get("quotes") else ""
        return f"{case['title'].split(' case')[0]}: {item['text']}{quote} {_ref_text(item)}".strip()

    seen = [line(case, item) for case in reading["cases"] for item in case["signposts_seen"]]
    against = [line(case, item) for case in reading["cases"] for item in case["counter_indicators_seen"]]
    from grit.report.notices import HYPOTHESIS_LABEL, suggestion_line

    triggered = [line for case in reading["cases"] for response in case["responses_triggered"] if (line := suggestion_line(f"{response['action']} Owner: {response['owner']}."))]
    gains = [f"{case['title'].split(' case')[0]}: {HYPOTHESIS_LABEL} {case['where_ste_could_gain']}" for case in reading["cases"] if case.get("where_ste_could_gain")]
    names = [f"{case['title'].split(' for')[0]}: {case['name']}" for case in reading["cases"]]
    if not seen and reading.get("related"):
        # Context, not evidence: these reports touched the shift but matched none of its reviewed signposts.
        seen = ["None matched. Related this week, not a signpost: " + "; ".join(f"{clean_title(ref['title'])} ({ref['outlet']}, {ref['date'] or 'date not stated'})" for ref in reading["related"]) + "."]
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
    # A format v2 brief lists the indicators once, in the look-ahead.
    if outlook.get("indicators") and not brief.get("v2"):
        document.add_paragraph("Leading indicators. Green: no action. Amber: BAs review exposure and plans. Red: BAs act on the planned responses. The bounds are proposed by GRIT-Scan until Wilson confirms them.")
        for item in outlook["indicators"]:
            document.add_paragraph(_indicator_text(item), style="List Bullet")
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
        f"({method['hidden_by_routing']} outside the aperture). {method['profile']} The profile reordered {method['moved_by_profile']} signal(s) within their bands and hid none.",
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
        if child in _WHOLE or paragraph._p.find(".//" + qn("w:hyperlink")) is not None:
            state.observe(paragraph.text)
            continue
        updated = state.filter(paragraph.text)
        if updated != paragraph.text and paragraph.runs:
            paragraph.runs[0].text = updated
            for run in paragraph.runs[1:]:
                run.text = ""


def _footers(document: Any) -> None:
    """One footer carries the disclaimer. Later sections link to it, so the document holds it once (spec 36)."""
    for number, section in enumerate(document.sections):
        footer = section.footer
        footer.is_linked_to_previous = number > 0
        if number:
            continue
        paragraph = footer.paragraphs[0]
        paragraph.text = DISCLAIMER
        for run in paragraph.runs:
            run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT


_SECTIONS = {
    "CONDITIONS": _conditions,
    "SCENARIOS": _scenarios,
    "PRIORITY_FINDINGS": _findings,
    "WATCHPOINTS": _watchpoints,
}


# Format v2 sections (spec 44) -----------------------------------------------------------------------------------------

V2_NOTE = (
    "Bands come from hard overrides and scores; order within a band is model-proposed with a URS reach reason "
    "(person, entity, contract, supplier, or cash), not a precise global 1–N rank. "
    "Rows are AI-drafted suggestions for consideration, checked by rule against their sources. A named person reviews them before anyone acts."
)


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
    if snapshot.get("notSupplied"):
        from grit.report.markdown import not_supplied

        document.add_paragraph(not_supplied(snapshot))
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
    widths = (1.4, 2.8, 4.4, 3.8, 3.2, 1.4)
    table = _table(document, ("Band", "Item and risk", "Summary", f"Implications for {short}", "Suggested response (owner)", "Sources"), widths)
    for row in rows:
        cells = table.add_row().cells
        for cell, width in zip(cells, widths):
            cell.width = Cm(width)
        _text_cell(cells[0], str(row.get("reader_band") or "Escalating"))
        item_lines = [f"{row['band']} {row['item']}", f"[{row['risk']}]" if row["risk"] else "No register risk matched."]
        if row.get("urs_reach_reason"):
            channel = f" ({row['reach_channel']})" if row.get("reach_channel") else ""
            item_lines.append(f"URS reach{channel}: {row['urs_reach_reason']}")
        _cell_lines(cells[1], item_lines, bold_first=True)
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
            document.add_paragraph(_indicator_text(item), style="List Bullet")
    return True


def _indicator_text(item: Mapping[str, Any]) -> str:
    """An indicator's reading and band, the risk themes it bears on by name, and why its band may differ from their trend."""
    from grit.pipeline.indicators import indicator_line, theme_note

    themes = [theme for theme in item.get("themes") or [] if isinstance(theme, Mapping)]
    text = f"{item['name']}: {indicator_line(item)}"
    if themes:
        text += " Bears on risk " + ("themes " if len(themes) > 1 else "theme ") + "; ".join(f"{theme['number']}, {theme['name']} ({theme['trend']})" for theme in themes) + "."
    note = theme_note(item)
    return f"{text} {note}" if note else text


def _priorities(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    rows = brief["v2"]["priorities"]
    if dry:
        return bool(rows)
    _heading(document, title)
    widths = (1.4, 6.3, 2.6, 3.4, 3.4)
    table = _table(document, ("Band", "Item", "Trend", "Risk linkage", "Owner or forum"), widths)
    for row in rows:
        item = row["item"] + (f" [{_marks(row['sources'])}]" if row.get("sources") and _REGISTER is not None else "")
        if row.get("urs_reach_reason"):
            channel = f" ({row['reach_channel']})" if row.get("reach_channel") else ""
            item = f"{item}\nURS reach{channel}: {row['urs_reach_reason']}"
        _row(table, [row.get("reader_band") or "", item, row["trend"], row["risk"] or "No register risk matched", row["owner"]], widths)
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
    from grit.report.format_v2 import clean_title

    refs = _REGISTER["refs"] if _REGISTER else {}
    if not refs:
        return
    _heading(document, title)
    _note(document, SOURCE_LEGEND)
    tiers = _REGISTER["tiers"]
    for key, number in _REGISTER["numbers"].items():
        ref = refs[key]
        paragraph = document.add_paragraph()
        run = paragraph.add_run(f"[{number}] {ref.get('outlet') or 'source'}, {clean_title(ref.get('title')) or ref.get('title') or ''}, {ref.get('when') or ref.get('date') or 'date not stated'} ({tiers.get(str(ref.get('signal_id')), 'T4')}) ")
        run.font.size = Pt(8.5)
        if ref.get("url"):
            _hyperlink(paragraph, str(ref["url"]), str(ref["url"]), size=8)


# The Group radar follows the URS methodology's order (section 15): these sections, then the reader's own sections, then
# V2_CLOSING, the quality check and the source register.
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


# Business-area layout (Wilson, 19 November 2026) ---------------------------------------------------------------------

SECTION_HEADERS = ("#", "Item and Risk", "Summary", "Potential Impact/Opportunities", "Proposed Response/Management Consideration (Owner)", "Sources")
SECTION_WIDTHS = (0.7, 2.3, 4.5, 5.1, 3.6, 1.4)
NO_ITEM = "No item this week."
WATCH_HEADERS = ("Name", "Type", "This week", "Proposed Response from Lessons Learnt")


def ba_headings(brief: Mapping[str, Any]) -> list[tuple[int, str, str | None]]:
    """The business-area report's body, as (heading level, title, section key or None for a heading alone)."""
    short = brief["v2"]["short"]
    return [
        (1, "1. Management Focus Topics", "MANAGEMENT_FOCUS"),
        (1, f"2. {short} Risks", None),
        (2, "a. Key, T2 & Emerging", "REGISTER_RISK"),
        (2, "b. Other Risk Themes", "OTHER_THEME"),
        (1, "3. Trends/Developments", "TREND"),
    ]


def response_text(row: Mapping[str, Any]) -> list[str]:
    response = row.get("response") or {}
    return [str(response.get("action") or ""), f"Owner: {response.get('owner') or 'to assign'}."]


def _section_table(document: Any, rows: Sequence[Mapping[str, Any]]) -> None:
    table = _table(document, SECTION_HEADERS, SECTION_WIDTHS)
    for number, row in enumerate(rows, start=1):
        cells = table.add_row().cells
        for cell, width in zip(cells, SECTION_WIDTHS):
            cell.width = Cm(width)
        _text_cell(cells[0], str(number))
        _cell_lines(cells[1], [f"{row['band']} {row['item']}", f"[{row['risk']}]"], bold_first=True)
        _runs(cells[2].paragraphs[0], row["summary"], size=8.5)
        _runs(cells[3].paragraphs[0], row["impact"], size=8.5, fallback="Not established from the cited sources.")
        if row.get("opportunity"):
            paragraph = cells[3].add_paragraph()
            label = paragraph.add_run("Opportunity: ")
            label.bold, label.font.size = True, Pt(8.5)
            _runs(paragraph, row["opportunity"], size=8.5)
        _cell_lines(cells[4], response_text(row))
        _marks_cell(cells[5], row.get("sources") or [])
    if not rows:
        _note(document, NO_ITEM)


def _ba_look_ahead(document: Any, brief: Mapping[str, Any], title: str) -> None:
    _heading(document, title)
    rows = brief["v2"]["look_ahead"]
    if not rows:
        document.add_paragraph("No source linked to the business area states a dated event in the next 30 days.")
        return
    widths = (2.2, 9.4, 3.8, 1.7)
    table = _table(document, ("Date", "Event, as the source states it", f"Relevance to {brief['v2']['short']}", "Sources"), widths)
    for row in rows:
        when = date.fromisoformat(row["date"])
        _row(table, [f"{when.strftime('%a')} {when.day} {when.strftime('%b')}", row["event"], row["relevance"] or "Linked by the scan", _marks(row["sources"])], widths)
    _note(document, "Dates come only from the week's sources, as stated. Nothing here is a forecast.")


def scenario_headers(short: str) -> tuple[str, ...]:
    return (*SCENARIO_HEADERS[:-1], f"Where {short} could gain")


def _ba_scenarios(document: Any, brief: Mapping[str, Any], title: str) -> None:
    outlook = brief.get("scenarios") or {}
    _heading(document, title)
    if not outlook.get("shifts"):
        document.add_paragraph("No reviewed scenario register for this business area yet.")
        return
    widths = (3.2, 2.2, 4.2, 2.6, 2.6, 2.8)
    table = _table(document, scenario_headers(brief["v2"]["short"]), widths)
    for reading in outlook["shifts"]:
        cells = table.add_row().cells
        for cell, width, lines in zip(cells, widths, scenario_cells(reading)):
            cell.width = Cm(width)
            _cell_lines(cell, lines, bold_first=cell is cells[0])
    _note(document, f"{SCENARIO_NOTE} Scenarios v{outlook['version']}, reviewed by {outlook['reviewed_by']} on {outlook['reviewed_on']}.")


def _ba_watch(document: Any, brief: Mapping[str, Any], title: str) -> None:
    _heading(document, title)
    rows = brief["v2"]["watch"]
    if not rows:
        document.add_paragraph("No named supplier, customer or competitor had item-specific news this week.")
        return
    widths = (3.6, 1.8, 7.0, 5.2)
    table = _table(document, WATCH_HEADERS, widths)
    for row in rows:
        _row(table, [row["name"], row["type"], row["text"], row.get("lesson") or "Not drafted."], widths)


def _render_business_area(brief: Mapping[str, Any], path: Path) -> Path:
    global _REGISTER
    document = _document()
    _masthead(document, brief["meta"])
    _REGISTER = {"numbers": {}, "refs": {}, "tiers": brief["v2"].get("tiers") or {}}
    box = document.add_table(rows=1, cols=1)
    box.style = "Table Grid"
    cell = box.rows[0].cells[0]
    _shade(cell, BOX_FILL.get(brief["bottom_line"].get("label"), "FFF2CC"))
    run = cell.paragraphs[0].add_run("SUMMARY")
    run.bold, run.font.color.rgb = True, NAVY
    cell.add_paragraph().add_run(brief["bottom_line"]["text"] or "")
    sections = brief["v2"]["sections"]
    for level, title, key in ba_headings(brief):
        _heading(document, title, level=level)
        if key:
            _section_table(document, sections.get(key) or [])
    _note(document, V2_NOTE)
    _heading(document, "Appendix A")
    _ba_look_ahead(document, brief, "1. Look-ahead: the next 30 days")
    _ba_scenarios(document, brief, "2. Scenario outlook: best, medium and worst case")
    if not _jurisdictions(document, brief, "3. Jurisdiction coverage", dry=True):
        _heading(document, "3. Jurisdiction coverage")
        document.add_paragraph("The register holds no entity with a country.")
    else:
        _jurisdictions(document, brief, "3. Jurisdiction coverage", dry=False)
    _ba_watch(document, brief, "4. Named supplier, customer and competitor watch")
    _source_register(document, brief, "Appendix B: Source Register")
    _REGISTER = None
    _apply_first_use(document)
    _WHOLE.clear()
    _footers(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    return path
