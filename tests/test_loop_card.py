import copy
import json
import math
from types import SimpleNamespace

from grit.ontology.link import LinkIndex, link_candidate
from grit.ontology.graph import build_graph
from grit.agents.loop.card import HIGH, LOW, MEDIUM, NOT_ESTABLISHED, check, confidence, default_follow_up, finalize, valid_follow_up
from grit.agents.loop.context import NAMED, PLACE_AND_LOB, add_outlet_evidence, build_context

_FILLER = "The programme covers design, delivery and operations across several phases and agencies. " * 6


def _unit(degrees):
    return [math.cos(math.radians(degrees)), math.sin(math.radians(degrees))]


def _pack():
    entities = [
        {"entityId": "ENTITY-a", "entityName": "ST Engineering Urban Solutions Ltd", "aliases": [], "countryOfIncorporation": "Singapore", "lob": "Mobility Rail", "lobGrouping": "Smart Mobility"},
        {"entityId": "ENTITY-b", "entityName": "Amtech Systems (Hong Kong) Limited", "aliases": [], "countryOfIncorporation": "Hong Kong", "lob": "Mobility (Rail & Road)", "lobGrouping": "Smart Mobility"},
    ]
    lobs = [{"lobName": "Mobility Rail", "lobGrouping": "Smart Mobility", "aliases": []}]
    return SimpleNamespace(
        exposure={
            "entities": entities,
            "customers": [{"customerId": "CUSTOMER-lta", "customerName": "LAND TRANSPORT AUTHORITY", "aliases": []}],
            "suppliers": [{"supplierId": "SUPPLIER-byd", "supplierName": "BYD (SINGAPORE) PTE LTD", "businessArea": "URS Singapore", "aliases": []}],
        },
        lob_hierarchy={"businessArea": "Urban Solutions", "lobs": lobs},
        lob_profiles={"lobs": [{"lobId": "LOB-mobility-rail", "lobName": "Mobility Rail", "canonicalLob": "Mobility Rail", "active": True, "keywords": ["platform screen doors"]}]},
        competitors={"competitors": []},
        management_focus={"requests": [], "focusItems": []},
        themes={"themes": [{"themeId": "URS-KEY-04", "themeName": "Cybersecurity risks", "tier": "KEY", "keywords": ["ransomware"], "sheetDetail": "Data exfiltration and major IT outage."}]},
    )


def _world():
    pack = _pack()
    graph = build_graph(pack)
    return pack, graph, LinkIndex(graph, pack.lob_profiles)


def _signal(pack, index, title, text="", publisher="a.com", priority=None):
    link = link_candidate({"title": title, "text": text}, index, pack.lob_profiles)
    return {
        "url": "https://a.com/1", "title": title, "text": text, "publisher": publisher, "published_at": "2026-10-05T08:00:00Z", "doc_type": "NEWS",
        "rung": link.rung, "link": link.as_record(), "priority": priority or {"tier": 2, "big": False, "reasons": []}, "also_reported_by": [],
    }


def _context(title, text=_FILLER, *, pool=(), vectors=None, publisher="a.com"):
    pack, graph, index = _world()
    signal = _signal(pack, index, title, text, publisher)
    return build_context("F01", signal, graph=graph, index=index, themes=pack.themes["themes"], pool=pool, vectors=vectors or {"https://a.com/1": _unit(0)}, same_event=0.9), index


def _page(url, publisher, title, text="", date="2026-10-05T09:00:00Z"):
    return {"url": url, "publisher": publisher, "title": title, "text": text, "published_at": date}


