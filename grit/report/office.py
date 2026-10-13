"""Render a brief model as an executive-summary deck in the reference format (docs/reference), and as an Excel signal list."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from grit.report.docx_brief import implication_text, key_risk_headers, opportunity_text, priority_trend, radar_cells, radar_headers, response_lines

# The reference deck's palette (docs/reference/GRIT-Scan_Executive_Summary.pptx).
GOLD, EYEBROW, INK, MUTED, BODY = RGBColor(0xC0, 0x8A, 0x1E), RGBColor(0xA9, 0x74, 0x07), RGBColor(0x12, 0x21, 0x3F), RGBColor(0x5A, 0x68, 0x84), RGBColor(0x2C, 0x3A, 0x55)
CARD, AMBER_FILL, RULE, DOT = RGBColor(0xF7, 0xF9, 0xFC), RGBColor(0xFD, 0xF7, 0xEA), RGBColor(0xD5, 0xDC, 0xE6), RGBColor(0xDC, 0xE3, 0xEE)
TRENDS = {
    "Escalating": ("\u25b2 ESCALATING", RGBColor(0xB9, 0x3A, 0x22), RGBColor(0xFB, 0xED, 0xE9), RGBColor(0xC4, 0x46, 0x2C)),
    "Easing": ("\u25bc EASING", RGBColor(0x2E, 0x6B, 0x3A), RGBColor(0xE8, 0xF3, 0xEA), RGBColor(0x4F, 0x9A, 0x5A)),
}
STEADY = ("= NO MATERIAL CHANGE", MUTED, RGBColor(0xEE, 0xF1, 0xF6), RGBColor(0x9A, 0xA6, 0xBC))
STATUS = {
    "RED": ("\u25cf  RED", RGBColor(0xB9, 0x3A, 0x22), RGBColor(0xFB, 0xED, 0xE9)),
    "WATCHPOINT": ("\u25cf  WATCHPOINT", RGBColor(0xA0, 0x5F, 0x00), AMBER_FILL),
    "STABLE": ("\u25cf  STABLE", MUTED, RGBColor(0xEE, 0xF1, 0xF6)),
}
ASK = ("\U0001f4ac  Ask GRIT-Scan chat about this risk", "mitigation  \u00b7  watchpoints  \u00b7  response plans  \u00b7  what to escalate")


def fit(text: str | None, limit: int) -> str:
    """Shorten text to whole sentences within limit, or cut at a word with an ellipsis. The Word brief keeps the full text."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    kept = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(kept) + len(sentence) + 1 > limit:
            break
        kept = f"{kept} {sentence}".strip()
    return kept or text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "\u2026"


def _shape(slide: Any, x: float, y: float, w: float, h: float, fill: RGBColor | None, kind: Any = MSO_SHAPE.RECTANGLE) -> Any:
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _words(slide: Any, x: float, y: float, w: float, h: float, lines: Sequence[tuple[str, float, bool, RGBColor]], *, link: Any = None,
           middle: bool = False, urls: Sequence[str | None] = ()) -> Any:
    """A text box. lines are (text, size, bold, colour). link makes the whole box jump to a slide."""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0.05)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    if middle:
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    for number, (text, size, bold, colour) in enumerate(lines):
        paragraph = frame.paragraphs[0] if number == 0 else frame.add_paragraph()
        run = paragraph.add_run()
        run.text = text
        run.font.size, run.font.bold, run.font.color.rgb = Pt(size), bold, colour
        if number < len(urls) and urls[number]:
            run.hyperlink.address = urls[number]
    if link is not None:
        box.click_action.target_slide = link
    return box


def _rule(slide: Any, y: float) -> None:
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(0.5), Inches(y), Inches(12.83), Inches(y))
    line.line.color.rgb = RULE
    line.line.width = Pt(1)


