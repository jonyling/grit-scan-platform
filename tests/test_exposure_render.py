from __future__ import annotations

from types import SimpleNamespace

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation

from grit.core.overrides import OVERRIDE_STAFF_THREAT
from grit.report.brief import exposure_ask_block, exposure_ask_record
from grit.report.docx_brief import render_docx
from grit.report.markdown import render_markdown
from grit.report.office import render_pptx, render_xlsx


def _profile() -> SimpleNamespace:
    return SimpleNamespace(
        profile_id="wilson",
        name="Wilson",
        role="Group Head, Risk & Assurance",
        label="Wilson (Group Head, Risk & Assurance)",
        review_role="REVIEWER",
        packs=("urs",),
        can_own_principal_ask=lambda pack: pack == "urs",
    )


def _run() -> dict:
    return {
        "pack": "urs",
        "run_date": "2026-11-23",
        "config": {
            "exposure_snapshot": {
                "pack": "urs",
                "asOf": "2026-09-30",
                "sources": ["Internal staff log"],
                "sites": [{
                    "country": "Saudi Arabia",
                    "entity": "ST Engineering Saudi Limited",
                    "headcount": 12,
                    "city": "Riyadh",
                    "safetyConfirmed": "2026-09-29",
                }],
            },
            "principal_ask": {
                "text": "Confirm the Riyadh staff exposure with the site lead before the next cycle.",
                "owner_profile_id": "wilson",
                "due_date": "2026-11-25",
                "red_triggers": ["STAFF_HARMED"],
            },
        },
    }


def _routed(count: int = 12) -> list[dict]:
    return [
        {
            "signal_id": f"SIG-{number:016x}",
            "title": f"Physical threat {number}",
            "publisher": "Test outlet",
            "published_at": "2026-11-23",
            "url": f"https://example.test/{number}",
            "shown": True,
            "decision": "KEEP",
            "tier": 3,
            "priority": {
                "band": "Escalating",
                "override_rule_id": OVERRIDE_STAFF_THREAT,
                "override_reason": "Physical threat in an exposed jurisdiction.",
            },
        }
        for number in range(count)
    ]


def _brief() -> dict:
    record = exposure_ask_record(exposure_ask_block(_run(), _routed(), profiles={"wilson": _profile()}))
    sections = {key: [] for key in ("MANAGEMENT_FOCUS", "REGISTER_RISK", "OTHER_THEME", "TREND")}
    sections["MANAGEMENT_FOCUS"] = [{
        "band": "▲",
        "item": "Test item",
        "risk": "MF: Test",
        "summary": {"runs": [{"text": "A test development."}], "plain": "A test development."},
        "impact": {"runs": [{"text": "It could affect URS."}], "plain": "It could affect URS."},
        "opportunity": None,
        "response": {"action": "Consider checking exposure.", "owner": "Business area head"},
        "sources": [],
    }]
    return {
        "meta": {
            "layout": "BA",
            "title": "GRIT-Scan: Urban Solutions",
            "subtitle": "Business Area Weekly Risk Scan",
            "window": "17–23 November 2026",
            "prepared_for": "Group Head, Risk & Assurance",
            "reader": "Wilson",
            "run_id": "RUN-TEST",
            "run_date": "2026-11-23",
            "pack": "urs",
            "pack_label": "Urban Solutions",
            "pack_short": "URS",
            "grounded_in": ["URS Exposure Ontology"],
            "show_review_status": True,
            "sections": [],
        },
        "bottom_line": {"label": "ELEVATED", "text": "A physical threat requires attention."},
        "v2": {
            "short": "URS",
            "status": "ELEVATED",
            "sections": sections,
            "look_ahead": [],
            "jurisdictions": [],
            "watch": [],
            "tiers": {},
            "exposure_ask": record,
        },
        "scenarios": None,
        "red": [],
        "membership": [item["signal_id"] for item in record["tier1_physical_threats"]],
        "content_hash": "a" * 64,
    }


def test_exposure_ask_model_uses_typed_contracts_and_has_no_event_cap() -> None:
    routed = _routed()
    routed[-1]["shown"] = False
    dropped = _routed(1)[0]
    dropped["signal_id"] = "SIG-ffffffffffffffff"
    dropped["editorial"] = {"decision": "EXCLUDE"}
    routed.append(dropped)
    block = exposure_ask_block(_run(), routed, profiles={"wilson": _profile()})
    record = exposure_ask_record(block)

    assert block.exposures[0].staff_count == 12
    assert block.principal_ask.owner_profile_id == "wilson"
    assert record["principal_ask"]["owner_label"] == "Wilson (Group Head, Risk & Assurance)"
    assert record["principal_ask"]["due_date"] == "2026-11-25"
    assert record["red_triggers"] == ["STAFF_HARMED"]
    assert "STALE EXPOSURE DATA" in record["stale_warning"]
    assert len(record["tier1_physical_threats"]) == 12
    assert record["tier1_physical_threats"][-1]["signal_id"].endswith("000b")
    assert "SIG-ffffffffffffffff" not in {
        item["signal_id"] for item in record["tier1_physical_threats"]
    }
    assert all(item["tier"] == 1 for item in record["tier1_physical_threats"])


def test_exposure_ask_renders_after_summary_in_every_business_area_surface(tmp_path) -> None:
    brief = _brief()

    markdown = render_markdown(brief)
    assert markdown.index("SUMMARY.") < markdown.index("Exposure & Ask") < markdown.index("1. Management Focus Topics")
    assert all(f"Physical threat {number}" in markdown for number in range(12))
    assert "2026-11-25" in markdown

    docx_path = render_docx(brief, tmp_path / "brief.docx")
    document = Document(docx_path)
    word_text = "".join(document.element.body.itertext())
    assert word_text.index("SUMMARY") < word_text.index("Exposure & Ask") < word_text.index("1. Management Focus Topics")
    assert "Physical threat 11" in word_text

    pptx_path = render_pptx(brief, tmp_path / "brief.pptx")
    deck = Presentation(pptx_path)
    slide_text = [
        "\n".join(shape.text for shape in slide.shapes if getattr(shape, "has_text_frame", False))
        for slide in deck.slides
    ]
    exposure_page = next(number for number, text in enumerate(slide_text) if "EXPOSURE & ASK" in text)
    first_section = next(number for number, text in enumerate(slide_text) if "MANAGEMENT FOCUS TOPICS" in text)
    assert exposure_page > 1 and exposure_page < first_section
    assert "Physical threat 11" in "\n".join(slide_text)

    xlsx_path = render_xlsx(brief, [], tmp_path / "brief.xlsx")
    workbook = load_workbook(xlsx_path, data_only=True)
    assert workbook.sheetnames[0] == "Exposure & Ask"
    exposure = workbook["Exposure & Ask"]
    values = [str(cell.value or "") for row in exposure.iter_rows() for cell in row]
    assert "Country" in values and "Wilson (Group Head, Risk & Assurance)" in values
    assert "Physical threat 11" in values
