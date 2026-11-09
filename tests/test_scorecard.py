from __future__ import annotations

from grit.report.scorecard import scorecard


def test_the_scorecard_measures_the_2_november_weaknesses():
    brief = {
        "evidence_ids": ["SIG-a", "SIG-b", "SIG-c"],
        "v2": {"status": "WATCHPOINT", "tiers": {"SIG-a": "T1", "SIG-b": "UNRATED", "SIG-c": "CONTESTED"},
               "escalating": [{"lead": "SIG-b", "signal_ids": ["SIG-c"]}], "arrows": ["Changi Airport and NCS upgrade ▲"],
               "risk_window": ["3 Nov: RIYADH — The State Department ordered non-emergency US government employees and their"],
               "watch": [{"text": {"plain": "Passing mention only, with nothing specific to Urban Solutions."}}],
               "gate": [{"question": "Were the top findings challenged by a contradiction search?", "answer": "Partly"}]},
        "method": {"weekly": {"selections": [
            {"signal_id": "SIG-b", "editorial": {"decision": "WATCH", "source_check": "verify",
                                                 "significance": {"exposure": 1, "consequence": 1, "immediacy": 2, "evidence": 2, "change": 1}}}]}},
        "conditions": [{"name": "Geopolitical impact to business operations", "trend": "Unchanged"}],
        "bottom_line": {"text": {"plain": "Geopolitical impact to business operations is escalating as China proposed export controls."}},
        "management_requests": [{"status": "ACTIVE", "signals": []}],
    }
    result = scorecard(brief)
    metrics, checks = result["metrics"], result["checks"]
    assert metrics["share_t1_t3"] == 0.33 and metrics["contested_cited"] == 1 and metrics["watched_for_verification"] == 1
    assert metrics["escalating_scores"] == [7] and metrics["escalating_below_8"] == 1
    assert metrics["truncated_status_text"] == 1 and metrics["passing_mentions_shown"] == 1
    assert metrics["trend_words_contradict_trend"] == ["Geopolitical impact to business operations"]
    assert [check for check, ok in checks.items() if ok] == [
        "no phrase the corrections ledger retired",
        "every open correction appears on the brief",
    ], "every other check fails"
