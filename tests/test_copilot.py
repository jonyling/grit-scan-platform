"""The Co-Pilot (spec 40), offline: the reader-scan fixture's pool, no embedder, and a stub model."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from grit.copilot.agent import ask, handle, history, record, render
from grit.copilot.checks import check_answer
from grit.copilot.scope import reach, scope_for
from grit.copilot.search import fuse, search
from grit.copilot.tools import Source, Toolbox
from grit.pipeline.reader_scan import stub_client
from grit.profiles import Profile

LABELS = {"urs": {"label": "Urban Solutions", "level": "BA"}, "urs-tw": {"label": "URS Taiwan", "level": "ENTITY"}, "ste": {"label": "ST Engineering Group", "level": "GROUP"}}
TODAY = date(2026, 10, 10)


def _profile(profile_id: str = "wilson", **changes: Any) -> Profile:
    base = dict(profile_id=profile_id, name=profile_id.title(), role="Reader", aperture="WIDE_APERTURE", packs=("urs-tw",), business_scope={}, review_role="READER",
                red_alerts={}, email=None, briefs={"urs-tw": {"sections": ["PRIORITY_FINDINGS"]}})
    return Profile(**{**base, **changes})


@pytest.fixture
def scanned(tmp_path: Path, monkeypatch):  # noqa: ANN201
    from test_reader_scan import _scanned

    return _scanned(tmp_path, monkeypatch)


def test_reach_is_own_packs_or_every_pack_for_group_readers() -> None:
    assert reach(_profile(), LABELS) == ("urs-tw",)
    group = _profile(briefs={"ste": {}}, packs=("ste",))
    assert reach(group, LABELS) == ("ste", "urs", "urs-tw")
    assert reach(_profile(business_scope={"level": "GROUP"}), LABELS) == ("ste", "urs", "urs-tw")
    with pytest.raises(PermissionError):
        scope_for(_profile(), pack="urs", labels=LABELS)
    assert scope_for(group, pack="urs", labels=LABELS).packs == ("urs",)


def test_fusion_rewards_agreement_between_lists() -> None:
    fused = [item for item, _score in fuse(["a", "b", "c"], ["c", "b", "d"])]
    assert set(fused[:2]) == {"b", "c"} and set(fused) == {"a", "b", "c", "d"}, "items both lists rank come first"
    assert [item for item, _ in fuse(["x", "y"], [])] == ["x", "y"]


def test_search_finds_by_name_and_collapses_the_same_story(scanned) -> None:
    pool, run = scanned
    blue = next(row for row in pool.assessments(run.run_id, kept_only=True) if "Blue Line" in row["title"])
    copy = pool.add_signal(url="https://other.example/blue-line", title=blue["title"], text="Another outlet, same story.", publisher="other.example",
                           published_at=blue["published_at"], lane="BRAVE", content_hash="x")
    pool.save_assessment({**{key: blue[key] for key in ("run_id", "pack", "decision", "tier", "rung")}, "signal_id": copy, "link_json": blue["link"], "themes_json": blue["themes"]})
    scope = scope_for(_profile(), today=TODAY, labels=LABELS)
    result = search(pool, scope, "Taichung Blue Line BC21 tender")
    top = result.events[0]
    assert "Blue Line" in top.row["title"] and len(top.outlets) == 2, "two pages of one story are one event with both outlets"
    assert result.in_scope == 3 and result.runs == 1
    narrow = search(pool, scope_for(_profile(aperture="MATERIAL_APERTURE"), today=TODAY, labels=LABELS), "PLA aircraft Taiwan Strait")
    assert all(event.row["route"]["include"] for event in narrow.events)
    assert search(pool, scope_for(_profile(), window="run", run_id="none", today=TODAY, labels=LABELS), "Blue Line").events == []


def test_toolbox_reads_state_and_turns_brief_citations_into_source_ids(scanned) -> None:
    pool, run = scanned
    tools = Toolbox(pool, scope_for(_profile(), today=TODAY, labels=LABELS))
    state = tools.call("get_state", {"pack": "urs-tw"})
    strait = next(item for item in state["standing_items"] if item["item_id"] == "TW-GLOBAL-03")
    assert strait["trend"] == "Easing" and strait["summary"].endswith("[S1]") and tools.sources.get("S1").kind == "SIGNAL"
    assert state["bottom_line"]["id"].startswith("B")
    assert tools.call("get_state", {"pack": "urs"})["error"].startswith("urs is outside")
    assert "Unknown tool" in tools.call("drop_tables", {})["error"]
    assert "Unknown argument" in tools.call("get_state", {"pack": "urs-tw", "sql": "x"})["error"]
    assert "must be text" in tools.call("condition_history", {"item_id": ["x"]})["error"]
    assert "must be text" in tools.call("lookup_register", {"kind": 5})["error"]
    history_rows = tools.call("condition_history", {"item_id": "TW-GLOBAL-03"})
    assert len(history_rows["runs"]) == 1 and "no trend history" in history_rows["note"]


def _sources() -> dict[str, Source]:
    return {
        "S1": Source("S1", "SIGNAL", "k1", "Taiwan reports 25 PLA aircraft near the strait.", ("focustaiwan.tw",), "focustaiwan.tw, 2026-10-05", "https://focustaiwan.tw/a"),
        "B1": Source("B1", "STATE", "k2", "PLA activity eased. No URS Taiwan project is disrupted.", ("GRIT-Scan", "the brief"), "URS Taiwan brief, 2026-10-07"),
        "R1": Source("R1", "REPORT", "k3", "Needs reach US$588 billion over ten years.", ("World Bank",), "World Bank RDNA5, p. 4", note="Internal report (Co-Confidential): quoted for ST Engineering readers only."),
    }


GOOD = {"answer": "PLA activity eased this week [B1, S1].", "evidence": ["Taiwan counted 25 PLA aircraft [S1]."], "implications": ["No URS Taiwan project is disrupted [B1]."],
        "uncertain": ["One week of data cannot show a trend."], "next_step": "Open the Taiwan Strait condition."}


@pytest.mark.parametrize(("change", "problem"), [
    ({"answer": "PLA activity eased this week."}, "UNCITED"),
    ({"answer": "PLA activity eased [S9]."}, "UNKNOWN_SOURCE"),
    ({"evidence": ["Taiwan counted 40 PLA aircraft [S1]."]}, "UNGROUNDED_NUMBER"),
    ({"evidence": ["Reconstruction needs will reach US$588 billion [R1]."]}, "UNATTRIBUTED_FORECAST"),
    ({"implications": ["Rail bids face delay [S1]."]}, "UNHEDGED_INFERENCE"),
    ({"answer": "The likelihood of escalation fell [B1]."}, "BANNED_TERM"),
    ({"answer": "See https://focustaiwan.tw [S1]."}, "URL_IN_TEXT"),
    ({"answer": "One [S1]. Two [S1]. Three [S1]. Four [S1]."}, "TOO_LONG"),
    ({"evidence": []}, "NO_EVIDENCE"),
    ({"evidence": 5}, "BAD_SHAPE"),
    ({"answer": "Read this <img src=x> [S1]."}, "MARKUP_IN_TEXT"),
    ({"evidence": ["See [the page](javascript:alert(1)) [S1]."]}, "MARKUP_IN_TEXT"),
    ({"answer": "Revenue will fall 40% next year.", "nothing_found": True}, "UNGROUNDED_NUMBER"),
    ({"answer": "Nothing on this.", "nothing_found": "false", "evidence": []}, "UNCITED"),
])
def test_every_answer_fault_is_named(change: dict, problem: str) -> None:
    found = check_answer({**GOOD, **change}, _sources())
    assert any(item.startswith(problem) for item in found), found


def test_a_good_answer_passes_and_attributed_forecasts_and_hedges_are_allowed() -> None:
    assert check_answer(GOOD, _sources()) == []
    allowed = {**GOOD, "evidence": ["According to the World Bank, needs will reach US$588 billion [R1]."], "implications": ["Rail bids could face delay [S1]."]}
    assert check_answer(allowed, _sources()) == []
    assert check_answer({"answer": "GRIT-Scan holds nothing on this.", "nothing_found": True}, {}) == []
    assert check_answer({**GOOD, "evidence": "- Taiwan counted 25 PLA aircraft [S1].\n- PLA activity eased [B1]."}, _sources()) == [], "a bulleted string is split into lines"


def test_render_adds_links_and_access_notes_and_follows_the_label_setting() -> None:
    text = render({**GOOD, "evidence": ["According to the World Bank, needs reach US$588 billion [R1]."]}, _sources(), steps="Steps: read a brief.")
    assert "[focustaiwan.tw, 2026-10-05](https://focustaiwan.tw/a)" in text and "(URS Taiwan brief, 2026-10-07" in text
    assert "Co-Confidential" in text and "Not reviewed" in text and "Steps: read a brief." in text
    assert "[S1]" not in text and "Not reviewed" not in render(GOOD, _sources(), show_review_status=False)


def _model(script: list[dict]):  # noqa: ANN202
    seen: list[dict] = []

    def respond(_system: str, payload: dict) -> dict:
        seen.append(payload)
        return script[min(len(seen), len(script)) - 1]

    return stub_client(respond), seen


def test_the_loop_reads_tools_then_answers_with_checked_citations(scanned) -> None:
    pool, run = scanned
    client, seen = _model([
        {"plan": "Read the brief and search.", "tool_calls": [{"tool": "get_state", "args": {"pack": "urs-tw"}}, {"tool": "search_signals", "args": {"query": "PLA aircraft"}}]},
        {"final": {"answer": "Cross-strait activity eased this week [B2, S1].", "evidence": ["Taiwan's defence ministry counted 25 PLA aircraft [S1]."], "implications": [], "uncertain": [], "next_step": ""}},
    ])
    scope = scope_for(_profile(), today=TODAY, labels=LABELS)
    result = ask(pool, scope, "What happened in the Taiwan Strait?", client=client, model="stub")
    assert result["status"] == "VALIDATED", result["markdown"]
    assert "focustaiwan.tw" in result["markdown"] and "read a brief" in result["steps"]
    assert {item["id"] for item in result["citations"]} == {"B2", "S1"}
    assert seen[1]["results"][0]["tool"] == "get_state" and seen[1]["rounds_left"] == 3
    assert [call["tool"] for call in result["log"]["detail"]["calls"]] == ["get_state", "search_signals"]


def test_a_failing_answer_is_redrafted_once_then_withheld(scanned) -> None:
    pool, run = scanned
    scope = scope_for(_profile(), today=TODAY, labels=LABELS)
    bad = {"final": {"answer": "Activity eased.", "evidence": []}}
    good = {"final": {"answer": "Cross-strait activity eased this week [B2].", "evidence": ["The brief records easing [B2]."]}}
    tool_round = {"tool_calls": [{"tool": "get_state", "args": {"pack": "urs-tw"}}]}
    client, seen = _model([tool_round, bad, good])
    fixed = ask(pool, scope, "Taiwan?", client=client, model="stub")
    assert fixed["status"] == "VALIDATED" and "violations" in seen[-1] and len(fixed["log"]["detail"]["attempts"]) == 2
    client, _ = _model([tool_round, bad, bad])
    withheld = ask(pool, scope, "Taiwan?", client=client, model="stub")
    assert withheld["status"] == "WITHHELD" and "withheld" in withheld["markdown"]


def test_the_loop_stops_asking_for_tools_at_the_round_limit(scanned) -> None:
    pool, run = scanned
    scope = scope_for(_profile(), today=TODAY, labels=LABELS)
    client, seen = _model([{"tool_calls": [{"tool": "search_signals", "args": {"query": "Taiwan"}}]}] * 4 + [{"final": {"answer": "Nothing new [S1].", "evidence": ["A signal [S1]."]}}])
    result = ask(pool, scope, "Taiwan?", client=client, model="stub", max_rounds=2)
    assert len(result["log"]["detail"]["calls"]) == 2 and seen[2]["rounds_left"] == 0 and "instruction" in seen[2]


def test_conversations_are_private_and_commands_are_handled_by_code(scanned, tmp_path: Path) -> None:
    pool, run = scanned
    wilson, chairman = _profile("wilson"), _profile("mkteo")
    record(pool, chairman, "What should I raise with the CEO?", "An answer.")
    record(pool, wilson, "What changed?", "Another answer ([Reuters, 2026-10-01](https://example.com/a)).")
    assert [row["content"] for row in history(pool, "wilson")] == ["What changed?", "Another answer (Reuters, 2026-10-01)."]
    assert all("CEO" not in row["content"] for row in history(pool, "wilson")), "Wilson never sees the Chairman's questions"
    scope = scope_for(wilson, today=TODAY, labels=LABELS)
    never = lambda: pytest.fail("a command never reaches the model")  # noqa: E731
    noted = handle(pool, scope, "/note Keep answers to five lines.", answer=never, notes_root=tmp_path / "readers")
    saved = list((tmp_path / "readers" / "wilson" / "inbox").glob("*_copilot.md"))
    assert noted["status"] == "COMMAND" and len(saved) == 1 and "five lines" in saved[0].read_text()
    proposed = handle(pool, scope, "/concern Strait closure: blockade, quarantine", answer=never)
    assert proposed["status"] == "COMMAND" and pool.management_requests()[-1]["status"] == "PROPOSED"


def test_codes_and_todays_date_phrases_are_not_numbers_but_everything_else_is_checked() -> None:
    from grit.copilot.checks import answer_dates

    dates = answer_dates(date(2026, 10, 20))
    ok = {**GOOD, "answer": "Since 19 October, PLA activity under TW-GLOBAL-03 eased, with H200 exports unchanged [B1, S1]."}
    assert check_answer(ok, _sources(), dates=dates) == []
    assert any(item.startswith("UNGROUNDED_NUMBER") for item in check_answer(ok, _sources())), "without the dates, 19 is ungrounded"
    for invented in ("Taiwan counted 20 PLA aircraft [S1].", "Exports fell 10 per cent [S1].", "The fine was $12bn [S1].", "Output rose 3x [S1].", "A 40-year high [S1].", "Needs reach US$1.2bn [R1]."):
        found = check_answer({**GOOD, "evidence": [invented]}, _sources(), dates=dates)
        assert any(item.startswith("UNGROUNDED_NUMBER") for item in found), invented
