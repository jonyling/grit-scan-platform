"""Front-page ranking, sentence-level cites, and a specific ask for the reasoned weekly brief."""

from __future__ import annotations

import json

from grit.agents.checks import sentence_cite_problems
from grit.agents.harness import prepare_output
from grit.pipeline.weekly import escalation_problems


def test_a_sentence_is_rejected_when_its_cited_page_lacks_the_name_it_asserts():
    sources = {
        "S1": {"title": "Typhoon Champi skirts Taiwan's east coast",
               "text": "Champi brought heavy rain to Hualien on 2 Nov. Winds eased overnight.", "date": "2026-11-02"},
        "S2": {"title": "Legislature debates budget", "text": "Lawmakers argued over defence spending in Taipei.", "date": "2026-11-01"},
    }
    ok = "Typhoon Champi brought heavy rain to Hualien on 2 Nov [S1]."
    bad = "Typhoon Champi brought heavy rain to Hualien on 2 Nov [S2]."
    assert sentence_cite_problems(ok, sources, "summary") == []
    found = sentence_cite_problems(bad, sources, "summary")
    assert any(item.startswith("UNSUPPORTED_NAME:") for item in found)
    assert "Champi" in " ".join(found) or "champi" in " ".join(found).casefold()


def test_an_escalation_that_only_names_readers_is_rejected():
    owners = ["Head, Urban Solutions", "Risk & Assurance"]
    vague = "Readers should review the typhoon item and consider forwarding it to named readers."
    assert escalation_problems(vague, owners=owners)
    assert any("owner role" in item for item in escalation_problems(vague, owners=owners))
    good = ("Head, Urban Solutions should confirm whether Champi disrupted URS site access "
            "before the next cycle.")
    assert escalation_problems(good, owners=owners) == []


def test_a_ranking_payload_is_reduced_until_it_fits():
    rows = [{"signal_id": f"SIG-{index:016x}", "decision": "INCLUDE", "reason": "r" * 2000,
             "source_ids": [f"SIG-{index:016x}"], "fact": "f" * 2000, "exposure": "e" * 800,
             "transmission": "t" * 800, "impact": "i" * 800, "significance": {
                 "exposure": 2, "consequence": 2, "immediacy": 2, "evidence": 1, "change": 1}}
            for index in range(80)]
    raw = prepare_output({"thinking": "z" * 50000, "candidates": {"blob": "y" * 100000}, "selections": rows})
    assert len(raw["selections"]) == 80
    assert raw.get("rank_compact") is True
    assert all(row.get("decision") == "INCLUDE" and row.get("fact") == "not established" for row in raw["selections"])
    assert all(set(row["significance"]) == {"exposure", "consequence", "immediacy", "evidence", "change"}
               for row in raw["selections"])
    assert len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) <= 32 * 1024


def test_a_statement_on_the_business_own_status_carries_no_news_citation():
    from grit.pipeline.weekly import sentence_break, without_internal_claims
    from grit.report.brief import citations, plain

    sources = {"S1": {"title": "Thai airlines hit by data breach", "text": "Two airlines were affected."}}
    problems = sentence_cite_problems("No internal impact is confirmed on business operations [S1].", sources, "bottom_line.text")
    assert problems and problems[0].startswith("INTERNAL_CLAIM_CITED")
    assert sentence_break(without_internal_claims("No internal impact is confirmed. Two airlines were affected")) is None
    signal = "SIG-0123456789abcdef"
    runs, _ = citations(f"Two airlines were affected [{signal}]. No internal impact is confirmed [{signal}].",
                        {signal: {"publisher": "thethaiger.com", "published_at": "2026-11-12T03:10:00Z", "url": "https://thethaiger.com/a", "title": "t"}})
    assert plain(runs) == "Two airlines were affected (thethaiger.com, 12 Nov 2026). No internal impact is confirmed."


