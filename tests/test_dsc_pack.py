"""The DS&C workbook layout: a numbered competitor sheet, comma-separated LOB keywords, a focus-area management sheet, a
supplier sheet with a stray header in column A, five emerging risks, and URS keyword-sheet copies that are not read."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from grit.ontology.build_pack import build_pack
from grit.ontology.lob_keyword_loader import parse_lob_keyword_rows
from grit.ontology.reader_plan import reader_queries
from grit.ontology.registry_loader import load_transmission_registry
from grit.ontology.risk_cascade import load_risk_cascade
from grit.ontology.search_inputs import parse_competitor_rows, parse_management_area_rows
from grit.ontology.workbook import workbook_path

_ROOT = Path(__file__).resolve().parent.parent
_WORKBOOK = _ROOT / "secrets" / "04_DSC_Entity_Info.xlsx"
needs_workbook = pytest.mark.skipif(not _WORKBOOK.is_file(), reason="the DS&C workbook is confidential and not in the repository")

_MANIFEST = {
    "pack": "dsc", "level": "BA", "label": "Digital Systems & Cyber", "shortLabel": "DS&C", "businessArea": "Digital Systems & Cyber", "children": [],
    "registers": {"source_register": "S.json", "exposure": "E.json", "lob_profiles": "L.json", "themes": "T.json", "baseline": "B.json"},
    "optional": {"keyword_library": "K.json", "competitors": "C.json", "management_focus": "M.json"},
    "build": {
        "prefix": "DSC", "keywordSheets": [], "themeCounts": {"EMERGING": 5}, "managementFocusLayout": "areas", "supplierUnit": "DS&C", "sharedSupplierSeparator": "/",
        "sourceSheets": {"9. Specific Industry News": {"name_col": 1, "heading_col": None, "numbered": True}},
        "themeVocabulary": {"pandemic": ["pandemic", "outbreak"]},
    },
}


def test_a_long_competitor_header_and_numbered_lob_labels_are_read() -> None:
    rows = [
        ["List of top competitors/peers - DS&C 2026", "", "", ""],
        ["", "", "", ""],
        ["Line of Business (LOB)", "Competitor", "Workscope & Core Capabilities", "Note (if any)"],
        ["1. Mission Software & Services", "Lockheed Martin", "Mission systems", ""],
        ["", "Elbit Systems", "C4I software", ""],
        ["2. Space", "Lockheed Martin", "Satellites", ""],
    ]
    parsed = parse_competitor_rows(rows)
    assert [(row["lobLabel"], row["competitor"]) for row in parsed["competitors"]] == [
        ("1. Mission Software & Services", "Lockheed Martin"),
        ("1. Mission Software & Services", "Elbit Systems"),
        ("2. Space", "Lockheed Martin"),
    ], "the header row is not a competitor called 'Competitor'"


def test_comma_separated_lob_keywords_are_split_and_a_single_keyword_is_not() -> None:
    rows = [
        ["Line of Business (LOB)", "Key Words"],
        ["Space", "Space Technology, Advanced connectivity, Big Data, Encryption Technologies"],
        ["Mobility Rail", "Rail signalling"],
        ["", "Depot, workshop"],
    ]
    keywords = [(row.lob, row.keyword) for row in parse_lob_keyword_rows(rows)]
    assert keywords == [
        ("Space", "Space Technology"), ("Space", "Advanced connectivity"), ("Space", "Big Data"), ("Space", "Encryption Technologies"),
        ("Mobility Rail", "Rail signalling"), ("Mobility Rail", "Depot, workshop"),
    ], "one comma is a keyword's own punctuation, not a list"
    split = parse_lob_keyword_rows([["LOB", "Key Words"], ["Networks", "Advanced connectivity, 5G, 3D mapping, 1. Cloud"]])
    assert [row.keyword for row in split] == ["Advanced connectivity", "5G", "3D mapping", "Cloud"], "a digit is stripped only as a list marker"


def test_focus_areas_cover_the_whole_business_area() -> None:
    rows = [
        ["S/N", "Management Focus Area", "Key Watchpoints", "", ""],
        ["1", "Geopolitical, Trade Controls & Export Compliance", "Sanctions, export controls, tariffs.", "", ""],
        ["", "A stray note", "", "", ""],
        ["2", "Space, Satellite & Geospatial Developments", "LEO satellites, SATCOM", "", ""],
    ]
    parsed = parse_management_area_rows(rows)
    assert parsed["requests"] == []
    assert [item["focus"] for item in parsed["focusItems"]] == ["Geopolitical, Trade Controls & Export Compliance", "Space, Satellite & Geospatial Developments"]
    assert parsed["focusItems"][0]["indicators"] == ["Sanctions", "export controls", "tariffs"]
    assert all(item["allLobs"] and item["status"] is None for item in parsed["focusItems"]), "the sheet states no status, so none is recorded"
    assert parsed["dropped"] == [{"row": 3, "text": "A stray note", "reason": "NO_SERIAL_NUMBER"}]
    with pytest.raises(ValueError):
        parse_management_area_rows([["LOB / Scope", "Management Focus", "", "", ""]])


@needs_workbook
def test_dsc_registers_are_built_from_the_workbook_without_invention(tmp_path: Path) -> None:
    build_pack(_WORKBOOK, tmp_path, _MANIFEST)
    read = lambda name: json.loads((tmp_path / name).read_text(encoding="utf-8"))  # noqa: E731
    exposure, themes, focus = read("E.json"), read("T.json"), read("M.json")

    assert len(exposure["entities"]) == 21 and len(exposure["customers"]) == 69 and len(exposure["suppliers"]) == 39
    assert exposure["projects"] == [] and exposure["sites"] == []
    assert sum(bool(row.get("anonymised")) for row in exposure["customers"]) == 8
    mindef = next(row for row in exposure["customers"] if row["customerName"] == "Ministry of Defence (MINDEF)")
    assert mindef["country"] == "Singapore" and mindef["confidence"] == "Verified – public source" and "Unmanned" in mindef["lob"], "the sheet's columns are copied"
    shared = {row["supplierName"]: row for row in exposure["suppliers"] if row.get("sharedWith")}
    assert set(shared) == {"MOTOROLA SOLUTIONS SINGAPORE PTE. LTD.", "FRONTIER INTEGRATED TECHNOLOGY PTE LTD"}
    assert all(row["businessArea"] == "DS&C" and row["sharedWith"] == ["URS"] for row in shared.values())
    assert not any(row["supplierName"].isdigit() for row in exposure["suppliers"]), "the stray header in column A is not a supplier"

    assert [theme["tier"] for theme in themes["themes"]].count("EMERGING") == 5
    assert themes["themes"][0]["themeId"] == "DSC-KEY-01"
    pandemic = next(theme for theme in themes["themes"] if theme["themeName"] == "Pandemic")
    assert pandemic["keywords"] == ["pandemic", "outbreak"], "the manifest's vocabulary is used"

    assert read("K.json")["libraries"] == [], "the hidden URS keyword sheets in the DS&C workbook are not read"
    assert len(focus["focusItems"]) == 16 and focus["requests"] == []
    assert len(read("C.json")["competitors"]) == 26
    assert len(read("L.json")["lobs"]) == 8
    lob_keywords = [keyword for lob in read("L.json")["lobs"] for keyword in lob["keywords"]]
    assert "5G" in lob_keywords and min(len(keyword) for keyword in lob_keywords) >= 2
    names = {row["sourceName"] for row in read("S.json")["sources"] if row["workbookSheet"] == "9. Specific Industry News"}
    assert names == {"Defence Industries Media"}, "a sheet 9 source needs a URL, and its '1. ' numbering is removed"
    assert read("B.json")["conditions"] == []


@needs_workbook
def test_the_dsc_transmission_registry_loads_without_the_urs_aliases() -> None:
    registry = load_transmission_registry(_WORKBOOK, None)
    assert len(registry.entries) == 21 + 69 + 39
    assert registry.aliases == ()


def test_a_urs_workbook_override_never_feeds_another_business_area(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urs, dsc = tmp_path / "urs.xlsx", tmp_path / "dsc.xlsx"
    urs.write_bytes(b"")
    dsc.write_bytes(b"")
    monkeypatch.setenv("GRIT_WORKBOOK", str(urs))
    monkeypatch.delenv("GRIT_WORKBOOK_DSC", raising=False)
    assert workbook_path() == urs
    assert workbook_path("URS.xlsx", prefix="URS") == urs
    import grit.ontology.workbook as workbook_module

    monkeypatch.setattr(workbook_module, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="GRIT_WORKBOOK_DSC"):
        workbook_path("no-such-dsc-workbook.xlsx", prefix="DSC")
    renamed = tmp_path / "secrets" / "04_DSC_Entity_Info.xlsx"
    renamed.parent.mkdir()
    renamed.write_bytes(b"")
    (tmp_path / "secrets" / "02_URS_Entity_Info.xlsx").write_bytes(b"")
    assert workbook_path("no-such-dsc-workbook.xlsx", prefix="DSC") == renamed, "a renamed workbook is found by its business-area tag"
    monkeypatch.setenv("GRIT_WORKBOOK_DSC", str(dsc))
    assert workbook_path("no-such-dsc-workbook.xlsx", prefix="DSC") == dsc


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_risk_cascade_places_a_signal_only_in_its_own_pack_lineage(tmp_path: Path) -> None:
    urs_tw = {"themes": [{"themeId": "URS-TW-01", "themeName": "Taiwan cyber", "tier": "KEY", "keywords": ["ransomware"]}]}
    urs = {"themes": [{"themeId": "URS-KEY-01", "themeName": "Cybersecurity", "tier": "KEY", "keywords": ["ransomware"]}]}
    _write(tmp_path / "urs-tw" / "themes.json", urs_tw)
    _write(tmp_path / "urs" / "themes.json", urs)
    _write(tmp_path / "risk" / "group.json", {"risks": []})
    _write(tmp_path / "risk" / "Risk-Cascade.json", {"levels": [
        {"level": "ENTITY", "weight": 3, "register": "urs-tw/themes.json"},
        {"level": "BA", "weight": 2, "register": "urs/themes.json"},
        {"level": "GROUP", "weight": 1, "register": "risk/group.json"},
    ]})
    dsc_themes = {"themes": [{"themeId": "DSC-KEY-05", "themeName": "Breach of security regulations", "tier": "KEY", "keywords": ["ransomware"]}]}
    dsc = SimpleNamespace(pack="dsc", level="BA", manifest={"parent": None}, children=[], themes=dsc_themes)
    urs_pack = SimpleNamespace(pack="urs", level="BA", manifest={"parent": None}, children=["urs-tw"], themes=urs)

    hits = load_risk_cascade(tmp_path, dsc).hits("Ransomware hits a defence contractor", "", ["DSC-KEY-05"])
    assert [(hit.level, hit.risk_id, hit.name) for hit in hits] == [("BA", "DSC-KEY-05", "Breach of security regulations")]

    hits = load_risk_cascade(tmp_path, urs_pack).hits("Ransomware hits a rail operator", "", ["URS-KEY-01"])
    assert [(hit.level, hit.risk_id) for hit in hits] == [("ENTITY", "URS-TW-01"), ("BA", "URS-KEY-01")], "URS keeps its own entity pack"


def test_an_anonymised_customer_is_not_searched_by_name() -> None:
    pack = SimpleNamespace(
        pack="dsc", competitors=None,
        exposure={"entities": [], "suppliers": [], "customers": [
            {"customerId": "CUSTOMER-a", "customerName": "Changi Airport Group (CAG)"},
            {"customerId": "CUSTOMER-b", "customerName": "Thailand NPE Programme Customer", "anonymised": True},
        ]},
    )
    plan = {"namedItems": {"source": "graph", "lanes": {"CUSTOMER": {"cadenceWeeks": 1}}}}
    targets = [spec.sources[0]["target"] for spec in reader_queries(plan, pack=pack)]
    assert targets == ["CUSTOMER-a"]


def test_an_unknown_pack_stops_the_cli_before_a_run_is_registered() -> None:
    from argparse import Namespace

    from grit.cli import _packs

    with pytest.raises(SystemExit, match="Unknown pack\\(s\\): dls"):
        _packs(Namespace(pack=["dls"]))
    assert _packs(Namespace(pack=["urs"])) == ["urs"]


def test_a_broken_register_file_is_named_in_the_error(tmp_path: Path) -> None:
    from grit.ontology.pack import load_ontology_pack

    _write(tmp_path / "x" / "pack.json", {"pack": "x", "level": "BA", "registers": {}, "optional": {"scan_plan": "X-Scan-Plan.json"}})
    (tmp_path / "x" / "X-Scan-Plan.json").write_text('{"themes": [{"a": 1} {"b": 2}]}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"X-Scan-Plan\.json is not valid JSON: Expecting ',' delimiter at line 1, column \d+"):
        load_ontology_pack(tmp_path, "x")
