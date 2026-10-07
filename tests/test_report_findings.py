import json

from grit.report.findings import QUESTIONS, method_lines, render_card, render_findings

_PATH = {"path_id": "P1", "text": "LAND TRANSPORT AUTHORITY —buys_from→ Urban Solutions", "status": "SOURCE_STATED", "targets_total": 1, "basis": ["4. Customers: listed as a customer of the business area"], "cited": True}


def _card(**changes):
    card = {
        "finding_id": "F01", "title": "LTA awards a signalling contract", "publisher": "a.com", "published_at": "2026-10-05T08:00:00Z", "url": "https://a.com/1",
        "also_reported_by": ["b.com", "a.com"], "rung": "COUNTERPARTY", "namesake": False, "doubted": False,
        "same_party": {"verdict": "YES", "why": "The text fits the register party.", "quote": None}, "themes": [],
        "priority": {"tier": 1, "big": True, "direct": True, "reasons": ["Hits KEY theme URS-KEY-04.", "Stated figure: $5 million (USD, as stated, not converted)."]},
        "status": "COMPLETE", "withheld": [], "violations": [],
        "q1": {"what_happened": "The authority awarded a rail signalling contract.", "quotes": ["LTA awards a signalling contract"]},
        "q2": {"register_scope": "BUSINESS_AREA", "affected": [], "why": "The register links this customer to the business area only.", "candidates_total": 0, "candidates": [], "gaps": ["The register does not say which STE entity a customer buys from."]},
        "q3": {"paths": [_PATH], "why": "The customer is listed in the register."},
        "q4": {
            "items": [{"evidence_id": "E1", "kind": "OUTLET", "publisher": "b.com", "published_at": "2026-10-05T09:00:00Z", "url": "https://b.com/1", "quote": "named the winner", "supports": "CONNECTION", "independent": True, "names_the_same_node": True, "cosine": 0.97, "text": None}],
            "register": ["LAND TRANSPORT AUTHORITY buys_from Urban Solutions. Source: 4. Customers."], "available": [{"evidence_id": "E1"}], "independent_outlets": 1, "outlets_naming_the_same_node": 1, "register_facts": 1,
        },
        "q5": {"hypothesis": "If the award shifts demand, the business area could see more signalling work.", "theme_ids": ["URS-KEY-04"], "path_ids": ["P1"]},
        "q6": {"level": "HIGH", "reasons": ["Named in the title as 'LAND TRANSPORT AUTHORITY', a register name."], "inputs": {}},
        "loop": {"iterations": [{"iteration": 1, "tool": {"query": "LTA signalling", "results": 9, "added": 1}}], "stop": "CHECKS_PASSED", "model_calls": 3, "follow_up_searches": 1, "open_issues": []},
    }
    return card | changes


def _text(card):
    return "\n".join(render_card(card))


def test_a_card_answers_the_six_questions_in_order_with_the_users_wording() -> None:
    text = _text(_card())
    positions = [text.index(f"{number}. **{question}**") for number, question in enumerate(QUESTIONS, start=1)]
    assert positions == sorted(positions) and len(QUESTIONS) == 6
    assert QUESTIONS[0] == "What happened?" and QUESTIONS[5] == "How confident are we?"
    assert "Tier 1: big event and direct link" in text and "Also carried by: b.com." in text and "[Source](https://a.com/1)" in text


def test_every_answer_shows_its_basis() -> None:
    text = _text(_card())
    assert "`P1` LAND TRANSPORT AUTHORITY —buys_from→ Urban Solutions. Stated in the workbook: 4. Customers" in text
    assert "Register: LAND TRANSPORT AUTHORITY buys_from Urban Solutions." in text
    assert 'b.com, 2026-10-05: "named the winner" (independent of the signal; names the same party). [source](https://b.com/1)' in text
    assert "This is a hypothesis, not a forecast. Basis: path `P1`, theme `URS-KEY-04`." in text
    assert "High. This is confidence in the link between the event and the STE register, by rule. It says nothing about outcomes." in text
    assert "Follow-up search: 'LTA signalling' returned 9 pages and added 1." in text


