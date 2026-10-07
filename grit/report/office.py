"""Render a brief model as a short PowerPoint deck and as an Excel signal list."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

NAVY = RGBColor(0x1F, 0x38, 0x64)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREY = RGBColor(0x55, 0x55, 0x55)
BOX = {"WATCHPOINT": RGBColor(0xFF, 0xF2, 0xCC), "STABLE": RGBColor(0xE2, 0xEF, 0xDA), "RED": RGBColor(0xF8, 0xCB, 0xAD)}


def _title(slide: Any, text: str, sub: str | None = None) -> None:
    box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12.3), Inches(0.9))
    frame = box.text_frame
    frame.word_wrap = True
    run = frame.paragraphs[0].add_run()
    run.text, run.font.size, run.font.bold, run.font.color.rgb = text, Pt(26), True, NAVY
    if sub:
        paragraph = frame.add_paragraph()
        run = paragraph.add_run()
        run.text, run.font.size, run.font.color.rgb = sub, Pt(12), GREY


def _text(slide: Any, lines: Sequence[str], *, top: float = 1.4, size: int = 14, height: float = 5.6) -> None:
    box = slide.shapes.add_textbox(Inches(0.6), Inches(top), Inches(12.1), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    for number, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if number == 0 else frame.add_paragraph()
        run = paragraph.add_run()
        run.text, run.font.size = line, Pt(size)
        paragraph.space_after = Pt(6)


def render_pptx(brief: Mapping[str, Any], path: Path) -> Path:
    meta = brief["meta"]
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    blank = deck.slide_layouts[6]

    slide = deck.slides.add_slide(blank)
    _title(slide, meta["title"], f"{meta['subtitle']} | {meta['window']} | Prepared for {meta['prepared_for']}")
    bottom = brief["bottom_line"]
    shape = slide.shapes.add_shape(1, Inches(0.6), Inches(1.6), Inches(12.1), Inches(3.6))
    shape.fill.solid()
    shape.fill.fore_color.rgb = BOX.get(bottom["label"], BOX["WATCHPOINT"])
    shape.line.color.rgb = NAVY
    frame = shape.text_frame
    frame.word_wrap = True
    run = frame.paragraphs[0].add_run()
    run.text, run.font.bold, run.font.size, run.font.color.rgb = f"{bottom['label']} — Bottom line", True, Pt(18), NAVY
    for line in (bottom["text"] or "", bottom.get("escalation") or ""):
        paragraph = frame.add_paragraph()
        run = paragraph.add_run()
        run.text, run.font.size, run.font.color.rgb = line, Pt(14), RGBColor(0, 0, 0)
    _text(slide, [f"Draft for review. Not approved. Run {meta['run_id']}."], top=5.5, size=10, height=0.5)

    if brief.get("conditions"):
        slide = deck.slides.add_slide(blank)
        prior = meta.get("prior_run_date")
        _title(slide, "Ten standing conditions", f"Trend vs {prior}" if prior else "First weekly assessment")
        rows = brief["conditions"]
        table = slide.shapes.add_table(len(rows) + 1, 4, Inches(0.5), Inches(1.3), Inches(12.3), Inches(5.4)).table
        for column, (header, width) in enumerate((("Condition", 4.2), ("Trend", 1.5), ("Confidence", 1.5), ("This week", 5.1))):
            table.columns[column].width = Inches(width)
            cell = table.cell(0, column)
            cell.text = header
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY
            cell.text_frame.paragraphs[0].runs[0].font.color.rgb = WHITE
            cell.text_frame.paragraphs[0].runs[0].font.size = Pt(11)
        for number, row in enumerate(rows, start=1):
            basis = row["basis"]["plain"]
            values = (f"{row['number']}. {row['name']}", row["trend"], (row["confidence"] or "").title(), basis[:220] + ("…" if len(basis) > 220 else ""))
            for column, value in enumerate(values):
                cell = table.cell(number, column)
                cell.text = value
                for paragraph in cell.text_frame.paragraphs:
                    for item in paragraph.runs:
                        item.font.size = Pt(9)

    named = [item for group in brief.get("projects") or [] for item in group["items"] if item["text"]]
    named += [item for item in (brief.get("suppliers") or []) + (brief.get("customers") or []) + (brief.get("hazards") or []) if item["text"]]
    if brief.get("projects") or brief.get("suppliers"):
        slide = deck.slides.add_slide(blank)
        _title(slide, "Named projects, suppliers, customers and hazards", "Every named item is checked each week; silence is reported as such")
        lines = [f"{item['name']}: {item['text']['plain']}" for item in named[:8]] or ["No item this week names a URS Taiwan project, supplier, customer, or hazard near a site."]
        _text(slide, lines, size=12)

    if brief.get("findings"):
        slide = deck.slides.add_slide(blank)
        _title(slide, "Priority findings", "Ordered by news priority (P1 to P4), then briefing tier")
        lines = [f"[{item['priority'] + ' · ' if item['priority'] else ''}tier {item['tier']}] {item['title']} ({item['outlet']}, {item['date'] or 'n.d.'})" for item in brief["findings"][:8]]
        _text(slide, lines, size=12)

    slide = deck.slides.add_slide(blank)
    _title(slide, "Watchpoints and QA", None)
    lines = [f"{row['title']} ({row['status'].lower()}): {row['update']['plain'] if row['update'] else row['detail']}" for row in brief["watchpoints"] if row["status"] != "CLOSED"]
    lines += [f"QA: {row['item']}: {row['correction']['plain']}" for row in brief["qa"][:4]]
    lines.append(f"Escalation: {bottom.get('escalation') or ''}")
    _text(slide, lines, size=12)

    path.parent.mkdir(parents=True, exist_ok=True)
    deck.save(str(path))
    return path


_HEADER = PatternFill("solid", fgColor="1F3864")


def _sheet(workbook: Workbook, title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]], widths: Sequence[int]) -> None:
    sheet = workbook.create_sheet(title)
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), _HEADER
    for row in rows:
        sheet.append([value if not isinstance(value, (list, dict)) else str(value) for value in row])
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
        workbook, "Conditions", ("#", "Condition", "Trend", "Confidence", "Status", "Basis"),
        [(row["number"], row["name"], row["trend"], row["confidence"], row["status"], row["basis"]["plain"]) for row in brief.get("conditions") or []], (4, 40, 12, 12, 14, 100),
    )
    named = [("Project", item) for group in brief.get("projects") or [] for item in group["items"]]
    named += [("Supplier", item) for item in brief.get("suppliers") or []] + [("Customer", item) for item in brief.get("customers") or []] + [("Hazard", item) for item in brief.get("hazards") or []]
    _sheet(workbook, "Named items", ("Type", "Item", "Status", "This week"), [(kind, item["name"], item["status"], item["text"]["plain"] if item["text"] else "No new item found") for kind, item in named], (10, 40, 16, 100))
    _sheet(workbook, "Watchpoints", ("Watchpoint", "Status", "Quiet cycles", "Detail", "This week"), [(row["title"], row["status"], row["cycles"], row["detail"], row["update"]["plain"] if row["update"] else "") for row in brief.get("watchpoints") or []], (40, 12, 12, 60, 60))
    _sheet(workbook, "QA log", ("Item", "Prior", "Correction", "Origin"), [(row["item"], row["prior"], row["correction"]["plain"], row["origin"]) for row in brief.get("qa") or []], (40, 50, 80, 10))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(path))
    return path
