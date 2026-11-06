"""Entity Info layouts added on 6 November 2026 for the Marine workbook: plain entity headers, a supplier sheet with no BA
column, counterparty class rows, a keyword-first sheet 6, one classified sheet 7 table, and a competitor sheet by region."""

from __future__ import annotations

from pathlib import Path

import pytest

from grit.ontology.build_pack import _exposure, _themes
from grit.ontology.graph import short_names
from grit.ontology.lob_keyword_loader import ALL_LOBS, parse_lob_keyword_rows
from grit.ontology.registry_loader import load_transmission_registry
from grit.ontology.search_inputs import parse_competitor_rows


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


def _marine_book(path: Path) -> Path:
    return _workbook(path, {
        "2. Entity Address": [
            ["1. Entity Address Register"],
            ["Entity ID", "Entity Name", "LOB Grouping", "LOB", "Country", "Ownership Type", "Registered Address"],
            [500, "ST Engineering Marine Ltd.", "Marine", "SG Marine", "Singapore", "Subsidiary", "7 Benoi Road"],
            [None, "ST Engineering Marine Ltd. - Abu Dhabi Branch", "Marine", "SG Marine", "United Arab Emirates", "Branch", "Musaffah"],
        ],
        "4. Customers": [
            ["3. Disclosed Customers / Prospects"],
            ["Customer / Market", "Engagement / Need", "Validation Status"],
            ["Commercial shipowners and operators", "Repair and conversion", "Customer class"],
            ["Boskalis Group", "Commercial ship repair/conversion", "Named customer (BA entity info, 5 Nov 26)"],
        ],
        "5. Suppliers": [
            ["5. Supplier & Dependency Map"],
            ["Supplier Category", "Dependency / Exposure"],
            ["Marine equipment vendors", "Valves, HVAC, electrical systems"],
            ["KONGSBERG MARITIME AS", "Critical supplier tracked in Sphera"],
        ],
        "6. LOB_Keywords": [
            ["6. GRIT Scan Keywords"],
            ["Keyword", "LOB (per BA file)"],
            ["Fearless class"],
            ["Dry Dock", "Ship Repair / Ship Conversion"],
        ],
    })


def test_plain_entity_headers_read_as_the_template_and_a_title_row_is_skipped(tmp_path: Path) -> None:
    exposure = _exposure(_marine_book(tmp_path / "marine.xlsx"), "MARINE", customer_class=r"\bclass\b", supplier_class=r"\bvendors\b")
    marine, branch = exposure["entities"]
    assert marine["entityName"] == "ST Engineering Marine Ltd." and marine["lob"] == "SG Marine"
    assert marine["countryOfIncorporation"] == "Singapore" and marine["natureOfShareholding"] == "Subsidiary" and marine["sourceEntityId"] == "500"
    assert branch["countryOfIncorporation"] == "United Arab Emirates" and branch["sourceEntityId"] is None


def test_a_supplier_sheet_without_a_ba_column_and_class_rows_are_kept_but_marked(tmp_path: Path) -> None:
    exposure = _exposure(_marine_book(tmp_path / "marine.xlsx"), "MARINE", customer_class=r"\bclass\b", supplier_class=r"\bvendors\b")
    vendors, kongsberg = exposure["suppliers"]
    assert vendors["counterpartyClass"] is True and vendors["businessArea"] is None
    assert kongsberg["dependency"] == "Critical supplier tracked in Sphera" and "counterpartyClass" not in kongsberg
    shipowners, boskalis = exposure["customers"]
    assert shipowners["counterpartyClass"] is True and shipowners["confidence"] == "Customer class"
    assert "counterpartyClass" not in boskalis and boskalis["engagement"] == "Commercial ship repair/conversion"
    assert "2 customer and supplier rows" in exposure["counterpartyClassNote"]


def test_without_class_patterns_no_row_is_a_class(tmp_path: Path) -> None:
    exposure = _exposure(_marine_book(tmp_path / "marine.xlsx"), "MARINE")
    assert not any(row.get("counterpartyClass") for row in [*exposure["customers"], *exposure["suppliers"]])
    assert "counterpartyClassNote" not in exposure


