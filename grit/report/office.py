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

from grit.report.docx_brief import (
    SCENARIO_HEADERS, SCENARIO_NOTE, ba_headings, implication_text, opportunity_text, radar_cells, radar_headers, response_lines, response_text,
    scenario_cells, scenario_headers,
)
from grit.report.notices import DISCLAIMER
from grit.report.repetition import FirstUse

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
    "ELEVATED": ("\u25cf  ELEVATED", RGBColor(0xA0, 0x5F, 0x00), AMBER_FILL),
    "STABLE": ("\u25cf  STABLE", MUTED, RGBColor(0xEE, 0xF1, 0xF6)),
    "NORMAL": ("\u25cf  NORMAL", MUTED, RGBColor(0xEE, 0xF1, 0xF6)),
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


def whole_sentences(text: str | None, limit: int) -> str:
    """Whole sentences that fit, with no truncation mark. One long sentence is kept whole so the box can wrap it."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    kept, first = "", ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        first = first or sentence
        nxt = f"{kept} {sentence}".strip()
        if len(nxt) > limit:
            break
        kept = nxt
    return kept or first


def summary_slide_count(item_count: int) -> int:
    """One summary slide for up to two critical items, two slides for three or four, and three when there are five or more."""
    if item_count <= 2:
        return 1
    if item_count <= 4:
        return 2
    return 3


def summary_groups(items: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """At most two full cards on a summary slide, across one to three slides. Further items are named on the last slide."""
    count = summary_slide_count(len(items))
    shown = list(items[: count * 2])
    return [shown[start:start + 2] for start in range(0, len(shown), 2)] or [[]]


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
    """The Group radar's conditions the deck details: every condition with evidence this period (spec 37), escalating first."""
    chosen = [row for row in brief.get("conditions") or [] if row.get("status") == "VALIDATED"]
    return sorted(chosen, key=lambda row: (row["trend"] != "Escalating", row.get("tier") != "KEY", row["number"]))


def _notice(slide: Any, deck: Any) -> None:
    """The disclaimer along the bottom of a slide. 914400 English Metric Units make one inch."""
    _words(slide, 0.4, deck.slide_height / 914400 - 0.34, 12.5, 0.28, [(DISCLAIMER, 8, False, MUTED)])


def _summary_card(slide: Any, row: Mapping[str, Any] | None, x: float, width: float, short: str, link: Any, bottom: str) -> None:
    """One summary point. Sentences stay whole, and the box wraps them. Nothing on this card is cut with an ellipsis."""
    tag, colour, _fill, bar = TRENDS.get(row["trend"], STEADY) if row else STEADY
    _shape(slide, x, 1.95, width, 3.35, CARD)
    _shape(slide, x, 1.95, 0.05, 3.35, bar)
    if row is None:
        _words(slide, x + 0.25, 2.15, width - 0.5, 0.3, [(tag, 12, True, colour)])
        _words(slide, x + 0.25, 2.5, width - 0.5, 0.6, [("No standing item moved this week", 18, True, INK)])
        _words(slide, x + 0.25, 3.2, width - 0.5, 1.6, [(whole_sentences(bottom, 400), 13, False, BODY)])
        return
    _words(slide, x + 0.25, 2.05, width - 0.5, 0.28, [(f"{tag}  \u00b7  {str(row.get('group') or 'standing item').upper()}", 12, True, colour)])
    name = " ".join(str(row["name"]).split())
    _words(slide, x + 0.25, 2.35, width - 0.5, 0.58, [(name, 16 if len(name) <= 70 else 13, True, INK)])
    happened = whole_sentences(row["development"], 420)
    _words(slide, x + 0.25, 2.95, width - 0.5, 1.15, [(happened, 12 if len(happened) <= 280 else 10.5, False, BODY)])
    meaning = whole_sentences(implication_text(row), 320)
    _words(slide, x + 0.25, 4.15, width - 0.5, 0.85, [(f"What it means for {short}: {meaning}", 12 if len(meaning) <= 220 else 10.5, True, INK)])
    if link is not None:
        _words(slide, x + width - 1.6, 4.95, 1.35, 0.28, [("Full detail \u25b8", 12, True, BODY)], link=link)