def _good(context):
    return {
        "same_party": {"verdict": "YES", "why": "The text names the register's party in a Singapore rail context.", "quote": ""},
        "what_happened": "The authority awarded a rail signalling contract.",
        "event_quotes": [context.signal["title"]],
        "affected": {"entity_ids": [], "why": "The register links this customer to the business area only."},
        "connection": {"path_ids": ["P1"] if context.paths else [], "why": "The customer is listed in the register."},
        "evidence": [],
        "hypothesis": {"text": "If the award changes demand, the business area could see more signalling work.", "theme_ids": ["URS-KEY-04"], "path_ids": ["P1"] if context.paths else []},
        "follow_up_query": None,
    }


def test_a_customer_signal_reaches_the_business_area_only_and_the_context_says_what_is_missing() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    assert context.scope == "BUSINESS_AREA" and context.entities == {} and context.entities_total == 0
    assert [path["text"] for path in context.paths.values()] == ["LAND TRANSPORT AUTHORITY —buys_from→ Urban Solutions"]
    assert any("customer buys from" in gap for gap in context.gaps)
    fact = context.evidence["R1"]
    assert fact["kind"] == "REGISTER_FACT" and fact["independent"] and "4. Customers" in fact["text"]
    assert "url" not in json.dumps(context.view).lower().replace("independent_of_the_signal", "")


def test_a_named_entity_is_selectable_and_a_place_and_lob_story_offers_the_entity_in_that_place() -> None:
    named, _ = _context("ST Engineering Urban Solutions Ltd wins an order")
    assert named.scope == "ENTITY" and named.entities["ENTITY-a"]["why"] == NAMED
    perimeter, _ = _context("Singapore tests platform screen doors")
    assert perimeter.entities["ENTITY-a"]["why"] == PLACE_AND_LOB
    path_only = perimeter.entities.get("ENTITY-b")
    assert path_only is None or path_only["why"] == "PATH_ONLY"


def test_another_outlet_that_carries_the_same_event_is_independent_and_the_filters_apply() -> None:
    title = "LAND TRANSPORT AUTHORITY awards a signalling contract"
    pool = [
        _page("https://b.com/1", "b.com", "Signalling contract awarded", "LAND TRANSPORT AUTHORITY named the winner."),
        _page("https://c.com/1", "c.com", "Different story", "Nothing related."),
        _page("https://a.com/2", "a.com", "Same outlet", "Same publisher as the signal."),
        _page("https://d.com/1", "d.com", "Market report", "Rail Market Size, Share and CAGR to 2033. Request a free sample."),
        _page("https://e.com/1", "e.com", "Old coverage", "LAND TRANSPORT AUTHORITY", date="2026-09-01T09:00:00Z"),
    ]
    vectors = {"https://a.com/1": _unit(0), "https://b.com/1": _unit(10), "https://c.com/1": _unit(80), "https://a.com/2": _unit(2), "https://d.com/1": _unit(1), "https://e.com/1": _unit(3)}
    context, _ = _context(title, pool=pool, vectors=vectors)
    outlets = [item for item in context.evidence.values() if item["kind"] == "OUTLET"]
    assert [(item["publisher"], item["reason"], item["names_node"]) for item in outlets] == [("b.com", "SAME_EVENT", ["CUSTOMER-lta"])]
    assert context.independent_outlets() == 1 and context.connection_support() == 1
    assert context.view["evidence_summary"] == {"independent_outlets": 1, "outlets_naming_the_same_node": 1, "register_facts": 1}


def test_a_page_that_only_names_the_same_party_is_another_story_and_is_not_evidence() -> None:
    pool = [_page("https://b.com/1", "b.com", "LAND TRANSPORT AUTHORITY budget review", "A separate piece about funding.")]
    vectors = {"https://a.com/1": _unit(0), "https://b.com/1": _unit(70)}
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract", pool=pool, vectors=vectors)
    assert [item for item in context.evidence.values() if item["kind"] == "OUTLET"] == [] and context.independent_outlets() == 0


def test_a_complete_card_passes_every_check() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    assert check(_good(context), context) == []


