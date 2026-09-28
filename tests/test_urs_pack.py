import json
from pathlib import Path

from grit_scan.build_urs_pack import build_urs_pack
from grit_scan.matchers import credibility_lookup, exposure_entity_match
from grit_scan.ontology import load_ontology_pack

_ROOT = Path(__file__).resolve().parent.parent
_PACK = _ROOT / "config" / "urs"


def test_urs_pack_is_rebuilt_from_the_workbook_and_does_not_invent() -> None:
    rebuilt = build_urs_pack(_ROOT / "URS Entity Info.xlsx", Path("/tmp/grit-urs-pack"))
    for key, path in rebuilt.items():
        assert json.loads(path.read_text(encoding="utf-8")) == json.loads((_PACK / path.name).read_text(encoding="utf-8")), key

    pack = load_ontology_pack(_ROOT / "config", "urs")
    assert pack.pack == "urs"
    assert pack.baseline["conditions"] == []
    assert "Taiwan Strait" not in json.dumps(pack.baseline)
    assert pack.baseline["baselineId"] == "URS-LAYER1-ABSENT"
    assert pack.exposure["projects"] == [] and pack.exposure["sites"] == []
    assert all(row["credibilityRating"] == "UNRATED" for row in pack.source_register["sources"])
    assert all(row["sourceDomain"] != "reuters.com" for row in pack.source_register["sources"])
    assert any(row["sourceDomain"] == "bbc.com" for row in pack.source_register["sources"])
    assert len(pack.themes["themes"]) == 14
    assert any(theme["themeName"] == "Competition" for theme in pack.themes["themes"])
    assert any(lob["lobName"] == "Mobility Rail" and lob["priorityCountries"] == [] for lob in pack.lob_profiles["lobs"])

    assert any(row["entityName"] == "STE TransCore Holdings, Inc." and row["lob"] is None for row in pack.exposure["entities"])
    entity = pack.exposure["entities"][0]
    matched = exposure_entity_match(entity["entityName"], pack.exposure)
    assert matched["matchCount"] == 1
    assert matched["matchedEntities"][0]["entityType"] == "ENTITY"
    assert credibility_lookup("https://www.bbc.com/news/story", pack.source_register)["credibilityRating"] == "UNRATED"
    assert credibility_lookup("https://www.reuters.com/world/story", pack.source_register)["matched"] is False