def _dedupe_deck(deck: Any, slides: int | None = None) -> None:
    """Keep the first use of a point on the cover and summary slides (the first `slides`). Appendix slides are the full detail
    a summary card links to, so they keep their text. A hyperlink and the disclaimer stay untouched."""
    state = FirstUse()
    for slide in list(deck.slides)[:slides]:
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for paragraph in shape.text_frame.paragraphs:
                text = paragraph.text
                if not text or any(run.hyperlink.address for run in paragraph.runs):
                    state.observe(text)
                    continue
                updated = state.filter(text)
                if updated != text and paragraph.runs:
                    paragraph.runs[0].text = updated
                    for run in paragraph.runs[1:]:
                        run.text = ""


def _escalation_slide(slide: Any, rows: Sequence[Mapping[str, Any]], short: str) -> None:
    """YK's table (format v2): each escalating or elevated event with its summary, implications and suggested response.
    Whole sentences only; the Word brief carries every source."""
    from pptx.util import Inches, Pt as PptPt

    _words(slide, 0.5, 0.22, 9.0, 0.28, [("ESCALATING AND ELEVATED ITEMS  \u00b7  GRIT-SCAN", 13, True, EYEBROW)])
    _words(slide, 0.5, 0.5, 12.3, 0.45, [(f"What escalated this week, and what it could mean for {short}", 22, True, INK)])
    shown = list(rows[:5])
    table = slide.shapes.add_table(len(shown) + 1, 4, Inches(0.5), Inches(1.15), Inches(12.33), Inches(0.4 + 0.95 * len(shown))).table
    for column, width in enumerate((2.6, 3.6, 3.2, 2.93)):
        table.columns[column].width = Inches(width)
    for column, text in enumerate(("Item and risk", "Summary", f"Implications for {short}", "Suggested response")):
        cell = table.cell(0, column)
        cell.text = text
        cell.text_frame.paragraphs[0].runs[0].font.size = PptPt(11)
        cell.text_frame.paragraphs[0].runs[0].font.bold = True
    for number, row in enumerate(shown, start=1):
        response = row.get("response") or {}
        item = f"{row.get('reader_band') or row['band']} {row['item']}" + (f" [{row['risk']}]" if row["risk"] else "")
        if row.get("urs_reach_reason"):
            channel = f" ({row['reach_channel']})" if row.get("reach_channel") else ""
            item += f"\nURS reach{channel}: {row['urs_reach_reason']}"
        values = (
            item,
            whole_sentences((row.get("summary") or {}).get("plain") or "Withheld: the draft failed its checks. See the Word brief's QA log.", 330),
            whole_sentences((row.get("implications") or {}).get("plain") or "Not drafted.", 260),
            whole_sentences(f"{response.get('action') or 'Not drafted.'} Owner: {response.get('owner') or 'to assign'}.", 220),
        )
        for column, text in enumerate(values):
            cell = table.cell(number, column)
            cell.text = text
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = PptPt(9.5)
    if len(rows) > len(shown):
        _words(slide, 0.5, 6.0, 12.3, 0.3, [(f"{len(rows) - len(shown)} more in the Word brief.", 10, False, MUTED)])


def _cover(deck: Any, cover: Any, meta: Mapping[str, Any]) -> None:
    short, label = meta.get("pack_short") or meta["pack_label"], meta["pack_label"]
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
    _words(cover, 0.5, 6.05, 12.3, 0.55, [
        ((("Draft for review. Not approved. " if meta.get("show_review_status", True) else "") + f"Reader: {meta['reader']}. Run {meta['run_id']}."), 10, False, MUTED),
        (DISCLAIMER, 10, False, MUTED),
    ])
    _notice(cover, deck)