def test_each_ontology_check_names_its_violation() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    base = _good(context)

    def violations(**changes):
        card = copy.deepcopy(base)
        for path, value in changes.items():
            target = card
            *parents, leaf = path.split("__")
            for parent in parents:
                target = target[parent]
            target[leaf] = value
        return " | ".join(check(card, context))

    assert "QUOTE_NOT_VERBATIM: event_quotes" in violations(event_quotes=["a sentence that is not in the text"])
    assert "NO_EVENT_QUOTE" in violations(event_quotes=[])
    assert "UNGROUNDED_NUMBER: what_happened: 40" in violations(what_happened="The authority awarded a 40 station contract.")
    assert "UNGROUNDED_NAME: what_happened: ST Engineering" in violations(what_happened="ST Engineering won the contract.")
    assert "BANNED_TERM: what_happened: likelihood" in violations(what_happened="The likelihood of an award rose.")
    assert "UNKNOWN_ENTITY: affected: ENTITY-zzz" in violations(affected__entity_ids=["ENTITY-zzz"])
    assert "UNKNOWN_PATH: connection: P9" in violations(connection__path_ids=["P9"])
    assert "NO_PATH_CITED" in violations(connection__path_ids=[])
    assert "UNKNOWN_EVIDENCE: evidence: E9" in violations(evidence=[{"evidence_id": "E9", "quote": "x", "supports": "EVENT"}])
    assert "NOT_CONDITIONAL" in violations(hypothesis__text="The business area has more signalling work.")
    assert "CERTAINTY_CLAIM: hypothesis: will" in violations(hypothesis__text="The business area will gain more signalling work if rates hold.")
    assert "BANNED_TERM: hypothesis: probably" in violations(hypothesis__text="It could probably add work.")
    assert "UNKNOWN_THEME: hypothesis: URS-KEY-99" in violations(hypothesis__theme_ids=["URS-KEY-99"])
    assert "HYPOTHESIS_WITHOUT_PATH: hypothesis: cite the path" in violations(hypothesis__path_ids=[])
    assert "URL_IN_TEXT: connection" in violations(connection__why="See https://example.com for the link.")
    assert "BAD_SHAPE" in " ".join(check({"what_happened": 3}, context))
    assert check("not a card", context) == ["BAD_SHAPE: shape: the card is not an object"]


def test_an_entity_chosen_by_path_only_is_refused_and_an_evidence_quote_must_be_verbatim() -> None:
    context, _ = _context("Singapore tests platform screen doors")
    context.entities["ENTITY-b"] = {"entity_id": "ENTITY-b", "name": "Amtech", "country": "Hong Kong", "lob_label": None, "why": "PATH_ONLY", "path_id": "P1"}
    card = _good(context)
    card["affected"]["entity_ids"] = ["ENTITY-b"]
    assert any(item.startswith("ENTITY_NOT_SUPPORTED") for item in check(card, context))
    card["affected"]["entity_ids"] = ["ENTITY-a"]
    assert not any("affected" in item for item in check(card, context))
    context.evidence["E1"] = {"kind": "OUTLET", "publisher": "b.com", "group": "b.com", "text": "The line opens in March.", "names_node": [], "independent": True, "url": "https://b.com/1", "published_at": None, "cosine": 0.95, "reason": "SAME_EVENT"}
    card["evidence"] = [{"evidence_id": "E1", "quote": "The line opens in June.", "supports": "EVENT"}]
    assert any(item.startswith("QUOTE_NOT_VERBATIM: evidence: E1") for item in check(card, context))
    card["evidence"] = [{"evidence_id": "E1", "quote": "the line  opens in march.", "supports": "EVENT"}]
    assert not any("evidence" in item for item in check(card, context))
    card["evidence"] = []
    assert any(item.startswith("NO_EVIDENCE_CITED") for item in check(card, context))


