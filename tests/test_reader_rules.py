"""Contract tests for the reader rules: routing, memory, RED, the reader index, confidence, and the risk cascade. Pure."""

from __future__ import annotations

import json

from grit.core.confidence import evidence_confidence, trend_label
from grit.core.red_alert import red_check
from grit.core.routing import aperture_routing
from grit.ontology.pack import load_ontology_pack
from grit.ontology.reader_index import ReaderIndex
from grit.ontology.risk_cascade import load_risk_cascade
from grit.profiles import load_profiles
from grit.profiles.memory import apply_memory, build_memory
from grit.settings import CONFIG_DIR

PATTERNS = json.loads((CONFIG_DIR / "red-patterns.json").read_text(encoding="utf-8"))


def _record(**fields):
    base = {"signal_id": "SIG-1", "title": "t", "text": "", "pack": "urs-tw", "rung": None, "link": {"involves": []}, "exposure": [], "condition_ids": [], "publisher": "focustaiwan.tw"}
    return base | fields


def test_priority_takes_the_lowest_number_and_records_every_match() -> None:
    record = _record(rung="COUNTERPARTY", link={"involves": [{"cls": "Customer", "strong": True}]}, exposure=[{"item_type": "PROJECT", "item_name": "Taichung Blue Line"}])
    route = aperture_routing(record, aperture="MATERIAL_APERTURE", packs=["urs-tw"])
    assert route.priority == "P2"
    assert any(line.startswith("P4") for line in route.matched), "the customer match is still recorded"


def test_p1_needs_an_active_request() -> None:
    record = _record(title="Kaohsiung metro tender delayed again")
    proposed = [{"request_id": "MR-1", "status": "PROPOSED", "keywords": ["metro tender"]}]
    assert aperture_routing(record, aperture="WIDE_APERTURE", packs=["urs-tw"], requests=proposed).priority is None
    active = [{"request_id": "MR-1", "status": "ACTIVE", "keywords": ["metro tender"]}]
    route = aperture_routing(record, aperture="WIDE_APERTURE", packs=["urs-tw"], requests=active)
    assert route.priority == "P1" and route.request_id == "MR-1"


def test_material_aperture_suppresses_without_deleting_and_wide_keeps() -> None:
    record = _record(rung="SECTOR")
    assert aperture_routing(record, aperture="WIDE_APERTURE", packs=["urs-tw"]).include
    material = aperture_routing(record, aperture="MATERIAL_APERTURE", packs=["urs-tw"])
    assert not material.include and material.suppression_reason == "NO_EXPOSURE_PATH" and material.priority == "P3"
    assert aperture_routing(_record(rung="SECTOR", condition_ids=["TW-GLOBAL-03"]), aperture="MATERIAL_APERTURE", packs=["urs-tw"]).include


def test_memory_reorders_and_hides_only_within_its_limits() -> None:
    records = {f"SIG-{n}": _record(signal_id=f"SIG-{n}", publisher="noisy.example", themes=[{"theme_id": "URS-KEY-01"}]) for n in range(5)}
    feedback = [{"profile_id": "wilson", "signal_id": f"SIG-{n}", "decision": "REJECT"} for n in range(3)]
    memory = build_memory("wilson", feedback, records)
    effect = apply_memory(records["SIG-4"], memory)
    assert effect.hidden and effect.affinity < 0, "three rejections from one outlet and no approval hide that outlet"
    assert not apply_memory(records["SIG-4"], memory, protected=True).hidden, "a P1 or RED signal is never hidden"
    assert build_memory("hwaimay", feedback, records).size == 0, "one reader's memory never changes another's view"
    later = [*feedback, {"profile_id": "wilson", "signal_id": "SIG-0", "decision": "APPROVE"}]
    assert build_memory("wilson", later, records).decisions["SIG-0"] == "APPROVE", "the latest decision wins"


