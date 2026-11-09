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