def test_confidence_follows_the_rule_table_from_logged_facts() -> None:
    pool = [_page("https://b.com/1", "b.com", "Signalling contract awarded", "LAND TRANSPORT AUTHORITY named the winner.")]
    vectors = {"https://a.com/1": _unit(0), "https://b.com/1": _unit(10)}
    corroborated, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract", pool=pool, vectors=vectors)
    result = confidence(corroborated)
    assert result["level"] == HIGH and result["inputs"]["independent_outlets"] == 1 and result["inputs"]["best_path_status"] == 3
    alone, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    assert confidence(alone)["level"] == MEDIUM  # named firmly and stated in the workbook, but no other outlet
    generated, _ = _context("BYD plans a plant")
    assert generated.signal["link"]["involves"][0]["alias_basis"] == "GENERATED" and confidence(generated)["level"] == LOW
    sector, _ = _context("Platform screen doors tested at stations")
    assert confidence(sector)["level"] == LOW
    theme_only, _ = _context("Ransomware halts a rail operator")
    assert theme_only.paths == {} and confidence(theme_only)["level"] == NOT_ESTABLISHED
    assert confidence(theme_only)["reasons"][0].startswith("No path from this signal")


def test_finalize_withholds_a_failing_part_and_keeps_the_rest() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    card = _good(context)
    card["hypothesis"]["text"] = "The business area will gain more work."
    violations = check(card, context)
    final = finalize(card, context, violations)
    assert final["status"] == "PARTIAL" and final["withheld"] == ["hypothesis"] and final["q5"]["hypothesis"] is None
    assert final["q1"]["what_happened"] and final["q3"]["paths"][0]["text"].startswith("LAND TRANSPORT AUTHORITY") and final["q3"]["paths"][0]["cited"]
    assert final["q6"]["level"] == MEDIUM
    json.dumps(final)


def test_a_card_whose_what_happened_fails_is_withheld_whole_but_still_carries_the_confidence() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    card = _good(context)
    card["event_quotes"] = ["not in the text"]
    final = finalize(card, context, check(card, context))
    assert final["status"] == "WITHHELD" and "q1" not in final and final["q6"]["level"] == MEDIUM
    assert finalize(None, context, ["BAD_SHAPE: shape: x"])["status"] == "WITHHELD"
    assert finalize(_good(context), context, [])["status"] == "COMPLETE"


def test_a_follow_up_query_must_be_short_plain_and_about_the_signal() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract | Rail News")
    assert valid_follow_up("LAND TRANSPORT AUTHORITY signalling contract", context) == "LAND TRANSPORT AUTHORITY signalling contract"
    assert valid_follow_up('"signalling contract" site:example.com', context) is None
    assert valid_follow_up("weather in Paris today please", context) is None
    assert valid_follow_up("https://example.com signalling contract", context) is None
    assert valid_follow_up("one two three four five six seven eight nine ten eleven twelve thirteen", context) is None
    assert valid_follow_up(None, context) is None
    assert default_follow_up(context) == "LAND TRANSPORT AUTHORITY awards a signalling contract"


def test_a_country_alone_gives_no_path_and_a_country_with_a_lob_gives_one_explicit_two_edge_path() -> None:
    alone, _ = _context("Singapore plans a new budget")
    assert alone.paths == {} and alone.scope == "NONE" and alone.entities == {}
    both, _ = _context("Singapore tests platform screen doors")
    path = next(item for item in both.paths.values() if item["start"]["class"] == "Place")
    assert path["text"] == "Singapore ←located_in— ST Engineering Urban Solutions Ltd —operates_in→ Mobility Rail"
    assert path["status"] == "SOURCE_STATED" and [edge["rel"] for edge in path["edges"]] == ["located_in", "operates_in"]
    assert both.scope == "ENTITY" and both.entities["ENTITY-a"]["why"] == PLACE_AND_LOB


def test_a_lob_only_story_is_scoped_to_the_lob_and_offers_no_selectable_entity() -> None:
    context, _ = _context("Platform screen doors tested at stations")
    assert context.scope == "LOB" and context.entities and all(not item["selectable"] for item in context.view["candidate_entities"])