def test_red_needs_an_event_sentence_and_corroboration() -> None:
    event = {"title": "China begins blockade of Taiwan", "text": "PLA ships closed approaches to Kaohsiung."}
    assert red_check(event, pack="urs-tw", patterns=PATTERNS, corroboration=2, high_rated_source=False).red
    assert not red_check(event, pack="urs-tw", patterns=PATTERNS, corroboration=1, high_rated_source=False).red
    forecast = {"title": "US sees China invasion of Taiwan unlikely before 2028", "text": ""}
    assert not red_check(forecast, pack="urs-tw", patterns=PATTERNS, corroboration=5, high_rated_source=True).red
    sanction = {"title": "US adds firm to Entity List", "text": "", "rung": "COUNTERPARTY"}
    assert not red_check(sanction, pack="urs", patterns=PATTERNS, corroboration=3, high_rated_source=True).red, "the STE sanction rule needs a DIRECT link"


def test_reader_index_name_rules() -> None:
    pack = load_ontology_pack(CONFIG_DIR, "urs-tw")
    index = ReaderIndex(pack.exposure, pack.scan_plan, pack.baseline, [{"watchpointId": "WP", "title": "w", "keywords": {"subject": ["RKC02"], "issue": ["hoisting"]}}])

    def strong(title: str, text: str = "") -> dict[str, bool]:
        return {hit.item_id: hit.strong for hit in index.hits(title, text)}

    assert "SUP-CHANCHUN" not in strong("Minister Chan Chun Sing visits Taipei"), "a longer proper name is a different name"
    assert strong("Leonardo SpA wins Italian navy order").get("SUP-LEONARDO") is False, "global supplier news without a Taiwan or rail cue is a passing mention"
    assert strong("Leonardo radios for Kaohsiung MRT")["SUP-LEONARDO"]
    assert strong("Purple Line opening date slips in Maryland").get("PRJ-PURPLE") is False, "a city-less line name needs its city"
    assert strong("Taichung MRT Blue Line tender opens")["PRJ-TC-BLUE"], "an operator word inside a project name still matches"
    assert "PRJ-YELLOW" not in strong("Stand behind the yellow line in Taipei")
    assert strong("RKC02 works continue in Kaohsiung").get("WP") is False, "a watchpoint update needs its issue word"
    assert strong("RKC02 hoisting restriction lifted in Kaohsiung")["WP"]


def test_confidence_is_a_rule_over_cited_outlets() -> None:
    assert evidence_confidence([]) == (None, "No source is cited.")
    assert evidence_confidence([{"outlet": "x.com", "credibilityRating": "UNRATED"}])[0] == "LOW"
    assert evidence_confidence([{"outlet": "reuters.com", "credibilityRating": "HIGH"}])[0] == "MEDIUM"
    assert evidence_confidence([{"outlet": "reuters.com", "credibilityRating": "HIGH"}, {"outlet": "taipeitimes.com", "credibilityRating": "HIGH"}])[0] == "HIGH"
    assert trend_label("EASING") == "Easing" and trend_label(None) == "Not established"


def test_risk_cascade_orders_entity_before_ba_before_group() -> None:
    hits = load_risk_cascade().hits("China tightens rare earth export controls", "Sanctions raise financing cost.", ["URS-KEY-05"])
    levels = [hit.level for hit in hits]
    assert levels == sorted(levels, key=["ENTITY", "BA", "GROUP"].index)
    assert any(hit.basis == "CROSSWALK" and "PROPOSED" in hit.matched for hit in hits)


def test_every_profile_loads_and_one_ba_head_approves_for_airis() -> None:
    profiles = load_profiles()
    assert {"wilson", "hwaimay", "gareth", "ksyong", "group-cfo"} <= set(profiles)
    assert [profile.profile_id for profile in profiles.values() if profile.review_role == "BA_HEAD"] == ["gareth"]
    assert profiles["wilson"].brief["readerPack"] == "urs-tw"