def render_pptx(brief: Mapping[str, Any], path: Path) -> Path:
    """The executive summary in the reference deck's format: a cover, one to three summary slides, and one appendix slide per item."""
    from grit.report.deck_template import CONTENT_LAYOUT, COVER_LAYOUT, TEMPLATE

    if brief["meta"].get("layout") == "BA":
        return _business_area_pptx(brief, path)
    meta, bottom = brief["meta"], brief["bottom_line"]
    short, label = meta.get("pack_short") or meta["pack_label"], meta["pack_label"]
    deck = Presentation(str(TEMPLATE))
    items = deck_items(brief)
    groups = summary_groups(items)
    cover = _blank(deck, COVER_LAYOUT)
    summaries = [_blank(deck, CONTENT_LAYOUT) for _ in groups]
    elevated_rows = (brief.get("v2") or {}).get("escalating") or []
    escalation_slide = _blank(deck, CONTENT_LAYOUT) if elevated_rows else None
    outlook = brief.get("scenarios")
    scenario_slide = _blank(deck, CONTENT_LAYOUT) if outlook and outlook.get("shifts") else None
    appendix = [_blank(deck, CONTENT_LAYOUT) for _ in items]

    _cover(deck, cover, meta)

    shown = [row for group in groups for row in group]
    overflow = [row for row in items if row not in shown]
    for page, (summary, group) in enumerate(zip(summaries, groups), start=1):
        eyebrow = "EXECUTIVE SUMMARY  \u00b7  GRIT-SCAN" if page == 1 else f"EXECUTIVE SUMMARY  \u00b7  CONTINUED  {page} OF {len(summaries)}"
        _words(summary, 0.5, 0.22, 8.0, 0.28, [(eyebrow, 13, True, EYEBROW)])
        if page == 1:
            title = f"{label} \u2014 Weekly Risk Radar"
            _words(summary, 0.5, 0.48, 9.4, 0.55, [(title, 28 if len(title) <= 40 else 22, True, INK)])
            _words(summary, 0.5, 1.05, 9.2, 0.28, [(whole_sentences(f"Scan window: {meta['window']}   \u00b7   Prepared for {meta['prepared_for']}   \u00b7   Group Risk & Assurance", 140), 12, False, MUTED)])
            status, colour, fill = STATUS.get(bottom["label"], STATUS["WATCHPOINT"])
            _badge(summary, 10.13, 0.5, 2.7, 0.5, status, colour, fill, size=13)
            escalating = sum(1 for row in items if row["trend"] == "Escalating")
            caption = "RED impact confirmed by R&A" if bottom["label"] == "RED" else f"{escalating} item(s) escalating this week" if escalating else "no material change this week"
            _words(summary, 10.13, 1.05, 2.7, 0.28, [(caption, 9, False, MUTED)]).text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        _rule(summary, 1.42)
        _words(summary, 0.5, 1.5, 12.3, 0.32, [(f"\u25b2  NEW & ESCALATING \u2014 WHAT'S CHANGED, AND WHAT IT MEANS FOR {short.upper()}", 15, True, TRENDS["Escalating"][1])])
        cards = group or [None]
        card_width = 12.33 if len(cards) == 1 else 6.05
        for number, row in enumerate(cards):
            link = appendix[items.index(row)] if row is not None else None
            _summary_card(summary, row, 0.5 + number * 6.28, card_width, short, link, bottom["text"])
        if page == len(summaries) and overflow:
            names = "   \u00b7   ".join(f"{row['name']} (A{items.index(row) + 1})" for row in overflow)
            _words(summary, 0.5, 5.4, 12.3, 0.55, [(f"Also in the appendix: {names}", 11, False, BODY)])
        if page == 1:
            _shape(summary, 0.5, 5.42, 12.33, 0.72, AMBER_FILL)
            _words(summary, 0.7, 5.42, 12.0, 0.72, [(f"BOTTOM LINE   {whole_sentences(bottom['text'], 420)}", 12, True, EYEBROW)], middle=True)
        if page == len(summaries) and appendix:
            _link(summary, 0.5, 6.22, f"Full supporting detail \u2014 Appendix A1\u2013A{len(appendix)}" if len(appendix) > 1 else "Full supporting detail \u2014 Appendix A1", appendix[0], dot=GOLD)
            _link(summary, 9.08, 6.22, "Ask GRIT-Scan chat about this full report", None, colour=EYEBROW, dot=GOLD, w=3.55)
        elif page < len(summaries):
            _link(summary, 0.5, 6.22, "Continued on the next summary slide", summaries[page], dot=GOLD)
        _notice(summary, deck)

    if escalation_slide is not None:
        _escalation_slide(escalation_slide, elevated_rows, short)
        _notice(escalation_slide, deck)

    if scenario_slide is not None:
        _scenario_slide(scenario_slide, outlook)
        _notice(scenario_slide, deck)

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
        _link(slide, 0.5, 6.48, "Back to Executive Summary", summaries[0])
        _notice(slide, deck)

    _dedupe_deck(deck, slides=1 + len(summaries))
    path.parent.mkdir(parents=True, exist_ok=True)
    deck.save(str(path))
    return path