def test_a_hypothesis_needs_a_path_and_an_event_with_no_path_gets_none() -> None:
    theme_only, _ = _context("Ransomware halts a rail operator")
    card = _good(theme_only)
    assert theme_only.paths == {} and card["hypothesis"]["text"]
    assert any(item.startswith("HYPOTHESIS_WITHOUT_PATH") for item in check(card, theme_only))
    card["hypothesis"] = {"text": "", "theme_ids": [], "path_ids": []}
    assert check(card, theme_only) == []
    with_path, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    empty = _good(with_path)
    empty["hypothesis"] = {"text": "", "theme_ids": [], "path_ids": []}
    assert any(item.startswith("EMPTY: hypothesis") for item in check(empty, with_path))


def test_only_the_register_facts_on_the_cited_paths_are_shown_and_counted_and_the_themes_hit_are_listed() -> None:
    context, _ = _context("Singapore tests platform screen doors and ransomware")
    card = _good(context)
    only = next(path_id for path_id, path in context.paths.items() if path["start"]["class"] == "LOB")
    card["connection"]["path_ids"] = [only]
    card["hypothesis"]["path_ids"] = [only]
    final = finalize(card, context, check(card, context))
    edges = {(edge["src"], edge["rel"], edge["dst"]) for edge in context.paths[only]["edges"]}
    expected = [item["text"] for item in context.evidence.values() if item["kind"] == "REGISTER_FACT" and tuple(item["edge"]) in edges]
    assert final["q4"]["register"] == expected and final["q4"]["register_facts"] == len(expected) and len(expected) < context.register_facts()
    assert [item["theme_id"] for item in final["themes"]] == ["URS-KEY-04"]


def _pool_with_one_outlet():
    return [_page("https://b.com/1", "b.com", "Signalling contract awarded", "LAND TRANSPORT AUTHORITY named the winner.")], {"https://a.com/1": _unit(0), "https://b.com/1": _unit(10)}


def test_a_same_party_answer_must_have_a_known_verdict_and_a_verbatim_quote_and_a_no_needs_one() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    card = _good(context)

    def with_party(**party):
        copy_ = copy.deepcopy(card)
        copy_["same_party"] = {"verdict": "YES", "why": "ok", "quote": ""} | party
        return " | ".join(check(copy_, context))

    assert with_party() == ""
    assert "BAD_SHAPE: same_party" in with_party(verdict="MAYBE")
    assert "IDENTITY_NO_QUOTE" in with_party(verdict="NO", quote="")
    assert "QUOTE_NOT_VERBATIM: same_party" in with_party(verdict="NO", quote="a phrase that is not in the text")
    assert with_party(verdict="NO", quote="LAND TRANSPORT AUTHORITY awards") == ""
    assert with_party(verdict="UNSURE", quote="") == ""
    assert "BANNED_TERM: same_party" in with_party(why="The likelihood is low.")
    missing = copy.deepcopy(card)
    del missing["same_party"]
    assert "BAD_SHAPE: same_party" in " ".join(check(missing, context))


