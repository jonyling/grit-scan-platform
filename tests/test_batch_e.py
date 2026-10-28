"""Batch E. A gain stays a hypothesis, an exposure cell stays blank without a register fact, and an indicator is uncolored until its bounds are set."""

from __future__ import annotations

import json
from pathlib import Path

from grit.agents.reader import check_scenarios
from grit.library.catalogue import Topic
from grit.library import sots
from grit.ontology.risk_cascade import RiskCascade
from grit.pipeline.indicators import LeadingIndicator, band, score_indicators
from grit.report.docx_brief import scenario_cells
from grit.report.notices import HYPOTHESIS_LABEL


def _case(case: str, gain: str | None) -> dict:
    return {
        "case": case, "name": f"{case} path", "narrative": "Supply could ease for STE.",
        "evidence": ["Prices rose. [S1]"],
        "signposts": [f"{case} signpost one", f"{case} signpost two", f"{case} signpost three"],
        "counter_indicators": [f"{case} counter"],
        "impact": ["Costs could move for Digital Systems & Cyber."],
        "where_ste_could_gain": gain,
        "responses": [{"action": "Consider a review.", "owner": "Group Risk & Assurance", "trigger": 1}],
    }


def test_a_gain_hypothesis_must_stay_conditional() -> None:
    sources = {"S1": {"text": "Prices rose.", "outlet": "Reuters"}}
    anchors, owners = ["Digital Systems & Cyber"], ["Group Risk & Assurance"]
    accepted = {"reasoning": "Working.", "builds_on": [], "cases": [_case(case, "Digital Systems & Cyber could gain a maintenance role.") for case in ("BEST", "MEDIUM", "WORST")]}
    assert check_scenarios(accepted, sources, anchors, owners, []) == []
    rejected = {"reasoning": "Working.", "builds_on": [], "cases": [_case(case, "Digital Systems & Cyber could gain a role and the contract will expand.") for case in ("BEST", "MEDIUM", "WORST")]}
    assert any("CERTAINTY_CLAIM" in item for item in check_scenarios(rejected, sources, anchors, owners, []))
    omitted = {"reasoning": "Working.", "builds_on": [], "cases": [_case(case, None) for case in ("BEST", "MEDIUM", "WORST")]}
    assert check_scenarios(omitted, sources, anchors, owners, []) == []


def test_the_exposure_table_leaves_a_missing_fact_blank(tmp_path: Path) -> None:
    item = Topic("SOTS-X", "Chips", "How chips flow.", (), (), ("Taiwan", "China"), ("chip",), counterparties=("TSMC",))
    (tmp_path / "ba").mkdir()
    registers = {
        "pack.json": {"pack": "ba", "level": "BA", "label": "Test BA", "registers": {key: f"{key}.json" for key in ("source_register", "exposure", "lob_profiles", "themes", "baseline")}},
        "exposure.json": {"entities": [
            {"entityName": "STE Taiwan", "countryOfIncorporation": "Taiwan", "staff": 40},
            {"entityName": "STE China", "countryOfIncorporation": "China"},
            {"entityName": "STE Hong Kong", "countryOfIncorporation": "Hong Kong"},
        ], "suppliers": [{"supplierName": "TSMC"}], "scenarioRatings": {"BEST": "High", "MEDIUM": "Severe"}},
        "themes.json": {"themes": []}, "source_register.json": {"sources": []}, "lob_profiles.json": {"lobs": []}, "baseline.json": {"conditions": []},
    }
    for name, body in registers.items():
        (tmp_path / "ba" / name).write_text(json.dumps(body), encoding="utf-8")
    row = sots.exposure(item, tmp_path)["table"][0]
    assert row["taiwan_entities"]["text"] == "STE Taiwan" and row["taiwan_entities"]["mark"] == "proposed"
    assert row["china_entities"]["text"] == "STE China"
    assert "Hong Kong" not in row["taiwan_entities"]["text"] and "Hong Kong" not in row["china_entities"]["text"]
    assert row["staff"]["text"] == "STE Taiwan: 40"
    assert row["key_suppliers"]["text"] == "TSMC"
    assert row["chip_dependence"]["text"] == "" and row["revenue_concentration"]["text"] == "" and row["asset_concentration"]["text"] == ""
    assert row["ratings"]["BEST"]["text"] == "High" and row["ratings"]["MEDIUM"]["text"] == "" and row["ratings"]["WORST"]["text"] == ""
    assert row["status"] == "proposed"