def test_a_policy_status_sentence_needs_no_citation_but_a_news_basis_does():
    from grit.pipeline.weekly import sentence_break, without_internal_claims

    policy = ("The report status is ELEVATED based on five included findings combining identifiable exposure, credible evidence, "
              "and near-term timing across regional security flashpoints. An ADNOC Logistics & Services vessel was attacked")
    assert sentence_break(without_internal_claims(policy)) is None
    news = "The report status is ELEVATED based on missile strikes across the Gulf. An ADNOC Logistics & Services vessel was attacked"
    assert sentence_break(without_internal_claims(news)) is not None


def test_common_red_trigger_phrasings_stand_without_a_citation():
    from grit.agents.checks import INTERNAL_CLAIM

    for sentence in ("The status would be raised to RED upon a Middle East evacuation.",
                     "A Middle East evacuation would raise the status to RED.",
                     "It becomes RED only when R&A confirms a site was hit."):
        assert INTERNAL_CLAIM.search(sentence), sentence
    for sentence in ("Iran raised its air-defence alert to red after the strike.", "The tanker turned red-hot after the blast."):
        assert not INTERNAL_CLAIM.search(sentence), sentence


def _withheld_brief():
    etihad, other = "SIG-0000000000000001", "SIG-0000000000000002"
    qa = [{"item": "Event: Etihad extends cancellations", "origin": "CHECK", "source_ids": [etihad],
           "correction": {"plain": "Escalating-items row withheld: the draft failed its checks twice. UNSUPPORTED_DATE: summary: "
                                   "'30 November' is not on the cited page(s) S1."}}]
    look = [{"date": "2026-11-30", "event": "Etihad cancelled flights until November 30.", "signal_ids": [etihad]}]
    return etihad, other, {"qa": qa, "v2": {"look_ahead": look, "risk_window": ["30 Nov: Etihad cancelled flights until November 30."],
                                          "escalating": [{"signal_ids": [other], "summary": {"runs": [{"text": "Riyadh was hit on 30 Nov."}]}}]}}


def test_a_withheld_date_may_not_return_through_the_withheld_event_s_own_reports():
    from grit.report.withheld import leaks

    _etihad, _other, brief = _withheld_brief()
    found = leaks(brief)
    assert any("v2.look_ahead[0].event" in line for line in found) and any("v2.risk_window[0]" in line for line in found)
    # The same date about another event, from other reports, is not a leak.
    assert not any("escalating" in line for line in found)
    brief["v2"]["look_ahead"], brief["v2"]["risk_window"] = [], []
    assert leaks(brief) == []


def test_a_new_agents_pass_clears_the_withheld_entries_of_the_last_one(tmp_path):
    from grit.pool import Pool

    pool = Pool(tmp_path / "grit.sqlite")
    pool.add_qa("R", item="Event: old", correction="Escalating-items row withheld.", origin="CHECK")
    pool.add_qa("R", item="SIG-1", correction="Critic note.", origin="WEEKLY_CRITIC")
    assert pool.clear_check_qa("R") == 1
    assert [row["origin"] for row in pool.qa_log("R")] == ["WEEKLY_CRITIC"]


def test_claims_read_as_claims():
    from grit.agents.checks import claim_problems

    page = "Yemen's Houthis said they fired two ballistic missiles. Hanwha nears a contract for the FLNG project."
    codes = lambda claim: [problem.split(":")[0] for problem in claim_problems(claim, page, "summary")]  # noqa: E731
    assert codes("Houthis targeted Saudi forces with two ballistic missiles") == ["CLAIM_AS_FACT"]
    assert codes("The WhiteArmor ransomware group stole 100GB of data") == ["CLAIM_AS_FACT"]
    assert codes("The Revolutionary Guard confirmed six drones hit the radar") == ["CLAIM_CONFIRMED"]
    assert codes("Hanwha Ocean signed the FLNG contract") == ["NEAR_DEAL_AS_DONE"]
    for fine in ("Yemen's Houthis said they fired two ballistic missiles", "Coalition forces intercepted two Houthi missiles fired at Riyadh",
                 "Maersk confirmed hackers extracted 100GB", "Hanwha Ocean nears the FLNG contract"):
        assert codes(fine) == [], fine