def _link(slide: Any, x: float, y: float, text: str, target: Any, *, colour: RGBColor = BODY, dot: RGBColor = DOT, w: float = 7.0) -> None:
    marker = _shape(slide, x, y + 0.22, 0.18, 0.18, dot)
    if target is not None:
        marker.click_action.target_slide = target
    _words(slide, x + 0.28, y, w, 0.62, [(text, 13.5, True, colour)], link=target, middle=True)


def _badge(slide: Any, x: float, y: float, w: float, h: float, text: str, colour: RGBColor, fill: RGBColor, size: float = 12.5) -> None:
    _shape(slide, x, y, w, h, fill, MSO_SHAPE.ROUNDED_RECTANGLE)
    box = _words(slide, x, y, w, h, [(text, size, True, colour)], middle=True)
    box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER


def _blank(deck: Any, layout: str) -> Any:
    """A slide on a template layout, with the layout's placeholders removed: every text box is drawn by the renderer."""
    slide = deck.slides.add_slide(next(item for item in deck.slide_layouts if item.name == layout))
    for placeholder in list(slide.placeholders):
        placeholder._element.getparent().remove(placeholder._element)
    return slide


def deck_items(brief: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The items the deck details: key risks and escalating themes for a business area, moved conditions for an entity
    pack. Escalating first, then key risks, then register order."""
    rows = brief.get("conditions") or []
    themes = brief["meta"].get("standing_label") == "risk theme"
    if brief["meta"].get("level") == "GROUP":
        chosen = [row for row in rows if row.get("status") == "VALIDATED"]  # every condition with evidence this period (spec 37)
    else:
        chosen = [row for row in rows if row.get("full")] if themes else [row for row in rows if row["trend"] in TRENDS]
    return sorted(chosen, key=lambda row: (row["trend"] != "Escalating", row.get("tier") != "KEY", row["number"]))


def render_pptx(brief: Mapping[str, Any], path: Path) -> Path:
    """The executive summary in the reference deck's format: a cover, a one-page summary, and one appendix slide per item."""
    from grit.report.deck_template import CONTENT_LAYOUT, COVER_LAYOUT, TEMPLATE

    meta, bottom = brief["meta"], brief["bottom_line"]
    short, label = meta.get("pack_short") or meta["pack_label"], meta["pack_label"]
    deck = Presentation(str(TEMPLATE))
    items = deck_items(brief)
    cover, summary = _blank(deck, COVER_LAYOUT), _blank(deck, CONTENT_LAYOUT)
    appendix = [_blank(deck, CONTENT_LAYOUT) for _ in items]

    # Cover.
    _shape(cover, 0.5, 1.1, 0.1, 1.5, GOLD)
    _words(cover, 0.75, 1.1, 9.0, 0.3, [(f"GROUP RISK & ASSURANCE   \u00b7   {short.upper()}", 13, True, EYEBROW)])
    _words(cover, 0.75, 1.4, 10.0, 1.0, [("GRIT-Scan", 54, True, INK)])
    _words(cover, 0.75, 2.34, 10.5, 0.5, [(f"Global Risk Impact Tracker \u2013 Scanning for key risks impacting {short}", 20, False, MUTED)])
    _rule(cover, 2.94)
    _words(cover, 0.5, 3.1, 10.0, 1.3, [(
        f"A weekly horizon scan of the developments that could affect {label} \u2014 what changed this week, what it means for {short}, "
        "and what we are watching next.", 16, False, BODY)])
    for number, (caption, value) in enumerate((("SCAN WINDOW", meta["window"]), ("PREPARED FOR", meta["prepared_for"]), ("REPORT OWNER", "Group Risk & Assurance"))):
        x = 0.5 + number * 4.2
        _shape(cover, x, 4.6, 3.9, 1.2, CARD)
        _shape(cover, x, 4.6, 0.05, 1.2, GOLD)
        _words(cover, x + 0.25, 4.83, 3.4, 0.25, [(caption, 11, True, EYEBROW)])
        _words(cover, x + 0.25, 5.15, 3.4, 0.5, [(fit(value, 60), 15, True, INK)])
    _words(cover, 0.5, 6.1, 12.3, 0.3, [(f"Draft for review. Not approved. Reader: {meta['reader']}. Run {meta['run_id']}.", 10, False, MUTED)])

    # Executive summary.
    _words(summary, 0.5, 0.24, 8.0, 0.3, [("EXECUTIVE SUMMARY  \u00b7  GRIT-SCAN", 13, True, EYEBROW)])
    title = f"{label} \u2014 Weekly Risk Radar"
    _words(summary, 0.5, 0.52, 9.4, 0.62, [(title, 32 if len(title) <= 34 else 26, True, INK)])
    _words(summary, 0.5, 1.13, 9.4, 0.3, [(fit(f"Scan window: {meta['window']}   \u00b7   Prepared for {meta['prepared_for']}   \u00b7   Group Risk & Assurance", 100), 12, False, MUTED)])
    status, colour, fill = STATUS.get(bottom["label"], STATUS["WATCHPOINT"])
    _badge(summary, 10.13, 0.87, 2.98, 0.64, status, colour, fill, size=15)
    escalating = sum(1 for row in items if row["trend"] == "Escalating")
    caption = "RED alert drafted this week" if bottom["label"] == "RED" else f"{escalating} item(s) escalating this week" if escalating else "no material change this week"
    _words(summary, 10.13, 1.52, 2.98, 0.3, [(caption, 9.5, False, MUTED)]).text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    _rule(summary, 1.5)
    _words(summary, 0.5, 1.6, 12.0, 0.32, [(f"\u25b2  NEW & ESCALATING \u2014 WHAT'S CHANGED, AND WHAT IT MEANS FOR {short.upper()}", 16.5, True, TRENDS["Escalating"][1])])
    cards = items[:2] or [None]
    for number, row in enumerate(cards):
        x = 0.5 + number * 6.3
        tag, colour, _fill, bar = TRENDS.get(row["trend"], STEADY) if row else STEADY
        _shape(summary, x, 2.06, 5.95, 2.46, CARD)
        _shape(summary, x, 2.06, 0.05, 2.46, bar)
        if row is None:
            _words(summary, x + 0.3, 2.21, 5.4, 0.26, [(tag, 12, True, colour)])
            _words(summary, x + 0.3, 2.5, 5.35, 0.68, [("No standing item moved this week", 18, True, INK)])
            _words(summary, x + 0.3, 3.21, 5.35, 1.08, [(fit(bottom["text"], 240), 13.5, False, BODY)])
            continue
        _words(summary, x + 0.3, 2.21, 5.4, 0.26, [(f"{tag}  \u00b7  {str(row.get('group') or 'standing item').upper()}", 12, True, colour)])
        _words(summary, x + 0.3, 2.5, 5.35, 0.68, [(fit(row["name"], 80), 18, True, INK)])
        body = fit(row["development"], 130 if row.get("implication") else 250)
        if row.get("implication"):
            body += " " + fit(implication_text(row), 120)
        _words(summary, x + 0.3, 3.21, 5.35, 1.08, [(body, 13, False, BODY)])
        _words(summary, x + 4.6, 4.1, 1.3, 0.34, [("Full detail \u25b8", 12.5, True, BODY)], link=appendix[number])
    rest = items[2:]
    moving = any(row["trend"] == "Escalating" for row in rest)
    header = "\u25b2  ALSO ESCALATING, AND OTHER KEY RISKS \u2014 IN THE APPENDIX" if moving else "=  CONTINUING \u2014 STILL OPEN, NO NEW DEVELOPMENT"
    _words(summary, 0.5, 4.5, 11.0, 0.26, [(header, 13, True, MUTED)])
    _shape(summary, 0.5, 4.8, 12.33, 0.56, CARD)
    listed = "   \u00b7   ".join(f"{TRENDS.get(row['trend'], STEADY)[0].split()[0]} {row['name']} (A{items.index(row) + 1})" for row in rest)
    _words(summary, 0.75, 4.8, 10.82, 0.56, [(fit(listed, 230) or "No other standing item is detailed this week. The Word brief lists every theme.", 12, True, BODY)], middle=True)
    if rest:
        _words(summary, 11.33, 4.86, 1.35, 0.5, [("Full detail \u25b8", 13, True, BODY)], link=appendix[2], middle=True)
    _shape(summary, 0.5, 5.5, 12.33, 0.9, AMBER_FILL)
    _words(summary, 0.78, 5.5, 11.8, 0.9, [(f"BOTTOM LINE   {fit(bottom['text'], 330)}", 12, True, EYEBROW)], middle=True)
    if appendix:
        _link(summary, 0.5, 6.5, f"Full supporting detail \u2014 Appendix A1\u2013A{len(appendix)}" if len(appendix) > 1 else "Full supporting detail \u2014 Appendix A1", appendix[0], dot=GOLD)
    _link(summary, 9.08, 6.5, "Ask GRIT-Scan chat about this full report", None, colour=EYEBROW, dot=GOLD, w=3.55)

    # One appendix slide per item.
    options_on = "RESPONSE_OPTIONS" in (meta.get("sections") or [])
    for number, (slide, row) in enumerate(zip(appendix, items), start=1):
        tag, colour, fill, bar = TRENDS.get(row["trend"], STEADY)
        _words(slide, 0.5, 0.26, 9.0, 0.3, [(f"APPENDIX A{number}  \u00b7  SUPPORTING DETAIL", 13, True, EYEBROW)])
        _words(slide, 0.5, 0.52, 9.85, 0.92, [(fit(row["name"], 90), 24, True, INK)])
        _badge(slide, 10.55, 0.9, 2.28, 0.54, tag, colour, fill)
        _rule(slide, 1.58)
        _words(slide, 0.5, 1.74, 7.75, 0.26, [(f"WHAT IT MEANS FOR {short.upper()}", 14, True, INK)])
        meaning = fit(implication_text(row), 230)
        if row.get("radar"):
            meaning = f"{fit(implication_text(row), 140)} Opportunity: {fit(opportunity_text(row), 85)}"
        _words(slide, 0.5, 2.06, 7.75, 1.0, [(meaning, 14 if not row.get("radar") else 12.5, True, INK)])
        _words(slide, 0.5, 3.1, 7.75, 0.26, [("WHAT HAPPENED", 14, True, INK)])
        happened = 380 if options_on else 650
        _words(slide, 0.5, 3.42, 7.75, 1.35 if options_on else 2.6, [(fit(row["development"], happened), 13, False, BODY)])
        owners = list(dict.fromkeys(str(option.get("owner")) for option in row.get("options") or [] if option.get("owner")))
        if options_on:
            _words(slide, 0.5, 4.85, 7.75, 0.26, [("OPTIONS FOR MANAGEMENT CONSIDERATION", 14, True, INK)])
            lines = [(f"\u2022 {fit(option, 135)} ({fit(detail.split('. ')[1] if '. ' in detail else detail, 45)})", 11, False, INK) for option, detail in response_lines(row)[:4]]
            lines = lines or [("No option proposed: the item is not escalating and cites no material source." if row.get("tier") == "KEY" else "Options are proposed for key risks only.", 11, False, MUTED)]
            _words(slide, 0.5, 5.15, 7.75, 1.2, [*lines, ("Proposed by GRIT-Scan for management consideration. Not reviewed advice.", 9, False, MUTED)])
        _shape(slide, 8.35, 1.74, 4.48, 2.1, CARD)
        _shape(slide, 8.35, 1.74, 0.05, 2.1, bar)
        _words(slide, 8.6, 1.94, 3.98, 0.25, [("RISK LINKAGE", 12, True, colour)])
        linkage = f"{row.get('group') or 'Standing item'} \u2192 {row['name']}." + (f" Priority {row['priority']}." if row.get("priority") else "") + f" Confidence: {row['confidence']}."
        if row.get("radar"):
            linkage = f"Affects: {', '.join(row.get('business_areas') or []) or 'not mapped yet'}. Would change if: {row.get('counter_indicator') or 'not established'}"
        _words(slide, 8.6, 2.24, 3.98, 0.86, [(fit(linkage, 130), 12.5, False, INK)])
        _words(slide, 8.6, 3.1, 3.98, 0.25, [("OWNER", 12, True, colour)])
        _words(slide, 8.6, 3.38, 3.98, 0.42, [(fit("; ".join(owners), 80) if owners else "Not assigned in this brief.", 12, False, BODY)])
        _shape(slide, 8.35, 3.95, 4.48, 0.84, AMBER_FILL)
        _words(slide, 8.35, 3.95, 4.48, 0.84, [(ASK[0], 13, False, EYEBROW), (ASK[1], 10.5, False, RGBColor(0xA0, 0x5F, 0x00))], middle=True)
        _shape(slide, 8.35, 4.9, 4.48, 1.48, CARD)
        _words(slide, 8.5, 4.95, 3.98, 0.25, [("SOURCES", 12, True, MUTED)])
        refs = (row.get("sources") or [])[:3]
        lines = [(fit(f"{ref['outlet']}, \u201c{ref.get('title') or ''},\u201d {ref.get('when') or ref.get('date') or 'date not stated'}", 100), 9.5, False, BODY) for ref in refs]
        more = len(row.get("sources") or []) - len(refs)
        if more > 0:
            lines.append((f"and {more} more in the Word brief.", 9.5, False, MUTED))
        _words(slide, 8.6, 5.2, 3.98, 1.15, lines or [("No source cited this week.", 10, False, MUTED)], urls=[ref.get("url") for ref in refs])
        _link(slide, 0.5, 6.48, "Back to Executive Summary", summary)

    path.parent.mkdir(parents=True, exist_ok=True)
    deck.save(str(path))
    return path


_HEADER = PatternFill("solid", fgColor="1F3864")


def cell_text(value: Any) -> Any:
    """Web text that starts like a formula is stored as text, so a hostile headline cannot run in Excel."""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


def _sheet(workbook: Workbook, title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]], widths: Sequence[int]) -> None:
    sheet = workbook.create_sheet(title)
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), _HEADER
    for row in rows:
        sheet.append([cell_text(value if not isinstance(value, (list, dict)) else str(value)) for value in row])
    for number, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(number)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def render_xlsx(brief: Mapping[str, Any], signals: Sequence[Mapping[str, Any]], path: Path) -> Path:
    """signals: routed_signals() rows for this reader, shown and hidden, so a reviewer can see what was cut and why."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    _sheet(
        workbook, "Signals",
        ("Shown", "Priority", "Tier", "Rung", "RED", "Title", "Outlet", "Published", "Outlets", "Conditions", "Named items", "Risk register", "Harm verdict", "Evidence", "Why", "Hidden because", "URL", "Signal id"),
        [
            (
                "yes" if row["shown"] else "no", row["route"]["priority"], row.get("tier"), row.get("rung"), "RED" if row.get("red") else "", row["title"], row["publisher"], row["published_at"],
                row.get("corroboration"), ", ".join(row.get("condition_ids") or []), ", ".join(hit["item_name"] for hit in row.get("exposure") or [] if hit.get("strong")),
                "; ".join(f"{hit['level']} {hit['risk_id']} {hit['name']}" for hit in (row.get("risk_cascade") or [])[:4]), row.get("harm_verdict") or "not established",
                row.get("evidence_confidence"), " ".join(row["route"]["matched"][:2]) or row.get("relevance_basis"),
                row["route"]["suppression_reason"] or " ".join(reason for reason in row["memory"]["reasons"] if reason.startswith("Hidden")), row["url"], row["signal_id"],
            )
            for row in signals
        ],
        (7, 8, 6, 13, 6, 60, 22, 20, 8, 16, 24, 40, 18, 10, 50, 30, 40, 22),
    )
    _sheet(
        workbook, "Standing items", ("#", "Item", "Tier", "Trend", "Confidence", "Status", "Basis", "Implication"),
        [
            (row["number"], row["name"], row.get("group") or "", row["trend"], row["confidence"], row["status"], row["basis"]["plain"], (row.get("implication") or {}).get("plain") or "")
            for row in brief.get("conditions") or []
        ],
        (4, 40, 14, 12, 12, 14, 100, 60),
    )
    full = [row for row in brief.get("conditions") or [] if row.get("full")]
    if full and brief["meta"].get("level") == "GROUP":
        options = any(row.get("options") for row in full) or "RESPONSE_OPTIONS" in (brief["meta"].get("sections") or [])
        rows = [["\n".join(lines) for lines in radar_cells(row, options=options)] for row in full]
        _sheet(workbook, "Group radar", radar_headers(options=options), rows, (60, 40, 70, 24, 50, 70, 22) if options else (60, 40, 70, 24, 50, 22))
    elif full:
        options = any(row.get("options") for row in full) or "RESPONSE_OPTIONS" in (brief["meta"].get("sections") or [])
        rows = []
        for row in full:
            refs = row.get("sources") or []
            values = [
                f"{row['number']}. {row['name']}\n{row['development']}",
                "\n".join(f"{number}. {ref['outlet']}: {ref.get('title') or ''} {ref.get('url') or ''}".strip() for number, ref in enumerate(refs, start=1)),
                "\n".join(f"{number}. {ref.get('when') or ref.get('date') or 'Date not stated'}" for number, ref in enumerate(refs, start=1)),
                implication_text(row),
            ]
            if options:
                values.append("\n".join(f"\u2022 {option} ({detail})" for option, detail in response_lines(row)))
            values.append("\n".join(priority_trend(row)))
            rows.append(values)
        widths = (60, 40, 22, 50, 70, 22) if options else (70, 45, 22, 60, 22)
        _sheet(workbook, "Key risks", key_risk_headers(brief["meta"], options=options), rows, widths)
    if brief.get("management_requests"):
        _sheet(
            workbook, "Management requests", ("Request", "Status", "Source", "Signals this week"),
            [(row["title"], row["status"], row["source"] or row["requested_by"], "; ".join(f"{item['title']} ({item['url']})" for item in row["signals"])) for row in brief["management_requests"]], (50, 12, 30, 100),
        )
    named = [("Project", item) for group in brief.get("projects") or [] for item in group["items"]]
    named += [("Entity", item) for group in brief.get("entities") or [] for item in group["items"]]
    named += [("Competitor", item) for item in brief.get("competitors") or []]
    named += [("Supplier", item) for item in brief.get("suppliers") or []] + [("Customer", item) for item in brief.get("customers") or []] + [("Hazard", item) for item in brief.get("hazards") or []]
    _sheet(workbook, "Named items", ("Type", "Item", "Status", "This week"), [(kind, item["name"], item["status"], item["text"]["plain"] if item["text"] else "No new item found") for kind, item in named], (10, 40, 16, 100))
    _sheet(workbook, "Watchpoints", ("Watchpoint", "Status", "Quiet cycles", "Detail", "This week"), [(row["title"], row["status"], row["cycles"], row["detail"], row["update"]["plain"] if row["update"] else "") for row in brief.get("watchpoints") or []], (40, 12, 12, 60, 60))
    _sheet(workbook, "QA log", ("Item", "Prior", "Correction", "Origin"), [(row["item"], row["prior"], row["correction"]["plain"], row["origin"]) for row in brief.get("qa") or []], (40, 50, 80, 10))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(path))
    return path