_HEADER = PatternFill("solid", fgColor="1F3864")


def cell_text(value: Any) -> Any:
    """Web text that starts like a formula is stored as text, so a hostile headline cannot run in Excel."""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


LEAN_COLOURS = {"BEST": "Easing", "WORST": "Escalating"}


def _scenario_slide(slide: Any, outlook: Mapping[str, Any]) -> None:
    """One row per strategic shift: where the signposts lean, and the first signpost seen."""
    from grit.pipeline.scenarios import lean_text

    _words(slide, 0.5, 0.24, 9.0, 0.3, [("SCENARIO OUTLOOK  \u00b7  BEST, MEDIUM AND WORST CASE FOR STE", 13, True, EYEBROW)])
    _words(slide, 0.5, 0.52, 12.3, 0.62, [("Where the strategic shifts are heading", 28, True, INK)])
    _rule(slide, 1.3)
    shifts = list(outlook["shifts"])[:8]
    height = min(0.62, 5.0 / max(1, len(shifts)))
    for number, reading in enumerate(shifts):
        y = 1.45 + number * (height + 0.06)
        trend = LEAN_COLOURS.get(reading["lean"])
        _tag, colour, fill, bar = TRENDS.get(trend, STEADY) if trend else STEADY
        _shape(slide, 0.5, y, 12.33, height, CARD)
        _shape(slide, 0.5, y, 0.05, height, bar)
        _words(slide, 0.68, y, 3.6, height, [(fit(reading["name"], 60), 12, True, INK)], middle=True)
        _badge(slide, 4.35, y + 0.08, 3.1, height - 0.16, fit(lean_text(reading).rstrip("."), 60), colour, fill, size=10)
        seen = [f"{case['title'].split(' case')[0]}: {item['text']}" for case in reading["cases"] for item in case["signposts_seen"]]
        _words(slide, 7.6, y, 5.15, height, [(fit("; ".join(seen) or "No signpost seen this period.", 150), 10, False, BODY)], middle=True)
    _words(slide, 0.5, 6.62, 12.3, 0.5, [(f"{SCENARIO_NOTE} Scenarios v{outlook['version']}, reviewed by {outlook['reviewed_by']} on {outlook['reviewed_on']}.", 9, False, MUTED)])


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