def test_a_no_with_a_verified_quote_ends_the_link_and_flags_a_namesake_and_it_can_only_lower_the_level() -> None:
    pool, vectors = _pool_with_one_outlet()
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract", pool=pool, vectors=vectors)
    assert confidence(context)["level"] == HIGH
    assert confidence(context, identity={"verdict": "YES"})["level"] == HIGH
    doubted = confidence(context, identity={"verdict": "NO", "quote": "LAND TRANSPORT AUTHORITY awards"})
    assert doubted["level"] == NOT_ESTABLISHED and doubted["reasons"][0].startswith("The model judges that the party in the text is not the register's")
    assert confidence(context, identity={"verdict": "UNSURE"})["level"] == MEDIUM  # no quote: capped below high
    unsure_quoted = confidence(context, identity={"verdict": "UNSURE", "quote": "LAND TRANSPORT AUTHORITY awards"})
    assert unsure_quoted["level"] == LOW and unsure_quoted["reasons"][0].startswith("The model could not confirm")
    assert confidence(context, identity_failed=True)["level"] == MEDIUM
    alone, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    assert confidence(alone, identity={"verdict": "YES"})["level"] == MEDIUM  # a YES never raises
    card = _good(context)
    card["same_party"] = {"verdict": "NO", "why": "The text is about another organisation.", "quote": "LAND TRANSPORT AUTHORITY awards"}
    final = finalize(card, context, check(card, context))
    assert final["namesake"] is True and final["doubted"] is True and final["q6"]["level"] == NOT_ESTABLISHED and final["same_party"]["verdict"] == "NO"
    ok = finalize(_good(context), context, [])
    assert ok["namesake"] is False and ok["doubted"] is False and ok["q6"]["level"] == HIGH
    card["same_party"] = {"verdict": "UNSURE", "why": "The text may be about a ministry elsewhere.", "quote": "LAND TRANSPORT AUTHORITY awards"}
    unsure = finalize(card, context, check(card, context))
    assert unsure["namesake"] is False and unsure["doubted"] is True and unsure["q6"]["level"] == LOW
    card["same_party"] = {"verdict": "UNSURE", "why": "The text does not say.", "quote": ""}
    hedge = finalize(card, context, check(card, context))
    assert hedge["doubted"] is False and hedge["q6"]["level"] == MEDIUM


def test_an_identity_answer_that_fails_its_checks_is_withheld_and_keeps_the_level_below_high() -> None:
    pool, vectors = _pool_with_one_outlet()
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract", pool=pool, vectors=vectors)
    card = _good(context)
    card["same_party"] = {"verdict": "NO", "why": "Another party.", "quote": "words that are not in the text"}
    final = finalize(card, context, check(card, context))
    assert "same_party" in final["withheld"] and final["same_party"] is None and final["namesake"] is False and final["doubted"] is False and final["q6"]["level"] == MEDIUM


def test_the_named_parties_carry_what_the_register_states_about_them() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    named = context.view["link"]["named"][0]
    assert named["register_details"] == {"register_name": "LAND TRANSPORT AUTHORITY", "country": "not stated in the register"}
    supplier, _ = _context("BYD plans a plant")
    assert supplier.view["link"]["named"][0]["register_details"]["supplies_unit"] == ["URS Singapore"]


def test_a_model_value_of_the_wrong_type_is_a_violation_and_never_an_exception() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    base = _good(context)
    bad_values = (["YES"], {"a": 1}, 5, None, [["x"]])
    for value in bad_values:
        for path, change in (
            ("same_party", {"verdict": value, "why": "ok", "quote": ""}),
            ("affected", {"entity_ids": [value], "why": "ok"}),
            ("connection", {"path_ids": [value], "why": "ok"}),
            ("evidence", [{"evidence_id": value, "quote": "x" * 30, "supports": "EVENT"}]),
            ("evidence", [{"evidence_id": "R1", "quote": "", "supports": value}]),
            ("hypothesis", {"text": "It could add work.", "theme_ids": [value], "path_ids": ["P1"]}),
            ("hypothesis", {"text": "It could add work.", "theme_ids": ["URS-KEY-04"], "path_ids": [value]}),
            ("event_quotes", [value]),
        ):
            card = copy.deepcopy(base)
            card[path] = change
            result = check(card, context)  # must not raise
            assert result, (path, value)


