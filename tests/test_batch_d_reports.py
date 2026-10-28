"""Batch D. The disclaimer is on every report, summary slides follow the item count, and a repeated claim is kept once."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from pptx import Presentation

from grit.report.notices import DISCLAIMER, SUGGESTION_LABEL
from grit.report.office import render_pptx, summary_slide_count
from grit.report.repetition import FirstUse
from grit.report.suggestions import suggestions_for_signal


def test_summary_slide_count_follows_the_critical_items() -> None:
    assert summary_slide_count(0) == 1
    assert summary_slide_count(2) == 1
    assert summary_slide_count(3) == 2
    assert summary_slide_count(5) == 3


def test_first_use_keeps_a_figure_once_and_keeps_the_disclaimer() -> None:
    state = FirstUse()
    first = state.filter("Revenue moved by 12% this quarter across the watched sites.")
    second = state.filter("The same revenue moved by 12% this quarter in the later note.")
    again = state.filter(DISCLAIMER)
    repeated = state.filter(DISCLAIMER)
    assert "12%" in first
    assert second == ""
    assert again == DISCLAIMER and repeated == DISCLAIMER
    suggestion = state.filter(f"{SUGGESTION_LABEL} Review the 12% movement with the owner.")
    assert suggestion.startswith(SUGGESTION_LABEL)


def test_a_suggestion_comes_only_from_an_existing_option_or_response() -> None:
    brief = {
        "conditions": [{"options": [{"source_ids": ["sig-1"], "text": {"runs": [{"text": "Check the vendor notice."}]}}]}],
        "scenarios": {"shifts": [{"cases": [{
            "signposts_seen": [{"number": 1, "signal_ids": ["sig-1"]}],
            "responses_triggered": [{"trigger": 1, "action": "Review the access list", "owner": "CISO"}],
        }]}]},
    }
    lines = suggestions_for_signal(brief, "sig-1")
    assert any("Review the access list" in line and line.startswith(SUGGESTION_LABEL) for line in lines)
    assert any("Check the vendor notice" in line for line in lines)
    assert suggestions_for_signal(brief, "sig-missing") == []
    from grit.report.docx_brief import scenario_cells

    reading = {
        "name": "Access", "lean": "WORST",
        "cases": [{
            "case": "WORST", "title": "Worst case for STE", "name": "Tight access", "signposts_total": 3,
            "signposts_seen": [{"text": "A vendor notice arrived.", "signal_ids": ["sig-1"]}],
            "counter_indicators_seen": [],
            "responses_triggered": [{"action": "Review the access list", "owner": "CISO"}],
        }],
    }
    assert scenario_cells(reading)[4][0].startswith(SUGGESTION_LABEL)


def test_word_and_slides_carry_the_disclaimer_and_a_claim_once(tmp_path: Path) -> None:
    from grit.report.docx_brief import render_docx

    claim = "Staff absence reached 12% across the watched sites this week."
    brief = _brief(claim)
    docx = tmp_path / "brief.docx"
    pptx = tmp_path / "brief.pptx"
    render_docx(brief, docx)
    render_pptx(brief, pptx)
    document = Document(docx)
    pieces = [paragraph.text for paragraph in document.paragraphs]
    pieces += [cell.text for table in document.tables for row in table.rows for cell in row.cells]
    text = "\n".join(pieces)
    assert text.count(claim) == 1
    assert DISCLAIMER in text
    assert any(DISCLAIMER in paragraph.text for section in document.sections for paragraph in section.footer.paragraphs)
    deck = Presentation(pptx)
    slides = ["\n".join(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame) for slide in deck.slides]
    assert len(deck.slides) == 1 + summary_slide_count(5) + 5
    assert all(DISCLAIMER in slide for slide in slides)
    summary = "\n".join(slides[1:4])
    assert "What it means for DS&C" in summary and "\u2026" not in summary


def _brief(claim: str) -> dict:
    rows = []
    for number in range(1, 6):
        rows.append({
            "number": number, "name": f"Theme {number}", "group": "operations", "trend": "Escalating", "tier": "KEY",
            "confidence": "Not established", "full": True, "development": f"{claim} Item {number} adds its own sentence about the week.",
            "implication": {"runs": [{"text": f"Item {number} can affect the business area if it continues."}], "plain": "x"},
            "signal_ids": [], "links": [], "sources": [], "options": [],
        })
    return {
        "meta": {
            "title": "GRIT-Scan: DS&C", "subtitle": "Weekly scan", "grounded_in": ["open sources"],
            "pack_label": "Digital Systems & Cyber", "pack_short": "DS&C", "level": "BA", "standing_label": "risk theme",
            "run_id": "run-d", "window": "1 October 2026", "prepared_for": "Reader", "reader": "Reader",
            "sections": ["WATCHPOINTS"], "show_review_status": True,
        },
        "bottom_line": {"label": "WATCHPOINT", "text": claim, "escalation": "Nothing new this week independently warrants escalation before the next weekly cycle.", "drafted_by": "rule"},
        "watchpoints": [],
        "conditions": rows,
        "method": {"queries": 1, "signals": 5, "kept": 5, "shown_to_reader": 5, "hidden_by_routing": 0, "profile": "A test profile.", "moved_by_profile": 0, "agents": []},
        "content_hash": "a" * 64,
    }
