from pathlib import Path
from types import SimpleNamespace

from grit_scan.bind import LOB_KEYWORD, REGISTER_ALIAS, REGISTER_EXACT, augment_exposure, bind_candidate
from grit_scan.ontology import load_ontology_pack

_ROOT = Path(__file__).resolve().parent.parent


def _lob(lob_id, name, keywords, canonical=None):
    return {"lobId": lob_id, "lobName": name, "active": True, "keywords": keywords, "canonicalLob": canonical, "priorityCountries": []}


def _pack(lobs=()):
    exposure = {
        "entities": [{"entityId": "ENTITY-st-engineering-taiwan-ltd", "entityName": "ST Engineering (Taiwan) Ltd.", "aliases": ["ST Engineering (Taiwan) Ltd."]}],
        "customers": [{"customerId": "CUSTOMER-siemens", "customerName": "SIEMENS", "aliases": ["SIEMENS"]}],
        "suppliers": [],
        "projects": [],
        "sites": [],
    }
    return SimpleNamespace(pack="urs", exposure=exposure, lob_profiles={"lobs": list(lobs)})


def _candidate(title, text=""):
    return {"title": title, "text": text}


def test_a_register_customer_in_the_text_binds_at_entity_level_with_an_exact_match() -> None:
    result = bind_candidate(_candidate("Siemens wins a rail signalling order"), _pack())
    assert result.bound and result.link_level == "ENTITY" and result.pack == "urs"
    assert result.entity_ids == ("CUSTOMER-siemens",) and result.entity_names == ("SIEMENS",) and result.match_types == (REGISTER_EXACT,)
    assert result.evidence_spans == ("SIEMENS",) and result.unbound_reason is None


def test_a_reviewed_alias_binds_only_through_the_overlay_and_the_pack_is_not_changed() -> None:
    pack = _pack()
    candidate = _candidate("ST Engineering Taiwan opens a depot")
    assert not bind_candidate(candidate, pack).bound
    overlay = augment_exposure(pack, {"aliases": [{"entity_id": "ENTITY-st-engineering-taiwan-ltd", "alias": "ST Engineering Taiwan"}]})
    result = bind_candidate(candidate, pack, exposure=overlay)
    assert result.bound and result.match_types == (REGISTER_ALIAS,) and result.evidence_spans == ("ST Engineering Taiwan",)
    assert pack.exposure["entities"][0]["aliases"] == ["ST Engineering (Taiwan) Ltd."]


def test_exactly_one_lob_keyword_hit_binds_at_lob_level_and_records_the_evidence() -> None:
    pack = _pack([_lob("LOB-a", "Mobility Rail", ["Smart Metro Systems", "Rail Signalling"], "Mobility Rail"), _lob("LOB-b", "TransCore", ["Electronic Tolling"])])
    result = bind_candidate(_candidate("Smart Metro Systems extend a line"), pack)
    assert result.bound and result.link_level == "LOB" and result.canonical_lob == "Mobility Rail"
    assert result.entity_ids == ("LOB-a",) and result.match_types == (LOB_KEYWORD,) and result.evidence_spans == ("Smart Metro Systems",)


def test_two_lobs_matching_is_not_a_link() -> None:
    pack = _pack([_lob("LOB-a", "Mobility Rail", ["Smart Metro Systems"]), _lob("LOB-b", "TransCore", ["Electronic Tolling"])])
    result = bind_candidate(_candidate("Smart Metro Systems and Electronic Tolling", "A joint tender."), pack)
    assert not result.bound and result.unbound_reason == "AMBIGUOUS_LOB" and result.entity_ids == ()


def test_no_register_hit_and_empty_text_are_unbound_with_a_reason() -> None:
    pack = _pack([_lob("LOB-a", "Mobility Rail", ["Smart Metro Systems"])])
    none = bind_candidate(_candidate("Orlando airport debuts upgraded trams"), pack)
    assert not none.bound and none.unbound_reason == "NO_REGISTER_HIT" and none.link_level is None
    assert bind_candidate(_candidate("", ""), pack).unbound_reason == "EMPTY_TEXT"


def test_a_bind_is_deterministic_and_names_its_pack() -> None:
    pack = load_ontology_pack(_ROOT / "config", "urs")
    candidate = _candidate("Smart Metro Systems extend a line")
    first = bind_candidate(candidate, pack)
    assert first == bind_candidate(candidate, pack) and first.pack == "urs" and first.as_record()["entity_ids"] == list(first.entity_ids)