def test_a_missing_link_says_not_established_and_names_what_is_missing() -> None:
    none = _text(_card(q2=_card()["q2"] | {"register_scope": "NONE"}, q3={"paths": [], "why": None}, q5={"hypothesis": None, "theme_ids": [], "path_ids": []}))
    assert "Not established. No register entity, customer, supplier, or line of business is named" in none
    assert "Not established. No ontology path connects this signal to STE." in none and "Not stated. No ontology path connects this event to STE, so no hypothesis is made." in none
    business = _text(_card())
    assert "Not established at entity level. The register ties this story to the business area only. The register does not say which STE entity a customer buys from." in business
    lob = _text(_card(lob="Mobility Rail", q2={"register_scope": "LOB", "affected": [], "why": "ignored", "candidates_total": 5, "gaps": [], "candidates": [{"name": f"E{n}", "country": "Singapore"} for n in range(5)]}))
    assert "ties this story to the Mobility Rail line of business only. 5 entities operate in it or in its grouping." in lob and "ignored" not in lob and "E0" not in lob


def test_a_named_entity_is_listed_with_its_country_and_the_basis_the_register_gives() -> None:
    affected = [{"entity_id": "ENTITY-a", "name": "ST Engineering Urban Solutions Ltd", "country": "Singapore", "lob_label": "Mobility Rail", "why": "NAMED"}]
    text = _text(_card(q2={"register_scope": "ENTITY", "affected": affected, "why": "The text names it.", "candidates_total": 1, "candidates": affected, "gaps": []}))
    assert "ST Engineering Urban Solutions Ltd (Singapore; LOB label: Mobility Rail). Supported by the register: named in the text. Model: The text names it." in text


def test_a_withheld_part_is_marked_withheld_and_the_confidence_still_shows() -> None:
    card = _card(status="WITHHELD", withheld=["event_quotes"])
    for key in ("q1", "q2", "q3", "q4", "q5"):
        card.pop(key)
    text = _text(card)
    assert text.count("Withheld: the draft did not pass the checks.") == 5 and "High. This is confidence" in text and "Withheld: event_quotes." in text


def test_the_findings_section_and_the_method_describe_the_big_event_rule_and_the_loop() -> None:
    doc = {
        "findings": [_card()],
        "summary": {"findings": 1, "status": {"COMPLETE": 1}, "confidence": {"HIGH": 1}, "stops": {"CHECKS_PASSED": 1}, "model_calls": 3, "follow_up_searches": 1},
        "config": {"max_iterations": 3, "follow_up_budget": 10, "followups_per_finding": 1},
    }
    lines = render_findings(doc)
    assert lines[0] == "## Priority findings" and "not by severity" in lines[2]
    method = " ".join(method_lines(doc, {"min_cues": 1, "outlets_needed": 3}))
    assert "KEY STE theme" in method and "at least 1 of 3 cues" in method and "a wire service counts once" in method and "at most 1 per finding" in method
    assert "no severity" in method.lower() or "has no severity" in method
    json.dumps(doc)


def test_with_no_connection_the_evidence_is_labelled_event_corroboration_and_no_hypothesis_is_made() -> None:
    outlet = {"evidence_id": "E1", "kind": "OUTLET", "publisher": "b.com", "published_at": "2026-10-05T09:00:00Z", "url": "https://b.com/1", "quote": "a distribution deal", "supports": "EVENT", "independent": True, "names_the_same_node": False, "cosine": 0.95, "text": None}
    card = _card(
        q3={"paths": [], "why": None}, q4={"items": [outlet], "register": [], "available": [{"evidence_id": "E1"}], "independent_outlets": 1, "outlets_naming_the_same_node": 0, "register_facts": 0},
        q5={"hypothesis": None, "theme_ids": [], "path_ids": []}, themes=[{"theme_id": "URS-KEY-04", "tier": "KEY", "name": "Cybersecurity risks", "where": "TITLE", "strong": True}],
    )
    text = _text(card)
    assert "No connection is established, so there is none to support." in text
    assert "Other outlets that report the event without naming the party:" in text and 'b.com, 2026-10-05: "a distribution deal"' in text
    assert "No ontology path connects this event to STE, so no hypothesis is made. It hits the KEY or other theme `URS-KEY-04` (Cybersecurity risks) at theme level only." in text


def test_an_outlet_that_names_the_party_is_evidence_for_the_connection_whatever_the_model_called_it() -> None:
    named = {"evidence_id": "E3", "kind": "OUTLET", "publisher": "d.com", "published_at": "2026-10-05T10:00:00Z", "url": None, "quote": "q", "supports": "EVENT", "independent": True, "names_the_same_node": True, "cosine": 0.95, "text": None}
    card = _card()
    card["q4"]["items"] = [named]
    text = _text(card)
    assert text.index("Register: LAND TRANSPORT") < text.index("d.com, 2026-10-05") and "report the event without naming the party" not in text