def _signals_sheet(workbook: Workbook, signals: Sequence[Mapping[str, Any]]) -> None:
    from grit.agents.weekly import reach_fields_of
    from grit.core.overrides import reader_band_of

    def _priority(row: Mapping[str, Any]) -> Mapping[str, Any]:
        value = row.get("priority") if isinstance(row.get("priority"), Mapping) else row.get("priority_json")
        return value if isinstance(value, Mapping) else {}

    def _editorial(row: Mapping[str, Any]) -> Mapping[str, Any]:
        value = row.get("editorial")
        return value if isinstance(value, Mapping) else {}

    _sheet(
        workbook, "Signals",
        ("Shown", "Priority", "Band", "Order in band", "Reach channel", "URS reach", "Tier", "Override", "Rung", "RED", "Title", "Outlet", "Published", "Outlets", "Conditions", "Named items", "Risk register", "Harm verdict", "Evidence", "Why", "Not shown because", "URL", "Signal id"),
        [
            (
                "yes" if row["shown"] else "no", row["route"]["priority"], reader_band_of(row),
                reach_fields_of(row).get("order_in_band") or "",
                reach_fields_of(row).get("reach_channel") or "",
                reach_fields_of(row).get("urs_reach_reason") or "",
                row.get("tier"),
                _priority(row).get("override_rule_id") or "",
                row.get("rung"), {"CONFIRMED": "RED", "CANDIDATE": "Candidate", "NOT_CONFIRMED": "Not confirmed"}.get(row.get("red_status") or "", "Candidate" if row.get("red") else ""), row["title"], row["publisher"], row["published_at"],
                row.get("corroboration"), ", ".join(row.get("condition_ids") or []), ", ".join(hit["item_name"] for hit in row.get("exposure") or [] if hit.get("strong")),
                "; ".join(f"{hit['level']} {hit['risk_id']} {hit['name']}" for hit in (row.get("risk_cascade") or [])[:4]), row.get("harm_verdict") or "not established",
                row.get("evidence_confidence"),
                ((" ".join(row["route"]["matched"][:2]) or row.get("relevance_basis") or "") +
                 (f" Editorial {_editorial(row).get('decision')}; {_editorial(row).get('time_scope')}: {_editorial(row).get('reason')}" if _editorial(row) else "")),
                row["route"]["suppression_reason"] or (_editorial(row).get("reason") if _editorial(row) and not row["shown"] else ""),
                row["url"], row["signal_id"],
            )
            for row in signals
        ],
        (7, 8, 12, 10, 12, 40, 6, 22, 13, 6, 60, 22, 20, 8, 16, 24, 40, 18, 10, 50, 30, 40, 22),
    )


def render_xlsx(brief: Mapping[str, Any], signals: Sequence[Mapping[str, Any]], path: Path) -> Path:
    """signals: routed_signals() rows for this reader, shown and hidden, so a reviewer can see what was cut and why."""
    if brief["meta"].get("layout") == "BA":
        return business_area_xlsx(brief, signals, path)
    workbook = Workbook()
    workbook.remove(workbook.active)
    _signals_sheet(workbook, signals)
    _sheet(
        workbook, "Standing items", ("#", "Item", "Tier", "Trend", "Confidence", "Status", "Basis", "Implication"),
        [
            (row["number"], row["name"], row.get("group") or "", row["trend"], row["confidence"], row["status"], row["basis"]["plain"], (row.get("implication") or {}).get("plain") or "")
            for row in brief.get("conditions") or []
        ],
        (4, 40, 14, 12, 12, 14, 100, 60),
    )
    full = [row for row in brief.get("conditions") or [] if row.get("full")]
    if full:
        options = any(row.get("options") for row in full) or "RESPONSE_OPTIONS" in (brief["meta"].get("sections") or [])
        rows = [["\n".join(lines) for lines in radar_cells(row, options=options)] for row in full]
        _sheet(workbook, "Group radar", radar_headers(options=options), rows, (60, 40, 70, 24, 50, 70, 22) if options else (60, 40, 70, 24, 50, 22))
    outlook = brief.get("scenarios") or {}
    if outlook.get("shifts"):
        _sheet(workbook, "Scenario outlook", SCENARIO_HEADERS, [["\n".join(lines) for lines in scenario_cells(reading)] for reading in outlook["shifts"]], (50, 40, 90, 60, 60, 60))
    if outlook.get("indicators"):
        _sheet(workbook, "Leading indicators", ("Indicator", "Measure", "Reading", "Band", "Green up to", "Amber up to", "Bounds", "When red", "Applies to"),
               [(item["name"], item.get("measure") or "kept signals in the window", "" if item.get("reading", item["signals"]) is None else item.get("reading", item["signals"]), item["band"],
                 "" if item.get("green_max") is None else item["green_max"], "" if item.get("amber_max") is None else item["amber_max"],
                 (item.get("bounds_status") or "not set").lower(), item.get("action") or "", ", ".join(item["applies_to"])) for item in outlook["indicators"]],
               (28, 30, 10, 14, 12, 12, 12, 60, 40))
    if outlook.get("risk_links"):
        _sheet(workbook, "Risk links", ("Group risk", "BA risks", "LOB risks", "Status"),
               [(f"{row['group_risk']} {row['name']}".strip(), "; ".join(f"{item['id']} {item['name']}" for item in row["ba"]), "; ".join(item["name"] for item in row["lob"]), row["status"]) for row in outlook["risk_links"]],
               (40, 50, 30, 14))
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


