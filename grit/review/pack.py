"""The daily review packs (spec 48): Word, PowerPoint and Excel for each mail.

BP pack, 09:00: every signal the R&A BP reviews today. Manager pack, 13:00: only the signals the BP approved, with the BP's
decision. Final pack, 18:00: only the signals both approved. A signal that either reviewer rejected, or left undecided at the
deadline, never reaches the final pack. Text comes from the pool and the checked arguments, never from a model at render time.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit import settings
from grit.pool import Pool
from grit.review.argue import withheld_text
from grit.review.batch import decisions, items, records, steps
from grit.review.config import BP, MANAGER, ReviewConfig, batch_id
from grit.review.sharepoint import LABELS

FINAL = "FINAL"
PACK_TITLES = {BP: "R&A Business Partner review", MANAGER: "Business Area Manager review", FINAL: "Approved signals"}
DISCLAIMER = (
    "GRIT-Scan found these signals and Gemini argued each one. Rules checked every quote and number against the page. "
    "A named person approves or rejects each signal. Only the final pack lists signals that both reviewers approved."
)


@dataclass
class PackRow:
    position: int
    signal_id: str
    title: str
    url: str
    publisher: str
    published: str
    tier: Any
    red: bool
    risk_level: str
    trend: str
    evidence: str
    relevance: str
    case_for: str
    case_against: str
    critic_view: str
    memory_note: str
    bp: Mapping[str, Any] | None = None
    manager: Mapping[str, Any] | None = None


@dataclass
class Pack:
    stage: str
    config: ReviewConfig
    day: date
    batch: str
    rows: list[PackRow]
    overflow: list[Mapping[str, Any]] = field(default_factory=list)
    approved_hash: str | None = None

    @property
    def title(self) -> str:
        return f"{self.config.title}: {PACK_TITLES[self.stage]}, {self.day.strftime('%d %B %Y').lstrip('0')}"

    @property
    def stem(self) -> str:
        return f"GRIT-Scan_{self.config.pack}_daily-review_{self.day.isoformat()}_{self.stage.lower()}"

    def lines(self) -> list[str]:
        """The cover lines: who reviews, by when, and what the pack holds."""
        bp, manager = self.config.bp, self.config.manager
        if self.stage == BP:
            return [f"For review by {bp.display_name} ({bp.role}) by {bp.close_at:%H:%M} SGT.",
                    f"{len(self.rows)} signal(s). Approve or Reject each row in the SharePoint list. A row with no decision at {bp.close_at:%H:%M} counts as not approved."]
        if self.stage == MANAGER:
            return [f"For review by {manager.display_name} ({manager.role}) by {manager.close_at:%H:%M} SGT.",
                    f"{len(self.rows)} signal(s) that {bp.display_name} approved. A row with no decision at {manager.close_at:%H:%M} counts as not approved."]
        return [f"Approved by {bp.display_name} ({bp.role}) and {manager.display_name} ({manager.role}).",
                f"{len(self.rows)} signal(s) approved by both." if self.rows else "No signal was approved by both reviewers today.",
                f"Approved set SHA-256: {self.approved_hash}"]


def _stamp(value: Any, zone: tzinfo) -> str:
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    label = "SGT" if getattr(zone, "key", "") == "Asia/Singapore" else getattr(zone, "key", "UTC")
    return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M ") + label


def decision_text(row: Mapping[str, Any] | None, zone: tzinfo = timezone.utc) -> str:
    if not row:
        return ""
    text = LABELS.get(row["decision"], row["decision"])
    if row["decision"] != "NO_DECISION" and row.get("decided_at"):
        text += f", {_stamp(row['decided_at'], zone)}"
    return text + (f". {row['comment']}" if row.get("comment") else "")


def approved_hash(signal_ids: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(signal_ids)).encode("utf-8")).hexdigest()


def build_pack(pool: Pool, config: ReviewConfig, day: date, stage: str) -> Pack:
    batch = batch_id(day, config.pack)
    found = records(pool, batch)
    bp_decisions = decisions(pool, batch, BP)
    manager_decisions = decisions(pool, batch, MANAGER)
    source_stage = BP if stage in {BP, FINAL} else MANAGER
    chosen = items(pool, batch, MANAGER if stage == FINAL else source_stage)
    if stage == FINAL:
        chosen = [item for item in chosen
                  if (bp_decisions.get(item["signal_id"]) or {}).get("decision") == "APPROVE" and (bp_decisions.get(item["signal_id"]) or {}).get("final")
                  and (manager_decisions.get(item["signal_id"]) or {}).get("decision") == "APPROVE" and (manager_decisions.get(item["signal_id"]) or {}).get("final")]
    rows: list[PackRow] = []
    for position, item in enumerate(chosen, start=1):
        record = found.get(item["signal_id"])
        if record is None:
            continue
        case_for, case_against, view = withheld_text(record.get("argument"))
        rows.append(PackRow(
            position=position, signal_id=item["signal_id"], title=str(record.get("title") or ""), url=str(record.get("url") or ""),
            publisher=str(record.get("publisher") or ""), published=str(record.get("published_at") or "")[:10], tier=record.get("tier"),
            red=bool(record.get("red")), risk_level=str(record.get("risk_level") or ""), trend=str(record.get("trend") or ""),
            evidence=str(record.get("evidence_confidence") or ""), relevance=str(record.get("relevance_basis") or ""),
            case_for=case_for, case_against=case_against, critic_view=view, memory_note=str(item.get("memory_note") or ""),
            bp=bp_decisions.get(item["signal_id"]) if stage != BP else None,
            manager=manager_decisions.get(item["signal_id"]) if stage == FINAL else None,
        ))
    overflow: list[Mapping[str, Any]] = []
    if stage == BP:
        argue_step = steps(pool, batch).get("ARGUE") or {}
        for signal_id in (argue_step.get("outcome") or {}).get("overflow") or []:
            signal = pool.signal(signal_id) or {}
            overflow.append({"signal_id": signal_id, "title": signal.get("title"), "url": signal.get("url"), "publisher": signal.get("publisher")})
    return Pack(stage=stage, config=config, day=day, batch=batch, rows=rows, overflow=overflow,
                approved_hash=approved_hash([row.signal_id for row in rows]) if stage == FINAL else None)


# Word -------------------------------------------------------------------------------------------------------------

def render_docx(pack: Pack, path: Path) -> Path:
    from docx import Document
    from docx.shared import Pt, RGBColor

    from grit.report.docx_brief import _hyperlink

    document = Document()
    style = document.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(10.5)
    document.add_heading(pack.title, level=0)
    for line in pack.lines():
        document.add_paragraph(line)
    note = document.add_paragraph(DISCLAIMER)
    note.runs[0].font.size, note.runs[0].font.color.rgb = Pt(9), RGBColor(0x55, 0x55, 0x55)
    if pack.rows:
        table = document.add_table(rows=1, cols=5)
        table.style = "Light Grid Accent 1"
        for cell, text in zip(table.rows[0].cells, ("#", "Signal", "Source", "Tier", "RED")):
            cell.text = text
        for row in pack.rows:
            cells = table.add_row().cells
            for cell, text in zip(cells, (str(row.position), row.title, row.publisher, f"P{row.tier}" if row.tier else "", "RED" if row.red else "")):
                cell.text = text
    for row in pack.rows:
        document.add_heading(f"{row.position}. {row.title}", level=2)
        source = document.add_paragraph(f"{row.publisher}, {row.published}. ")
        if row.url:
            _hyperlink(source, "Open the page", row.url)
        document.add_paragraph(f"Tier P{row.tier}" + (" · RED" if row.red else "") + f" · Risk level {row.risk_level or 'not established'}"
                               f" · Trend {row.trend or 'not established'} · Evidence {row.evidence or 'not established'}")
        for label, text in (("Why the rules kept it", row.relevance), ("Case for (Gemini Proposer)", row.case_for),
                            ("Case against (Gemini Critic)", row.case_against + (f" View: {row.critic_view}." if row.critic_view else "")),
                            ("Reviewer memory", row.memory_note if pack.stage != FINAL else ""),
                            (f"{pack.config.bp.display_name}", decision_text(row.bp, pack.config.zone)),
                            (f"{pack.config.manager.display_name}", decision_text(row.manager, pack.config.zone))):
            if text:
                paragraph = document.add_paragraph()
                paragraph.add_run(f"{label}: ").bold = True
                paragraph.add_run(text)
    if not pack.rows:
        document.add_paragraph("Nothing to list." if pack.stage != FINAL else "No signal was approved by both reviewers today.")
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    return path


# PowerPoint -------------------------------------------------------------------------------------------------------

def render_pptx(pack: Pack, path: Path) -> Path:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    from grit.report.office import BODY, INK, MUTED, fit

    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    blank = deck.slide_layouts[6]

    def text(slide: Any, x: float, y: float, w: float, h: float, lines: Sequence[tuple[str, float, bool, Any]]) -> None:
        frame = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        frame.word_wrap = True
        for index, (words, size, bold, colour) in enumerate(lines):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            run = paragraph.add_run()
            run.text, run.font.size, run.font.bold, run.font.color.rgb = words, Pt(size), bold, colour

    cover = deck.slides.add_slide(blank)
    text(cover, 0.7, 1.6, 12, 1.6, [(pack.title, 30, True, INK)])
    text(cover, 0.7, 3.4, 12, 2.4, [(line, 16, False, BODY) for line in pack.lines()] + [("", 10, False, BODY), (DISCLAIMER, 11, False, MUTED)])
    for start in range(0, len(pack.rows), 10):
        slide = deck.slides.add_slide(blank)
        text(slide, 0.6, 0.4, 12, 0.7, [("Signals" + (f" ({start + 1}–{min(start + 10, len(pack.rows))})" if len(pack.rows) > 10 else ""), 24, True, INK)])
        text(slide, 0.6, 1.2, 12.2, 5.8, [(f"{row.position}. {fit(row.title, 110)}  ·  {row.publisher}  ·  P{row.tier}" + ("  ·  RED" if row.red else ""), 13, False, BODY)
                                           for row in pack.rows[start : start + 10]])
    for row in pack.rows:
        slide = deck.slides.add_slide(blank)
        text(slide, 0.6, 0.35, 12.2, 1.1, [(f"{row.position}. {fit(row.title, 140)}", 20, True, INK),
                                            (f"{row.publisher}, {row.published}  ·  P{row.tier}" + ("  ·  RED" if row.red else "") + f"  ·  {row.trend}", 12, False, MUTED)])
        body = [("Case for", 13, True, INK), (fit(row.case_for, 520), 12, False, BODY), ("", 6, False, BODY),
                ("Case against" + (f" (view: {row.critic_view})" if row.critic_view else ""), 13, True, INK), (fit(row.case_against, 420), 12, False, BODY)]
        for label, decision in ((pack.config.bp.display_name, row.bp), (pack.config.manager.display_name, row.manager)):
            if decision:
                body += [("", 6, False, BODY), (f"{label}: {fit(decision_text(decision, pack.config.zone), 200)}", 12, True, BODY)]
        text(slide, 0.6, 1.6, 12.2, 5.6, body)
    if not pack.rows:
        slide = deck.slides.add_slide(blank)
        text(slide, 0.7, 3.0, 12, 1.0, [("No signal was approved by both reviewers today." if pack.stage == FINAL else "Nothing to list.", 20, False, BODY)])
    path.parent.mkdir(parents=True, exist_ok=True)
    deck.save(str(path))
    return path


# Excel ------------------------------------------------------------------------------------------------------------

def render_xlsx(pack: Pack, path: Path) -> Path:
    from openpyxl import Workbook

    from grit.report.office import _sheet

    workbook = Workbook()
    workbook.remove(workbook.active)
    headers = ["#", "Signal ID", "Headline", "Source", "Published", "Link", "Tier", "RED", "Risk level", "Trend", "Evidence",
               "Why kept", "Case for", "Case against", "Critic view"]
    widths = [5, 22, 50, 20, 12, 40, 6, 6, 10, 18, 10, 50, 60, 60, 12]
    if pack.stage != BP:
        headers.append(f"{pack.config.bp.display_name} (R&A BP)")
        widths.append(30)
    if pack.stage == FINAL:
        headers.append(f"{pack.config.manager.display_name} (BA Manager)")
        widths.append(30)
    rows = []
    for row in pack.rows:
        values: list[Any] = [row.position, row.signal_id, row.title, row.publisher, row.published, row.url, row.tier, "RED" if row.red else "",
                             row.risk_level, row.trend, row.evidence, row.relevance, row.case_for, row.case_against, row.critic_view]
        if pack.stage != BP:
            values.append(decision_text(row.bp, pack.config.zone))
        if pack.stage == FINAL:
            values.append(decision_text(row.manager, pack.config.zone))
        rows.append(values)
    _sheet(workbook, "Signals", headers, rows, widths)
    _sheet(workbook, "About", ["Item", "Value"], [["Pack", pack.title], *[["Note", line] for line in pack.lines()], ["Method", DISCLAIMER]], [14, 120])
    if pack.overflow:
        _sheet(workbook, "Over the cap", ["Signal ID", "Headline", "Source", "Link"],
               [[item["signal_id"], item.get("title"), item.get("publisher"), item.get("url")] for item in pack.overflow], [22, 60, 20, 50])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(path))
    return path


def write_pack(pack: Pack, root: Path | None = None) -> dict[str, Path]:
    folder = (root or settings.OUTPUT_DIR / "review") / pack.batch / pack.stage.lower()
    return {
        "docx": render_docx(pack, folder / f"{pack.stem}.docx"),
        "pptx": render_pptx(pack, folder / f"{pack.stem}.pptx"),
        "xlsx": render_xlsx(pack, folder / f"{pack.stem}.xlsx"),
    }
