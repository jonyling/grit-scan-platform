"""DeepSeek Harness proposals stay inside the weekly contract. These tests never call the network."""

import json
import sys
import types
from pathlib import Path

import pytest

from grit.agents.harness import (
    assert_proposal_path, deepseek_harness, ground_report_edits, normalize_report_edits, prepare_output, propose_changes,
)
from grit.agents.weekly import REASON_CHARS, ContractError, check_selections
from grit.pool import Pool
from grit.settings import ROOT

CHAIN = {"fact": "Democrats won both chambers.", "exposure": "TransCore bids for a tolling contract.",
         "transmission": "A harder China line in Congress could reach that bid.", "impact": "not established",
         "significance": {"exposure": 3, "consequence": 2, "immediacy": 2, "evidence": 1, "change": 1}}


def _selection(hops, **extra):
    return {"signal_id": "A", "decision": "INCLUDE", "reason": "The election can reach a tolling bid.", "source_ids": ["A"],
            "immediate_threat": True, "impact_status": "CONFIRMED", **CHAIN, "hops": hops, **extra}


def _checked(hops):
    raw = check_selections({"selections": [_selection(hops)]}, {"A": {"text": "Democrats won the House and the Senate."}}, {"A"},
                           register_names=["TransCore"])
    return raw["selections"][0]


def test_an_uncited_midterm_path_is_only_a_watch():
    row = _checked([
        {"from": "Democrats hold both chambers", "to": "Congress is more hostile to the Chinese Communist Party", "support": "not established"},
        {"from": "A harder China stance", "to": "New Jersey members may press the TransCore tolling award", "support": "not established"},
    ])
    assert row["decision"] == "WATCH"
    assert row["immediate_threat"] is False
    assert row["significance"]["exposure"] == 1
    assert row["impact_status"] == "PLAUSIBLE"
    assert all(hop["support"] == "not established" for hop in row["hops"])


def test_a_quoted_transcore_objection_can_cite_the_hop():
    row = _checked([
        {"from": "New Jersey members spoke", "to": "They object to the TransCore tolling award", "support": "cited", "source_ids": ["A"]},
    ])
    assert row["decision"] == "INCLUDE"
    assert row["hops"][0]["support"] == "cited"
    assert row["impact_status"] == "PLAUSIBLE"
    assert "harm_verdict" not in row


def test_a_path_that_names_no_register_item_is_excluded():
    row = _checked([
        {"from": "Democrats hold both chambers", "to": "Congress debates a highway bill", "support": "not established"},
    ])
    assert row["decision"] == "EXCLUDE"
    assert row["hop_rejected"] is True


def test_unrated_evidence_cannot_stay_at_3():
    row = {"signal_id": "A", "decision": "INCLUDE", "reason": "One outlet reported it.", "source_ids": ["A"], **CHAIN,
           "significance": {"exposure": 1, "consequence": 1, "immediacy": 1, "evidence": 3, "change": 0}}
    checked = check_selections({"selections": [row]}, {"A": {"evidence_confidence": "LOW"}}, {"A"})
    assert checked["selections"][0]["significance"]["evidence"] == 1


def test_a_controlled_field_is_rejected():
    with pytest.raises(ContractError, match="controlled"):
        check_selections({"selections": [{"signal_id": "A", "decision": "EXCLUDE", "reason": "No.", "source_ids": ["A"], "severity": 3}]},
                         {"A": {}}, {"A"})


def test_prepare_output_drops_private_reasoning_and_caps_the_trace():
    raw = prepare_output({"thinking": "x" * 100000, "selections": [
        {"signal_id": "A", "reason": "y" * (REASON_CHARS + 50), "hops": [{"from": "a" * 400, "to": "TransCore", "support": "not established"}]}]})
    assert "thinking" not in raw
    assert len(raw["selections"][0]["reason"]) == REASON_CHARS
    assert len(raw["selections"][0]["hops"][0]["from"]) == 300
    assert len(json.dumps(raw).encode("utf-8")) <= 32 * 1024


def test_a_decision_map_becomes_a_selection_list():
    raw = prepare_output({"selections": {"SIG-00d543878f52b296": "EXCLUDE", "SIG-01bef69f2b95017c": "watch"}})
    checked = check_selections(raw, {"SIG-00d543878f52b296": {}, "SIG-01bef69f2b95017c": {}},
                               {"SIG-00d543878f52b296", "SIG-01bef69f2b95017c"}, chain=False)
    decisions = {row["signal_id"]: row["decision"] for row in checked["selections"]}
    assert decisions == {"SIG-00d543878f52b296": "EXCLUDE", "SIG-01bef69f2b95017c": "WATCH"}
    assert checked["selections"][0]["reason_defaulted"] is True


def test_a_long_ranking_keeps_its_decisions_under_the_trace_cap():
    rows = [{"signal_id": f"SIG-{index:016x}", "decision": "INCLUDE", "reason": "r" * 2000,
             "source_ids": [f"SIG-{index:016x}"], "fact": "f" * 2000, "exposure": "e" * 500,
             "transmission": "t" * 500, "impact": "i" * 500, "candidates": "x" * 5000}
            for index in range(80)]
    raw = prepare_output({"thinking": "z" * 100000, "candidates": {"blob": "y" * 200000}, "selections": rows})
    assert "thinking" not in raw and "candidates" not in raw
    assert len(raw["selections"]) == 80
    assert raw["selections"][0]["decision"] == "INCLUDE" and raw["selections"][0]["fact"]
    assert len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) <= 32 * 1024