def test_keyword_first_sheet_6_files_a_blank_lob_under_all_lobs() -> None:
    rows = [["6. GRIT Scan Keywords"], ["Keyword", "LOB (per BA file)"], ["Fearless class"], ["Dry Dock", "Ship Repair / Ship Conversion"]]
    first, second = parse_lob_keyword_rows(rows)
    assert (first.lob, first.keyword) == (ALL_LOBS, "Fearless class")
    assert (second.lob, second.keyword) == ("Ship Repair / Ship Conversion", "Dry Dock")


def test_the_harm_registry_reads_the_marine_layout(tmp_path: Path) -> None:
    registry = load_transmission_registry(_marine_book(tmp_path / "marine.xlsx"), None)
    names = {entry.canonical_name for entry in registry.entries}
    assert {"ST Engineering Marine Ltd.", "Boskalis Group", "KONGSBERG MARITIME AS"} <= names
    assert len(registry.lob_keywords) == 2


def test_a_classified_theme_table_reads_tiers_and_keeps_drafts_out(tmp_path: Path) -> None:
    book = _workbook(tmp_path / "marine.xlsx", {"7. Annual Risks": [
        ["7. Draft Risk Themes"],
        ["Risk Theme", "Exposure Rationale", "Proposed Classification"],
        ["Workplace safety", "Heavy lifting", "Key"],
        ["Quality and warranty", "Rework", "Key/Tier 2"],
        ["Major workplace injuries", "Hiring", "Key (BA Annual Risk Refresh)"],
        ["Fraud by intermediaries", "Agents", "Tier 2 (BA Annual Risk Refresh)"],
        ["Climate change adaptation", "Flooding", "Emerging (BA Annual Risk Refresh)"],
        ["Cyber breach", "Human factor", "Key (BA Annual Risk Refresh)"],
    ]})
    register = _themes(book, "MARINE", layout="classified", require="BA Annual Risk Refresh", counts={"KEY": 2, "TIER_2": 1, "EMERGING": 1})
    assert [(theme["themeId"], theme["themeName"]) for theme in register["themes"]] == [
        ("MARINE-KEY-01", "Major workplace injuries"),
        ("MARINE-KEY-02", "Cyber breach"),
        ("MARINE-TIER_2-03", "Fraud by intermediaries"),
        ("MARINE-EMERGING-04", "Climate change adaptation"),
    ]
    assert [draft["themeName"] for draft in register["draftThemes"]] == ["Workplace safety", "Quality and warranty"]
    with pytest.raises(ValueError, match="KEY section has 2 rows; expected 3"):
        _themes(book, "MARINE", layout="classified", require="BA Annual Risk Refresh", counts={"KEY": 3})
    first_named = _themes(book, "MARINE", layout="classified")["themes"]
    assert next(theme for theme in first_named if theme["themeName"] == "Quality and warranty")["tier"] == "KEY", "the first tier named wins"


def test_a_competitor_sheet_by_region_skips_its_header_and_keeps_the_region() -> None:
    rows = [
        ["Region", "Competitors/Peers", "Workscope & Core Capabilities", ""],
        ["Singapore", "Seatrium", "Offshore and marine engineering", ""],
        ["MENA", "Abu Dhabi Shipbuilding", "Naval shipbuilding", ""],
    ]
    competitors = parse_competitor_rows(rows)["competitors"]
    assert [row["competitor"] for row in competitors] == ["Seatrium", "Abu Dhabi Shipbuilding"]
    assert competitors[1]["region"] == "MENA"


def test_short_names_for_branches_and_names_ending_in_of_a_country() -> None:
    assert short_names("ST Engineering Marine Ltd. - Kingdom Saudi Arabia Branch", ["Singapore", "Saudi Arabia"]) == ["ST Engineering Marine"]
    assert short_names("Ministry of Home Affairs of Singapore", ["Singapore"]) == []
    assert short_names("Monetary Authority of Singapore (MAS)", ["Singapore"]) == ["Monetary Authority of Singapore"]
    assert short_names("STSE Engineering Services Pte Ltd", ["Singapore"]) == ["STSE Engineering Services"]
