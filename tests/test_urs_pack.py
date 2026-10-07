import json
from pathlib import Path

from grit.ontology.build_pack import build_urs_pack
from grit.ontology.lob_hierarchy import resolve_lob
from grit.ontology.matchers import credibility_lookup, exposure_entity_match
from grit.ontology.pack import load_ontology_pack
from grit.ontology.workbook import workbook_path

_ROOT = Path(__file__).resolve().parent.parent
_PACK = _ROOT / "config" / "urs"


def test_urs_pack_is_rebuilt_from_the_workbook_and_does_not_invent() -> None:
    rebuilt = build_urs_pack(workbook_path(), Path("/tmp/grit-urs-pack"))
    for key, path in rebuilt.items():
        assert json.loads(path.read_text(encoding="utf-8")) == json.loads((_PACK / path.name).read_text(encoding="utf-8")), key

    pack = load_ontology_pack(_ROOT / "config", "urs")
    assert pack.pack == "urs"
    assert pack.baseline["conditions"] == []
    assert "Taiwan Strait" not in json.dumps(pack.baseline)
    assert pack.baseline["baselineId"] == "URS-LAYER1-ABSENT"
    assert pack.exposure["projects"] == [] and pack.exposure["sites"] == []
    assert all(row["credibilityRating"] == "UNRATED" for row in pack.source_register["sources"])
    assert {row["workbookSheet"] for row in pack.source_register["sources"]} == {"News-Pri", "News-Sec", "9. Specific Industry News"}
    assert len({row["sourceDomain"] for row in pack.source_register["sources"]}) == len(pack.source_register["sources"])
    assert any(row["sourceDomain"] == "bbc.com" for row in pack.source_register["sources"])
    assert any(row["sourceDomain"] == "mindef.gov.sg" and row["group"] for row in pack.source_register["sources"])
    assert pack.source_register["skippedWithoutUrl"], "named rows with no URL are listed, not given an invented domain"
    assert len(pack.themes["themes"]) == 14
    assert any(theme["themeName"] == "Competition" for theme in pack.themes["themes"])
    assert any(lob["lobName"] == "Mobility Rail" and lob["priorityCountries"] == [] for lob in pack.lob_profiles["lobs"])

    assert any(
        row["entityName"] == "STE TransCore Holdings, Inc." and row["lob"] == "Mobility (Rail & Road)" and row["sourceEntityId"] == "517"
        for row in pack.exposure["entities"]
    )
    assert len(pack.exposure["entities"]) == 41
    entity = pack.exposure["entities"][0]
    matched = exposure_entity_match(entity["entityName"], pack.exposure)
    assert matched["matchCount"] == 1
    assert matched["matchedEntities"][0]["entityType"] == "ENTITY"
    assert credibility_lookup("https://www.bbc.com/news/story", pack.source_register)["credibilityRating"] == "UNRATED"
    reuters = credibility_lookup("https://www.reuters.com/world/story", pack.source_register)
    assert reuters["matched"] is True and reuters["credibilityRating"] == "UNRATED"
    assert credibility_lookup("https://example-unlisted-outlet.com/story", pack.source_register)["matched"] is False


def test_lob_hierarchy_fills_every_lob_and_leaves_unresolved_labels_unassigned() -> None:
    pack = load_ontology_pack(_ROOT / "config", "urs")
    hierarchy = pack.lob_hierarchy
    assert hierarchy["status"] == "TRANSCRIBED_FROM_IMAGE_PENDING_BA_VALIDATION"
    assert {lob["lobGrouping"] for lob in hierarchy["lobs"]} == {"Smart Utilities & Infrastructure", "Smart Mobility"}
    # Names and groupings only. No individual is recorded in the file.
    assert all(set(lob) <= {"lobName", "lobGrouping", "aliases", "note"} for lob in hierarchy["lobs"])

    by_name = {lob["lobName"]: lob for lob in pack.lob_profiles["lobs"]}
    assert all(lob["baName"] == "Urban Solutions" and lob["lobGrouping"] and lob["canonicalLob"] for lob in by_name.values())
    assert by_name["Heatlhcare Robotics & Automation"]["canonicalLob"] == "Healthcare Robotics & Automation"
    assert by_name["Mobility Road"]["canonicalLob"] == by_name["Mobility Services"]["canonicalLob"] == "Mobility Road & Services"
    assert pack.lob_profiles["lobHierarchy"]["status"] == hierarchy["status"]

    # A label that spans several LOBs, or names a capability, is never assigned.
    for item in hierarchy["unresolvedLabels"]:
        assert resolve_lob(item["label"], hierarchy) is None, item["label"]
    assert resolve_lob("Mobility (Rail & Road)", hierarchy) is None
    assert resolve_lob("Mobility Services", hierarchy)["lobName"] == "Mobility Road & Services"


def test_lob_hierarchy_agrees_with_the_workbook_entity_sheet() -> None:
    pack = load_ontology_pack(_ROOT / "config", "urs")
    checked = 0
    for entity in pack.exposure["entities"]:
        resolved = resolve_lob(entity["lob"] or "", pack.lob_hierarchy)
        if resolved is not None:
            assert entity["lobGrouping"] == resolved["lobGrouping"], entity["entityName"]
            checked += 1
    assert checked > 0


def test_a_pack_without_a_hierarchy_loads_with_none() -> None:
    assert load_ontology_pack(_ROOT / "config", "urs-tw").lob_hierarchy is None