def test_repeated_bottom_line_prose_becomes_two_edits():
    signal = "SIG-672339e219f3f673"
    other = "SIG-d58a64484d407bd7"
    raw = normalize_report_edits({
        "edits": [
            {"path": "bottom_line.text", "after": "ignored", "signal_ids": [signal],
             "sentences": [{"text": "Supplier Tatung was designated a disposition stock.", "signal_ids": [signal]}]},
            {"path": "bottom_line.text", "sentences": [{"text": "Chennai Metro opened a section.", "signal_ids": [other]}]},
            {"path": "bottom_line.escalation", "after": f"The embassy issued an alert [{signal}].", "signal_ids": [signal]},
            {"path": "qa.conflicts", "after": "This section is not editable."},
        ]
    }, {"bottom_line.text": "old", "bottom_line.escalation": "old"})
    paths = [edit["path"] for edit in raw["edits"]]
    assert paths == ["bottom_line.text", "bottom_line.escalation"]
    assert signal in raw["edits"][0]["text"] and other in raw["edits"][0]["text"]
    assert raw["edits"][0]["source_ids"] == [signal, other]
    assert raw["edits"][1]["text"].startswith("The embassy")


def test_a_sentence_with_an_uncited_number_is_left_out():
    signal = "SIG-672339e219f3f673"
    other = "SIG-d58a64484d407bd7"
    raw = ground_report_edits({
        "edits": [{
            "path": "bottom_line.text",
            "text": (f"Supplier Tatung was designated a disposition stock [{signal}]. "
                     f"The share rose 58.14 percent [{other}]."),
            "source_ids": [signal, other],
        }]
    }, {signal: {"title": "Tatung", "text": "Tatung was designated a disposition stock."},
        other: {"title": "Shares", "text": "The share price moved."}})
    assert raw["edits"][0]["source_ids"] == [signal]
    assert "58.14" not in raw["edits"][0]["text"]
    assert raw["edits_trimmed"]


def _pool(tmp_path):
    pool = Pool(tmp_path / "pool.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="test", kind="READER_SCAN")
    signal = pool.add_signal(url="https://news.example/a", title="Midterm result", text="Democrats won both chambers.",
                             publisher="news.example", published_at="2026-11-01", lane="TEST", content_hash="text")
    pool.save_assessment({"signal_id": signal, "run_id": "R", "pack": "test", "decision": "KEEP",
                          "harm_json": {"status": "NOT_RUN", "reason": "No structured observations"}})
    return pool, signal


def _kept(pool, signal):
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore
    store = WeeklyStore(pool, "R", search_limit=0)
    selection = {"selections": [{"signal_id": signal, "decision": "INCLUDE", "reason": "Gemini kept this.", "source_ids": [signal], **CHAIN}]}
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=lambda *_args, **_kwargs: selection, reasoner="deepseek")
    _, call_id = coordinator.invoke("Assessment", "pool-0-x", {"candidates": {signal: {}}},
                                    lambda raw: check_selections(raw, {signal: {}}, {signal}))
    store.select(call_id, selection["selections"])
    return store, coordinator


def test_a_failed_harness_keeps_the_gemini_selection(tmp_path):
    pool, signal = _pool(tmp_path)
    store, coordinator = _kept(pool, signal)
    coordinator.harness = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    coordinator.reason_with_harness()
    assert pool.weekly_assessments("R")[0]["editorial"]["reason"] == "Gemini kept this."
    assert pool.assessments("R")[0]["decision"] == "KEEP"
    assert store.state["status"] == "STARTED"
    assert pool.run("R")["config"]["deepseek_notes"][0]["task"].startswith("rank-")


def test_a_ranking_proposal_replaces_only_the_editorial_selection(tmp_path):
    pool, signal = _pool(tmp_path)
    _store, coordinator = _kept(pool, signal)

    def harness(task, payload):
        assert task.startswith("rank-")
        key = next(iter(payload["candidates"]))
        score = {"exposure": 1, "consequence": 0, "immediacy": 1, "evidence": 1, "change": 0}
        return {"selections": [{"signal_id": key, "decision": "WATCH", "reason": "Ranked against the week.", "source_ids": [key],
                                **CHAIN, "significance": score}]}

    coordinator.harness = harness
    coordinator.reason_with_harness()
    editorial = pool.weekly_assessments("R")[0]["editorial"]
    assert editorial["decision"] == "WATCH"
    assert editorial["significance"]["consequence"] == 0
    assert pool.assessments("R")[0]["decision"] == "KEEP"


def test_a_proposal_cannot_be_written_into_the_rules(tmp_path):
    with pytest.raises(ValueError, match="config"):
        assert_proposal_path(ROOT / "config" / "proposed.json")
    path = tmp_path / "proposal.json"
    assert assert_proposal_path(path) == path.resolve()
    misses = [{"kind": "KEYWORD_DROP", "text": "The midterm page was dropped.", "keyword": "TransCore tolling"}]
    assert propose_changes(misses)["apply"] is False
    assert propose_changes(misses)["proposed_keywords"] == ["TransCore tolling"]


def test_the_live_runner_uses_a_temporary_directory(monkeypatch):
    seen = {}

    class FakeHarness:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def run(self, _prompt, session_id):
            assert session_id.startswith("grit-")
            Path(seen["cwd"], "proposal.json").write_text('{"selections": []}', encoding="utf-8")
            return None

    module = types.ModuleType("deepseek_harness")
    module.DeepSeekHarness = FakeHarness
    monkeypatch.setitem(sys.modules, "deepseek_harness", module)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    result = deepseek_harness("rank-demo", {"candidates": {}})
    cwd = Path(seen["cwd"]).resolve()
    assert result == {"selections": []}
    assert cwd != ROOT and ROOT not in cwd.parents
    assert Path(seen["dsh_home"]).resolve() != ROOT