def test_a_quote_must_be_long_enough_to_mean_something_and_a_doubt_quote_a_little_shorter() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    card = _good(context)
    card["event_quotes"] = ["a"]
    assert any(item.startswith("QUOTE_TOO_SHORT: event_quotes") for item in check(card, context))
    card["event_quotes"] = [context.signal["title"], "   "]
    assert not any("event_quotes" in item for item in check(card, context))  # a blank entry is ignored, and the real quote counts
    final = finalize(card, context, check(card, context))
    assert final["q1"]["quotes"] == [context.signal["title"]]  # the blank entry is not shown
    doubt = _good(context)
    doubt["same_party"] = {"verdict": "NO", "why": "Another party.", "quote": "a"}
    assert any(item.startswith("QUOTE_TOO_SHORT: same_party") for item in check(doubt, context))
    doubt["same_party"]["quote"] = "LAND"
    assert not any("same_party" in item for item in check(doubt, context))
    pool, vectors = _pool_with_one_outlet()
    with_outlet, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract", pool=pool, vectors=vectors)
    cited = _good(with_outlet)
    cited["evidence"] = [{"evidence_id": "E1", "quote": "named", "supports": "EVENT"}]
    assert any(item.startswith("QUOTE_TOO_SHORT: evidence: E1") for item in check(cited, with_outlet))


def test_a_number_is_matched_as_a_whole_number_and_not_inside_a_longer_one() -> None:
    context, _ = _context("LAND TRANSPORT AUTHORITY awards a $50 million contract in 2026", text="The award was made on October 4, 2026. " + _FILLER)
    card = _good(context)
    card["what_happened"] = "The authority awarded a 5 million contract."
    assert "UNGROUNDED_NUMBER: what_happened: 5" in " | ".join(check(card, context))
    card["what_happened"] = "The authority awarded a contract in 20."
    assert "UNGROUNDED_NUMBER: what_happened: 20" in " | ".join(check(card, context))
    card["what_happened"] = "The authority awarded a $50 million contract on October 4, 2026."
    assert check(card, context) == []
    card["what_happened"] = "The authority awarded 50,000 contracts."
    assert "UNGROUNDED_NUMBER: what_happened: 50,000" in " | ".join(check(card, context))


def test_a_competitor_with_no_line_of_business_in_the_register_gets_a_reason_that_says_so() -> None:
    pack = _pack()
    pack.competitors = {"competitors": [{"lobLabel": "Unmapped", "canonicalLob": None, "competitor": "Orphan Co", "workscope": "x", "row": 1}]}
    graph = build_graph(pack)
    index = LinkIndex(graph, pack.lob_profiles)
    signal = _signal(pack, index, "Orphan Co wins a tender", _FILLER)
    context = build_context("F01", signal, graph=graph, index=index, themes=pack.themes["themes"], pool=[], vectors={signal["url"]: _unit(0)}, same_event=0.9)
    result = confidence(context)
    assert signal["rung"] == "COMPETITOR" and context.paths.get("P1", {}).get("start", {}).get("class") != "Org"
    assert result["level"] == NOT_ESTABLISHED and "the named competitor has no line of business there that matches an org chart LOB" in result["reasons"][0]
    assert "linked by a theme only" not in " ".join(result["reasons"])