def test_the_identity_answer_is_shown_and_a_doubted_link_leaves_the_priority_list_with_its_quote() -> None:
    assert "Same party as the register's? Yes. Model: The text fits the register party." in _text(_card())
    doubted = _card(
        finding_id="F02", title="Delhi cop's charge withheld", namesake=True, doubted=True,
        same_party={"verdict": "NO", "why": "The story is about India's ministry.", "quote": "Delhi government"},
        q6={"level": "NOT_ESTABLISHED", "reasons": [], "inputs": {"rung": "COUNTERPARTY"}},
    )
    doc = {"findings": [_card(), doubted]}
    lines = "\n".join(render_findings(doc))
    assert lines.index("### F01.") < lines.index("### Links the model doubted")
    assert "### F02." not in lines and "- **F02. Delhi cop's charge withheld**" in lines
    assert "doubted by the model (it judged the story to be about another party): The story is about India's ministry. Quote: \"Delhi government\"." in lines
    assert "Confidence in the link: Not established. Rung COUNTERPARTY." in lines
    unsure = _card(finding_id="F03", title="Another", doubted=True, same_party={"verdict": "UNSURE", "why": "The country is unclear.", "quote": "Delhi"}, q6={"level": "LOW", "reasons": [], "inputs": {"rung": "COUNTERPARTY"}})
    assert "(it could not confirm that the party is the register party): The country is unclear. Quote: \"Delhi\"." in "\n".join(render_findings({"findings": [unsure]}))
    assert "Every finding was doubted by the model. They are listed below." in "\n".join(render_findings({"findings": [doubted]}))
    assert "No signal reached the priority tiers this run." in "\n".join(render_findings({"findings": []}))


def test_evidence_for_the_event_is_kept_apart_from_evidence_for_the_connection() -> None:
    event = {"evidence_id": "E2", "kind": "OUTLET", "publisher": "c.com", "published_at": None, "url": None, "quote": None, "supports": "EVENT", "independent": True, "names_the_same_node": False, "cosine": 0.93, "text": None}
    card = _card()
    card["q4"]["items"] = [*card["q4"]["items"], event]
    text = _text(card)
    assert text.index("Register: LAND TRANSPORT") < text.index("b.com, 2026-10-05") < text.index("Other outlets that report the event") < text.index("c.com, date not stated")


def test_a_named_entity_the_model_did_not_pick_is_not_described_as_unnamed() -> None:
    candidates = [{"entity_id": "ENTITY-a", "name": "SPTel Pte. Ltd.", "country": "Singapore", "lob_label": "Smart Security & Automation", "why": "NAMED"}]
    q2 = {"register_scope": "ENTITY", "affected": [], "why": None, "candidates_total": 1, "candidates": candidates, "gaps": []}
    text = _text(_card(q2=q2))
    assert "Candidates the text supports: SPTel Pte. Ltd. (Singapore). The model chose none of them." in text and "The text names none of them" not in text


def test_a_withheld_part_is_labelled_and_a_withheld_part_never_reads_as_nothing_found() -> None:
    q2 = _card()["q2"] | {"register_scope": "BUSINESS_AREA"}
    text = _text(_card(withheld=["affected", "connection", "evidence"], q2=q2))
    assert "Withheld: the model's choice of entity did not pass the checks." in text and "Withheld: the model's citation of paths did not pass the checks." in text
    assert "(not cited by the model)" not in text  # a withheld citation is not the same as an uncited path
    q4 = {"items": [], "register": [], "available": [], "independent_outlets": 0, "outlets_naming_the_same_node": 0, "register_facts": 0}
    withheld_evidence = _text(_card(withheld=["evidence"], q4=q4))
    assert "Withheld: the model's evidence citations did not pass the checks." in withheld_evidence and "None found for the connection" not in withheld_evidence
    assert "None found for the connection" in _text(_card(q4=q4))


def test_only_the_logged_flag_files_an_outlet_under_the_connection_and_not_the_models_label() -> None:
    labelled = {"evidence_id": "E5", "kind": "OUTLET", "publisher": "f.com", "published_at": "2026-10-05T10:00:00Z", "url": None, "quote": "a quote that is long enough", "supports": "CONNECTION", "independent": True, "names_the_same_node": False, "cosine": 0.93, "text": None}
    card = _card()
    card["q4"]["items"] = [labelled]
    text = _text(card)
    assert text.index("Other outlets that report the event without naming the party:") < text.index("f.com, 2026-10-05")