# Business-area layout (Wilson, 19 November 2026) ---------------------------------------------------------------------

SECTION_SLIDE_ROWS = 4


def section_cells(row: Mapping[str, Any]) -> list[str]:
    """One business-area row as plain text, per column after "#": item and risk, summary, impact and opportunity,
    response with its owner, and sources."""
    impact = (row.get("impact") or {}).get("plain") or "Not established from the cited sources."
    if row.get("opportunity"):
        impact += f" Opportunity: {row['opportunity']['plain']}"
    return [f"{row['band']} {row['item']}\n[{row['risk']}]", (row.get("summary") or {}).get("plain") or "", impact,
            "\n".join(response_text(row)), ", ".join(dict.fromkeys(str(ref.get("outlet")) for ref in row.get("sources") or []))]


def _section_slide(slide: Any, title: str, rows: Sequence[Mapping[str, Any]], page: int, pages: int) -> None:
    from pptx.util import Pt as PptPt

    eyebrow = title.upper() + (f"  \u00b7  {page} OF {pages}" if pages > 1 else "") + "  \u00b7  GRIT-SCAN"
    _words(slide, 0.5, 0.22, 12.3, 0.28, [(eyebrow, 13, True, EYEBROW)])
    _words(slide, 0.5, 0.5, 12.3, 0.45, [(title, 22, True, INK)])
    table = slide.shapes.add_table(len(rows) + 1, 4, Inches(0.5), Inches(1.15), Inches(12.33), Inches(0.4 + 1.1 * len(rows))).table
    for column, width in enumerate((2.4, 3.7, 3.4, 2.83)):
        table.columns[column].width = Inches(width)
    for column, text in enumerate(("Item and Risk", "Summary", "Potential Impact/Opportunities", "Proposed Response (Owner)")):
        cell = table.cell(0, column)
        cell.text = text
        cell.text_frame.paragraphs[0].runs[0].font.size = PptPt(11)
        cell.text_frame.paragraphs[0].runs[0].font.bold = True
    for number, row in enumerate(rows, start=1):
        item, summary, impact, response, _sources = section_cells(row)
        for column, text in enumerate((item, whole_sentences(summary, 330), whole_sentences(impact, 300), whole_sentences(response, 220))):
            cell = table.cell(number, column)
            cell.text = text
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = PptPt(9.5)


def _business_area_pptx(brief: Mapping[str, Any], path: Path) -> Path:
    """The business-area deck: a cover, the summary, one table slide per section with items, and the scenario outlook."""
    from grit.report.deck_template import CONTENT_LAYOUT, COVER_LAYOUT, TEMPLATE

    meta, bottom = brief["meta"], brief["bottom_line"]
    deck = Presentation(str(TEMPLATE))
    _cover(deck, _blank(deck, COVER_LAYOUT), meta)
    summary = _blank(deck, CONTENT_LAYOUT)
    _words(summary, 0.5, 0.22, 8.0, 0.28, [("SUMMARY  \u00b7  GRIT-SCAN", 13, True, EYEBROW)])
    title = f"{meta['pack_label']} \u2014 Weekly Risk Scan"
    _words(summary, 0.5, 0.48, 12.3, 0.55, [(title, 28 if len(title) <= 40 else 22, True, INK)])
    _words(summary, 0.5, 1.05, 12.3, 0.28, [(f"Scan window: {meta['window']}   \u00b7   Prepared for {meta['prepared_for']}", 12, False, MUTED)])
    _rule(summary, 1.42)
    _shape(summary, 0.5, 1.6, 12.33, 3.0, AMBER_FILL)
    _words(summary, 0.75, 1.7, 11.8, 2.8, [(whole_sentences(bottom["text"], 900), 14, False, INK)], middle=True)
    sections = brief["v2"]["sections"]
    counts = [f"{title.split('. ', 1)[-1]}: {len(sections.get(key) or [])}" for _level, title, key in ba_headings(brief) if key]
    _words(summary, 0.5, 4.8, 12.3, 0.5, [("Items this week \u2014 " + "   \u00b7   ".join(counts), 12, True, BODY)])
    _notice(summary, deck)
    for _level, title, key in ba_headings(brief):
        rows = list(sections.get(key) or []) if key else []
        pages = [rows[start:start + SECTION_SLIDE_ROWS] for start in range(0, len(rows), SECTION_SLIDE_ROWS)]
        for page, chunk in enumerate(pages, start=1):
            slide = _blank(deck, CONTENT_LAYOUT)
            _section_slide(slide, title.split(". ", 1)[-1], chunk, page, len(pages))
            _notice(slide, deck)
    outlook = brief.get("scenarios")
    if outlook and outlook.get("shifts"):
        slide = _blank(deck, CONTENT_LAYOUT)
        _scenario_slide(slide, outlook)
        _notice(slide, deck)
    path.parent.mkdir(parents=True, exist_ok=True)
    deck.save(str(path))
    return path