def test_a_leading_indicator_stays_uncolored_until_the_bounds_are_set() -> None:
    rows = [
        {"signal_id": "S1", "title": "PLA sortie near Taiwan", "text": "An ADIZ incursion was reported."},
        {"signal_id": "S2", "title": "A market note", "text": "No military activity."},
    ]
    found = score_indicators(rows)
    pla = next(item for item in found if item["id"] == "PLA-ACTIVITY")
    assert pla["signals"] == 1 and pla["band"] == "not established" and pla["green_max"] is None
    bounded = LeadingIndicator("PLA-ACTIVITY", "PLA activity", ("ADIZ incursion",), ("STE-GC-05",), green_max=0, amber_max=2)
    assert score_indicators(rows, [bounded])[0]["band"] == "amber"
    assert band(0, 0, 2) == "green" and band(3, 0, 2) == "red" and band(1, None, 2) == "not established"


def test_a_group_risk_lists_the_business_area_risks_under_it() -> None:
    cascade = RiskCascade(
        {"crosswalk": [
            {"baThemeId": "URS-KEY-01", "entityThemeId": "URS-TW-01", "groupRiskCodes": ["G-1"], "lob": "Mobility Rail"},
            {"baThemeId": "DSC-KEY-02", "entityThemeId": "DSC-TW-02", "groupRiskCodes": ["G-1"]},
        ]},
        {"themes": [{"themeId": "URS-TW-01", "themeName": "Taiwan cyber", "lob": "Cyber"}]},
        {"themes": [{"themeId": "URS-KEY-01", "themeName": "Cybersecurity"}, {"themeId": "DSC-KEY-02", "themeName": "Export control"}]},
        {"risks": [{"riskCode": "G-1", "riskName": "Geopolitics", "keywords": []}]},
    )
    row = cascade.links()[0]
    assert row["group_risk"] == "G-1" and row["status"] == "PROPOSED"
    assert {item["id"] for item in row["ba"]} == {"URS-KEY-01", "DSC-KEY-02"}
    assert {item["name"] for item in row["lob"]} == {"Mobility Rail", "Cyber"}


def test_history_names_sources_beyond_the_library_and_the_gain_stays_marked() -> None:
    document = {
        "name": "China-Taiwan tensions", "note": "Draft.", "question": "How does tension reach STE?", "covers": [], "coverage": "THIN",
        "gapSources": ["CSIS China Power"],
        "inBrief": {"status": "WITHHELD"},
        "sections": [{"key": "HISTORY", "title": "History and how we got here", "kind": "NARRATIVE", "status": "WITHHELD", "violations": ["UNCITED"], "draft": None}],
        "gains": [{"shift": "China-Taiwan tensions", "case": "Best case for STE", "text": "STE could gain a maintenance role."}],
        "exposure": {"basis": "Proposed until the business areas confirm it.", "business_areas": [], "table": []},
        "passages": {}, "reports": [],
    }
    text = sots.to_markdown(document)
    assert "CSIS China Power (proposed)" in text
    assert "Hypothesis: to validate with BAs." in text and "STE could gain a maintenance role." in text
    reading = {"name": "Export controls", "lean": "BEST", "seen": {"BEST": 0}, "cases": [{
        "case": "BEST", "title": "Best case for STE", "name": "Open trade", "signposts_seen": [], "signposts_total": 3,
        "counter_indicators_seen": [], "responses_triggered": [], "where_ste_could_gain": "Digital Systems & Cyber could gain a support contract.",
    }]}
    assert scenario_cells(reading)[-1] == [f"Best: {HYPOTHESIS_LABEL} Digital Systems & Cyber could gain a support contract."]
