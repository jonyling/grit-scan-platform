"""Render a brief model as a Word document in the style of the reference brief (docs/reference)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

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
    run = status.add_run(f"Status: draft for review, not approved. Reader profile: {meta['reader']}. Run {meta['run_id']}.")
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


def _full_entry(document: Any, row: Mapping[str, Any], label: str) -> None:
    """A key risk, or an escalating theme, in full: trend, assessment, implication, and the options the profile asks for."""
    _heading(document, f"{row['number']}. {row['name']}", level=3)
    line = document.add_paragraph()
    run = line.add_run(f"{row.get('group') or 'Risk theme'}. Trend: {row['trend']}. Confidence: {row['confidence']}.")
    run.italic, run.font.size, run.font.color.rgb = True, Pt(9), GREY
    if row.get("covered_by"):
        line.add_run(f" Draws on the {row['covered_by']} entity pack.").font.size = Pt(9)
    _runs(document.add_paragraph(), row["basis"])
    if row.get("implication"):
        paragraph = document.add_paragraph()
        paragraph.add_run(f"What it could mean for {label}: ").bold = True
        _runs(paragraph, row["implication"])
    if row.get("options"):
        paragraph = document.add_paragraph()
        paragraph.add_run("Options for management consideration").bold = True
        for option in row["options"]:
            item = document.add_paragraph(style="List Bullet")
            _runs(item, option["text"])
            meta = item.add_run(f" {option_line(option)}")
            meta.font.size, meta.font.color.rgb = Pt(9), GREY
        note = document.add_paragraph()
        run = note.add_run(OPTIONS_NOTE)
        run.italic, run.font.size, run.font.color.rgb = True, Pt(8), LIGHT


def _conditions(document: Any, brief: Mapping[str, Any], title: str, *, dry: bool) -> bool:
    if dry:
        return bool(brief.get("conditions"))
    _heading(document, title)
    prior = brief["meta"].get("prior_run_date")
    widths = (0.8, 3.6, 2.2, 2.0, 8.6)
    themes = brief["meta"].get("standing_label") == "risk theme"
    full = [row for row in brief["conditions"] if row.get("full")] if themes else []
    rest = [row for row in brief["conditions"] if row not in full]
    if full:
        _heading(document, "Key risks" if all(row.get("tier") == "KEY" for row in full) else "Key risks and escalating themes", level=2)
        for row in full:
            _full_entry(document, row, brief["meta"].get("pack_label") or "the business area")
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
        f"({method['hidden_by_routing']} outside the aperture, {method['hidden_by_memory']} hidden by the reader's memory of {method['memory_size']} decisions).",
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
    "PRIORITY_FINDINGS": _findings,
    "OTHER_SIGNALS": _others,
    "QA_LOG": _qa,
    "WATCHPOINTS": _watchpoints,
}
