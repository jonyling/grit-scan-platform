"""Entity Info layouts added on 29 October 2026: CA's sheets, the richer URS and DS&C customer and supplier columns, and
the graph edges they state."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from grit.ontology.build_pack import _customers, _exposure, _themes, build_pack
from grit.ontology.search_inputs import parse_management_focus_rows, parse_management_scenario_rows

_ROOT = Path(__file__).resolve().parent.parent
_CA_WORKBOOK = _ROOT / "secrets" / "01_CA_Entity_Info.xlsx"


def _workbook(path: Path, sheets: dict[str, list[list]]) -> Path:
    import openpyxl

    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    book.save(path)
    return path


def test_customer_columns_are_copied_and_a_short_name_becomes_an_alias(tmp_path: Path) -> None:
    book = _workbook(tmp_path / "urs.xlsx", {"4. Customers": [
        ["#", "Customer", "Short Name (match key)", "HQ Country", "Customer Type", "Sector", "Primary URS LOB", "Likely URS Contracting Entity", "Linked Risk Codes", "Basis / Confidence"],
        [1, "HYUNDAI ROTEM COMPANY", "Hyundai Rotem", "South Korea", "Rolling stock OEM", "Rail", "Mobility Rail", "ST Engineering Urban Solutions Ltd.", "O1.3; F3.1", "Research draft"],
        [2, "LAND TRANSPORT AUTHORITY", "Land Transport Authority", "Singapore", "Statutory board", "Land transport", "Mobility Rail; Mobility Road", "N/A", "", ""],
        ["", "A note under the table", "", "", "", "", "", "", "", ""],
    ]})
    rotem, lta = _customers(book)
    assert rotem["aliases"] == ["HYUNDAI ROTEM COMPANY", "Hyundai Rotem"] and rotem["country"] == "South Korea"
    assert rotem["lob"] == "Mobility Rail" and rotem["contractingEntity"] == "ST Engineering Urban Solutions Ltd." and rotem["linkedRiskCodes"] == ["O1.3", "F3.1"]
    assert lta["aliases"] == ["LAND TRANSPORT AUTHORITY"], "a short name that only changes case adds no alias"
    assert "contractingEntity" not in lta and "confidence" not in lta, "N/A and blank cells stay absent"


def test_a_harmonised_customer_list_without_serials_keeps_every_named_row_once(tmp_path: Path) -> None:
    book = _workbook(tmp_path / "ca.xlsx", {"4. Customers": [
        ["*last updated on 23 Oct"],
        ["Harmonised Customer Name", "Country", "Customer group", "Entity Name"],
        ["Wamos Air, S.A.", "Spain", "Abra Group Limited", "ST Engineering Aerospace Solutions"],
        ["Wamos Air, S.A.", "Spain", "Abra Group Limited", "ST Engineering Aerospace Engines"],
        ["AAR Aircaft Turbine Center", "USA", "AAR Corp", "ST Engineering Aerospace Engines"],
    ]})
    rows = _customers(book)
    assert [row["customerName"] for row in rows] == ["Wamos Air, S.A.", "AAR Aircaft Turbine Center"]
    assert rows[0]["customerGroup"] == "Abra Group Limited" and rows[0]["contractingEntity"] == "ST Engineering Aerospace Solutions"


def test_an_entity_sheet_with_a_note_line_and_an_asset_listing(tmp_path: Path) -> None:
    book = _workbook(tmp_path / "ca.xlsx", {
        "2a. Entity and Asset Address": [
            ["*last updated on 16 Oct by cosec"],
            ["BusinessArea", "LOBGrouping1", "LOB1", "NameOfEntity", "CountryOfIncorporation"],
            ["Commercial Aerospace", "Aerospace MRO", "Engine Services", "ST Engineering Aerospace Engines Pte. Ltd."],
        ],
        "2. Asset Listing (Finance)": [
            ["DBA/GBA", "LOB Grp", "Entity Name", "Occupancy Type", "Country"],
            ["Commercial Aerospace", "Aerospace MRO", "ST Engineering Aerospace Engines Pte Ltd", "Hangar", "Singapore"],
            ["Commercial Aerospace", "Aerospace MRO", "ST Engineering Aerospace Engines Pte Ltd", "Workshop", "China"],
            ["Commercial Aerospace", "Cabin Interiors", "ST Engineering AirX Pte Ltd", "Office", "Singapore"],
        ],
        "4. Customers": [["Harmonised Customer Name", "Country"], ["Wamos Air, S.A.", "Spain"]],
        "5. Suppliers": [["Name", "BA", "IBU", "City", "Country"], ["CFM INTERNATIONAL", "Aero", "N/A", "Cincinnati", "United States"]],
        "6. LOB_Keywords": [["LOB", "Keywords (Key/Tier 2)"], ["Engines MRO", "Engine overhaul"]],
    })
    exposure = _exposure(book, "CA", entity_sheet="2a. Entity and Asset Address", asset_sheet="2. Asset Listing (Finance)")
    engines, airx = exposure["entities"]
    assert engines["assetCountries"] == ["Singapore", "China"] and engines["countryOfIncorporation"] is None, "an asset country is not a country of incorporation"
    assert airx["entityName"] == "ST Engineering AirX Pte Ltd" and airx["source"] == "2. Asset Listing (Finance)"
    assert "1 entities are named only" in exposure["assetNote"]
    assert exposure["suppliers"][0]["country"] == "United States" and "ibu" not in exposure["suppliers"][0]


def test_theme_sections_headed_only_ref_and_risk_are_found_by_their_headings(tmp_path: Path) -> None:
    rows = [["CA Annual Risks"], ["1. Key Risks"], ["Ref", "Risk", "Key Drivers / Issues", "Key Controls", "Action Plans"]]
    rows += [[str(n), f"Key risk {n}", "driver", "control", "plan"] for n in (1, 2)]
    rows += [[], ["2. Tier-2 Risks"], ["Ref", "Risk", "Key Drivers / Issues", "Key Controls", "Action Plans"], ["3", "Tier two risk", "d", "c", "p"]]
    rows += [[], ["3. Emerging Risks"], ["Ref", "Risk", "Key Drivers / Issues", "Key Controls", "Action Plans"], ["4", "Emerging risk", "d", "c", "p"]]
    book = _workbook(tmp_path / "ca.xlsx", {"7. Annual Risks": rows})
    themes = _themes(book, "CA", counts={"KEY": 2, "TIER_2": 1, "EMERGING": 1}, action_plans=True)["themes"]
    assert [theme["themeId"] for theme in themes] == ["CA-KEY-01", "CA-KEY-02", "CA-TIER_2-03", "CA-EMERGING-04"]
    assert themes[0]["sheetActionPlans"] == "plan"


def test_management_scenarios_keep_their_origin_and_watchpoints() -> None:
    rows = [
        ["Scenario Planning from Management", "", "", "", ""],
        ["No.", "Scenario", "Scenario Description", "Impact", "Key Watchpoints (Non-exhaustive)"],
        ["1", "Major aviation incident", "A part fails in service", "Safety impact", "Accidents; regulator safety directives"],
        ["Others from the R&A team:", "", "", "", ""],
        ["No.", "Scenario", "What Could Happen", "Relevant CA Risks", "Key Watchpoints (Non-exhaustive)"],
        ["1", "Geopolitical fragmentation", "Sanctions disrupt supply", "Geopolitics; OEM dependence", "Export controls"],
    ]
    first, second = parse_management_scenario_rows(rows)["focusItems"]
    assert first["origin"] == "Scenario Planning from Management" and first["impact"] == "Safety impact"
    assert first["indicators"] == ["Accidents", "regulator safety directives"]
    assert second["origin"] == "Others from the R&A team" and second["relevantRisks"] == "Geopolitics; OEM dependence" and "impact" not in second
    with pytest.raises(ValueError):
        parse_management_scenario_rows([["LOB / Scope", "Management Focus", "", "", ""]])


def test_a_numbered_topic_heading_continues_the_request_and_named_countries_are_kept() -> None:
    rows = [
        ["10", "LOB", "", "Keywords", ""],
        ["4", "Mobility Rail", "", "Analyse the Rail sectors in Thailand, Malaysia and Australia. Identify:", ""],
        ["5", "Mobility Road", "", "Opportunities", ""],
        ["", "", "", "PPPs and upcoming tenders", ""],
        ["LOB / Scope", "Management Focus", "", "", ""],
    ]
    (request,) = parse_management_focus_rows(rows)["requests"]
    assert request["number"] == 4 and request["scope"] == "Mobility Rail; Mobility Road"
    assert request["countries"] == ["Thailand", "Malaysia", "Australia"]
    assert request["topics"] == [{"name": "Opportunities", "items": ["PPPs and upcoming tenders"]}]
    assert any("continues request 4" in note for note in request["notes"])


def test_every_urs_customer_is_linked_to_a_lob_or_its_contracting_entity() -> None:
    from grit.ontology.graph import build_graph
    from grit.ontology.pack import load_ontology_pack

    graph = build_graph(load_ontology_pack(_ROOT / "config", "urs"))
    customers = graph.of_class("Customer")
    linked = {edge.src for edge in graph.edges if edge.rel == "buys_from" and graph.nodes[edge.dst].kind != "BA"}
    assert customers and all(customer.id in linked for customer in customers)
    assert not any(gap["kind"] == "CUSTOMER_ENTITY_UNKNOWN" for gap in graph.gaps)
    assert not any(edge.rel == "located_in" and graph.nodes[edge.src].cls in {"Customer", "Supplier"} for edge in graph.edges), \
        "a counterparty's country is a field, not STE footprint"


def test_sots_marks_an_entity_that_only_holds_an_asset_in_the_country() -> None:
    from grit.library.sots import _exposure_row

    pack = SimpleNamespace(label="CA", exposure={"entities": [
        {"entityName": "Xiamen Co", "countryOfIncorporation": "China"},
        {"entityName": "Systems Pte Ltd", "countryOfIncorporation": "Singapore", "assetCountries": ["Singapore", "Taiwan"]},
    ], "suppliers": []})
    row = _exposure_row("ca", pack, SimpleNamespace(counterparties=(), countries=("Taiwan", "China")))
    assert row["china_entities"]["text"] == "Xiamen Co" and row["taiwan_entities"]["text"] == "Systems Pte Ltd (asset there)"


@pytest.mark.skipif(not _CA_WORKBOOK.is_file(), reason="the CA workbook is confidential and not in the repository")
def test_the_ca_pack_builds_from_its_workbook(tmp_path: Path) -> None:
    from grit.ontology.pack import manifests

    manifest = manifests(_ROOT / "config")["ca"]
    build_pack(_CA_WORKBOOK, tmp_path, manifest, lob_hierarchy=_ROOT / "config" / "ca" / "CA-LOB-Hierarchy.json")
    read = lambda key: json.loads((tmp_path / {**manifest["registers"], **manifest["optional"]}[key]).read_text(encoding="utf-8"))  # noqa: E731
    exposure = read("exposure")
    assert len(exposure["entities"]) == 57 and len(exposure["customers"]) == 458 and len(exposure["suppliers"]) == 45
    assert sum(bool(row.get("assetCountries")) for row in exposure["entities"]) == 23
    themes = read("themes")["themes"]
    assert [theme["tier"] for theme in themes].count("KEY") == 8 and all(len(theme["keywords"]) >= 2 for theme in themes)
    assert len(read("management_focus")["focusItems"]) == 5
    assert all(row["canonicalLob"] for row in read("competitors")["competitors"])
