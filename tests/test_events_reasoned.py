from __future__ import annotations

from pathlib import Path

from grit.pipeline.events import _elevated, _group
from grit.pool import Pool


def _row(signal_id, score=None, *, threat=False, cites=(), band="UNRATED", tier=3, title=""):
    editorial = {"decision": "INCLUDE", "source_ids": [signal_id, *cites], "immediate_threat": threat}
    if score is not None:
        editorial["significance"] = {"exposure": score, "consequence": 0, "immediacy": 0, "evidence": 0, "change": 0}
    return {"signal_id": signal_id, "tier": tier, "source_band": band, "editorial": editorial, "publisher": signal_id, "title": title}


def test_in_a_reasoned_run_significance_decides_materiality_not_the_rule_tier():
    assert _elevated([{**_row("A", 3), "editorial": {**_row("A", 3)["editorial"], "significance": {"exposure": 3, "consequence": 3, "immediacy": 2, "evidence": 0, "change": 0}}}])
    assert not _elevated([_row("B", 3, tier=1)]), "a tier-1 report with a low reviewed score is not material"
    assert _elevated([_row("C", 0, threat=True)]), "an immediate threat is material whatever its score"


def test_reports_a_selection_cites_as_the_same_event_are_one_event(tmp_path: Path):
    pool = Pool(tmp_path / "grit.sqlite")
    rows = [_row("SIG-a", 11, cites=("SIG-b", "SIG-c"), title="US orders staff out of Saudi Arabia"),
            _row("SIG-b", 9, band="T1", title="Security alert for Saudi Arabia"),
            _row("SIG-c", 7, title="PRC weaponizing Taiwan handover")]
    groups = sorted(sorted(row["signal_id"] for row in members) for members in _group(pool, rows).values())
    assert groups == [["SIG-a", "SIG-b"], ["SIG-c"]], "a cited report about another event does not join it"


def test_the_status_line_text_ends_at_a_sentence_or_an_ellipsis():
    from grit.report.format_v2 import short_text

    assert short_text("Fed meeting on 9 Dec. Markets expect a hold.") == "Fed meeting on 9 Dec."
    cut = short_text("RIYADH — The State Department ordered non-emergency US government employees and their families out of "
                     "Saudi Arabia effective October 23, leaving the embassy on a skeleton crew.")
    assert cut.endswith("…") and len(cut) <= 121 and not cut.endswith(" …")


def test_elevated_reports_of_one_story_merge_into_one_event():
    from grit.pipeline.events import UP, _merge_stories

    def event(key, title, score, band="UNRATED", day="2026-11-01"):
        row = {"signal_id": key, "title": title, "source_band": band, "tier": 3, "publisher": key}
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3, "red": False,
                "red_candidate": False, "lead": row, "members": [row], "signal_ids": [key], "publishers": 1, "first_date": day,
                "last_date": day, "risks": [], "risk_label": "", "requests": []}

    events = [event("a", "Houthi drone and missile strikes targeting Saudi Arabian airports", 12),
              event("b", "US Embassy security alert on Saudi airport attacks", 10, band="T1"),
              event("c", "US orders staff departure from Saudi Arabia amid attacks", 11),
              event("d", "Israeli shekel and stocks drop amid election deadlock", 10),
              event("e", "Saudi Arabia hosts tourism summit", 9)]
    merged = _merge_stories(events, {})
    stories = sorted(sorted(item["signal_ids"]) for item in merged)
    assert stories == [["a", "b"], ["c"], ["d"], ["e"]], "words shared only through another report do not chain stories"
    saudi = next(item for item in merged if len(item["signal_ids"]) == 2)
    assert saudi["score"] == 12 and saudi["lead"]["signal_id"] == "b", "the top score stands and the T1 report leads"


def test_reports_naming_the_same_jurisdiction_and_risk_are_one_story():
    import re

    from grit.pipeline.events import UP, _merge_stories

    def event(key, title, risks, score=10):
        row = {"signal_id": key, "title": title, "source_band": "UNRATED", "tier": 3, "publisher": key}
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3, "red": False,
                "red_candidate": False, "lead": row, "members": [row], "signal_ids": [key], "publishers": 1, "first_date": "2026-11-01",
                "last_date": "2026-11-02", "risks": risks, "risk_label": "", "requests": []}

    places = {"Saudi Arabia": re.compile(r"(?<![\w])(?:Saudi Arabia|Saudi)", re.IGNORECASE),
              "Israel": re.compile(r"(?<![\w])(?:Israel)", re.IGNORECASE)}
    events = [event("a", "US warns of Houthi strikes on Saudi airports", ["URS-EMERGING-01", "URS-KEY-05", "URS-KEY-04"], 12),
              event("b", "State Department orders non-emergency staff out of Saudi Arabia", ["URS-EMERGING-01"]),
              event("c", "Saudi Arabia hosts a tourism summit", ["URS-KEY-01"]),
              event("d", "Israeli shekel drops amid election deadlock", ["URS-KEY-02"])]
    merged = _merge_stories(events, {}, places)
    assert sorted(sorted(item["signal_ids"]) for item in merged) == [["a", "b"], ["c"], ["d"]]
    saudi = next(item for item in merged if len(item["signal_ids"]) == 2)
    assert saudi["risks"][0] == "URS-EMERGING-01" and len(saudi["risks"]) <= 3
