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
    record = _record(rung="COUNTERPARTY", link={"involves": [{"cls": "Customer", "strong": True}]}, exposure=[{"item_type": "PROJECT", "item_name": "Taichung Blue Line", "strong": True}])
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
    heads = {profile.profile_id: set(profile.packs) for profile in profiles.values() if profile.review_role == "BA_HEAD"}
    assert [profile_id for profile_id, packs in heads.items() if packs & {"urs", "urs-tw"}] == ["gareth"], "one BA head approves URS for AIRIS"
    for name in ("hwaimay", "gareth"):
        assert set(profiles[name].briefs) == {"urs", "urs-tw"}, "BA readers get the business-area and the entity brief"
    assert {"urs", "urs-tw"} <= set(profiles["wilson"].briefs), "the head of R&A reads every URS brief"
    assert set(profiles["ksyong"].briefs) == {"urs-tw"}, "the entity head gets the entity brief only"
    assert profiles["group-cfo"].briefs == {}


def test_a_passing_mention_sets_no_priority() -> None:
    weak = _record(rung=None, exposure=[{"item_type": "PROJECT", "item_name": "Kaohsiung Purple Line", "strong": False}], condition_ids=[])
    route = aperture_routing(weak, aperture="MATERIAL_APERTURE", packs=["urs-tw"])
    assert route.priority is None and not route.include


def test_renumbering_drops_a_mark_with_no_source_in_the_target() -> None:
    from grit.pipeline.reader_scan import _renumber

    combined = {"S1": "sigP", "S2": "sigQ", "S3": "sigR"}
    local = {"S1": "sigQ", "S2": "sigR"}
    assert _renumber("Fact [S3]; see also [S1].", combined, local) == "Fact [S2]; see also ."


def test_spreadsheet_text_never_starts_a_formula() -> None:
    from grit.report.office import cell_text

    assert cell_text('=HYPERLINK("https://evil")') == '\'=HYPERLINK("https://evil")'
    assert cell_text("Plain headline") == "Plain headline" and cell_text(3) == 3


def test_the_printed_window_matches_the_keep_rule() -> None:
    from datetime import date

    from grit.report.brief import window_label

    assert window_label(date(2026, 10, 7), 8) == "29 September – 7 October 2026"


def test_a_demoted_generic_name_falls_down_the_link_ladder() -> None:
    import json as _json

    from grit.ontology.graph import build_graph
    from grit.ontology.link import LinkIndex, link_candidate
    from grit.pipeline.reader_scan import generic_name_rule

    pack = load_ontology_pack(CONFIG_DIR, "urs")
    graph = build_graph(pack, reviewed_aliases=_json.loads((CONFIG_DIR / "registry_aliases.json").read_text(encoding="utf-8-sig")))
    index = LinkIndex(graph, pack.lob_profiles)
    rule = (pack.scan_plan.get("namedItems") or {}).get("genericNames")
    candidate = {"title": "MINISTRY OF HOME AFFAIRS awards hospital robot deal to Diligent Robotics", "text": "The healthcare robotics contract covers autonomous mobile robots."}
    link = generic_name_rule(link_candidate(candidate, index, pack.lob_profiles), f"{candidate['title']}\n{candidate['text']}", rule, graph)
    assert link.rung == "COMPETITOR", link.reasons
    assert not any(item.strong and item.cls == "Customer" for item in link.involves)


def test_a_topic_keyword_counts_in_a_title_case_headline_but_a_name_does_not_run_into_another() -> None:
    from grit.ontology.reader_index import CONDITION, ReaderIndex

    plan = {"conditions": [{"conditionId": "GC-02", "keywords": ["Houthi", "Taiwan Strait"]}]}
    index = ReaderIndex({"suppliers": [{"supplierId": "S1", "supplierName": "Leonardo SpA", "aliases": ["Leonardo"]}]}, plan, {"conditions": [{"id": "GC-02", "name": "Middle East security"}]})
    hits = index.hits("Houthi Attacks in Saudi Arabia as of October 18", "A map of Houthi Attacks.")
    assert [(hit.item_type, hit.item_id, hit.strong) for hit in hits] == [(CONDITION, "GC-02", True)]
    assert any(hit.item_id == "GC-02" for hit in index.hits("Taiwan Strait Blockade Fears Grow", ""))
    assert not any(hit.item_id == "S1" for hit in index.hits("Leonardo DiCaprio Film Premieres", "")), "a name still stops at a longer name"