def test_a_later_path_keeps_its_register_facts_and_the_date_window_is_the_same_both_ways() -> None:
    context, _ = _context("Singapore tests platform screen doors")
    facts = [item for item in context.evidence.values() if item["kind"] == "REGISTER_FACT"]
    place_path = next(path for path in context.paths.values() if path["start"]["class"] == "Place")
    wanted = {(edge["src"], edge["rel"], edge["dst"]) for edge in place_path["edges"]}
    assert wanted <= {tuple(item["edge"]) for item in facts}  # the place-and-LOB path is not starved by the earlier paths
    near_old = _page("https://b.com/1", "b.com", "Signalling contract awarded", "Same event.", date="2026-09-28T07:00:00Z")
    near_new = _page("https://c.com/1", "c.com", "Signalling contract awarded", "Same event.", date="2026-10-12T09:00:00Z")
    vectors = {"https://a.com/1": _unit(0), "https://b.com/1": _unit(5), "https://c.com/1": _unit(5)}
    within, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract", pool=[near_old, near_new], vectors=vectors)
    assert [item["publisher"] for item in within.evidence.values() if item["kind"] == "OUTLET"] == []  # an hour past 7 days, earlier and later
    just_in = _page("https://d.com/1", "d.com", "Signalling contract awarded", "Same event.", date="2026-10-12T07:00:00Z")
    earlier_in = _page("https://e.com/1", "e.com", "Signalling contract awarded", "Same event.", date="2026-09-28T09:00:00Z")
    vectors |= {"https://d.com/1": _unit(5), "https://e.com/1": _unit(5)}
    both, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract", pool=[just_in, earlier_in], vectors=vectors)
    assert sorted(item["publisher"] for item in both.evidence.values() if item["kind"] == "OUTLET") == ["d.com", "e.com"]


def test_a_ministry_name_cannot_reach_high_because_the_register_states_no_country_for_a_customer() -> None:
    pack = _pack()
    pack.exposure["customers"].append({"customerId": "CUSTOMER-moha", "customerName": "MINISTRY OF HOME AFFAIRS", "aliases": []})
    graph = build_graph(pack)
    index = LinkIndex(graph, pack.lob_profiles)
    signal = _signal(pack, index, "MINISTRY OF HOME AFFAIRS announces a new posting", _FILLER)
    other = _page("https://b.com/1", "b.com", "Ministry of Home Affairs posting", "MINISTRY OF HOME AFFAIRS announces a new posting.")
    vectors = {signal["url"]: _unit(0), "https://b.com/1": _unit(5)}
    context = build_context("F01", signal, graph=graph, index=index, themes=pack.themes["themes"], pool=[other], vectors=vectors, same_event=0.9)
    assert context.independent_outlets() == 1
    capped = confidence(context)
    assert capped["level"] == MEDIUM and capped["reasons"][0].startswith("The name of a ministry is used in many countries")
    assert confidence(context, identity={"verdict": "YES"})["level"] == MEDIUM  # a YES from the model does not lift it
    agency, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract", pool=[_page("https://b.com/1", "b.com", "Signalling contract awarded", "LAND TRANSPORT AUTHORITY named the winner.")], vectors={"https://a.com/1": _unit(0), "https://b.com/1": _unit(5)})
    assert confidence(agency)["level"] == HIGH  # an agency name that is not a generic ministry is unaffected


def test_the_card_checks_and_prompt_follow_the_business_area() -> None:
    import dataclasses

    from grit.agents.loop.prompts import DRAFT_SYSTEM, draft_system

    context, _ = _context("LAND TRANSPORT AUTHORITY awards a signalling contract")
    dsc = dataclasses.replace(context, business_names=("Digital Systems & Cyber", "DS&C"))
    card = _good(dsc)
    card["what_happened"] = "DS&C won the signalling contract."
    assert "UNGROUNDED_NAME: what_happened: DS&C" in " | ".join(check(card, dsc))
    card["what_happened"] = "URS won the signalling contract."
    assert not any("UNGROUNDED_NAME" in item for item in check(card, dsc)), "another business area's label is not this pack's name"
    card = _good(dsc)
    card["hypothesis"]["text"] = "Under DSC-KEY-05 the business area could see 2 more reviews."
    found = " | ".join(check(card, dsc))
    assert "UNGROUNDED_NUMBER: hypothesis: 2" in found and "UNGROUNDED_NUMBER: hypothesis: 05" not in found, "a DS&C theme id is an id, not a number"

    prompt = draft_system("Digital Systems & Cyber", "DS&C")
    assert "Digital Systems & Cyber (DS&C)" in prompt and "URS" not in prompt and "Urban Solutions" not in prompt
    assert "Urban Solutions (URS)" in DRAFT_SYSTEM