def business_area_xlsx(brief: Mapping[str, Any], signals: Sequence[Mapping[str, Any]], path: Path) -> Path:
    """One sheet per table of the business-area report, the source list, and the reviewer's signal list."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    v2 = brief["v2"]
    for _level, title, key in ba_headings(brief):
        if key:
            rows = [[number, *section_cells(row)] for number, row in enumerate(v2["sections"].get(key) or [], start=1)]
            _sheet(workbook, title.split(". ", 1)[-1].replace("/", "-")[:31], ("#", "Item and Risk", "Summary", "Potential Impact/Opportunities",
                   "Proposed Response/Management Consideration (Owner)", "Sources"), rows, (4, 40, 80, 70, 50, 30))
    _sheet(workbook, "Look-ahead", ("Date", "Event, as the source states it", f"Relevance to {v2['short']}", "Sources"),
           [(row["date"], row["event"], row["relevance"] or "Linked by the scan", "; ".join(f"{ref['outlet']} {ref.get('url') or ''}".strip() for ref in row.get("sources") or []))
            for row in v2["look_ahead"]], (12, 90, 30, 60))
    outlook = brief.get("scenarios") or {}
    if outlook.get("shifts"):
        _sheet(workbook, "Scenario outlook", scenario_headers(v2["short"]), [["\n".join(lines) for lines in scenario_cells(reading)] for reading in outlook["shifts"]], (50, 40, 90, 60, 60, 60))
    _sheet(workbook, "Jurisdictions", ("Jurisdiction", f"{v2['short']} entities", "In-window finding", "Trend"),
           [(row["country"], "; ".join(row["entities"]) + (f"; and {row['more_entities']} more" if row["more_entities"] else ""), row["finding"], row["trend"]) for row in v2["jurisdictions"]],
           (16, 60, 80, 8))
    _sheet(workbook, "Named watch", ("Name", "Type", "This week", "Proposed Response from Lessons Learnt"),
           [(row["name"], row["type"], (row["text"] or {}).get("plain") or "", row.get("lesson") or "Not drafted.") for row in v2["watch"]], (40, 12, 90, 60))
    refs = {ref["signal_id"]: ref for rows in v2["sections"].values() for row in rows for ref in row.get("sources") or []}
    refs |= {ref["signal_id"]: ref for row in [*v2["look_ahead"], *v2["jurisdictions"]] for ref in row.get("sources") or []}
    _sheet(workbook, "Sources", ("Outlet", "Title", "Date", "Tier", "URL"),
           [(ref["outlet"], ref.get("title") or "", ref.get("date") or "", (v2.get("tiers") or {}).get(key, "T4"), ref.get("url") or "") for key, ref in refs.items()],
           (24, 70, 14, 10, 60))
    _signals_sheet(workbook, signals)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(path))
    return path
