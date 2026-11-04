"""A rule raises a RED candidate. Only a named Risk & Assurance confirmer makes a report RED."""

from __future__ import annotations

import sqlite3

import pytest

from grit.pipeline.events import _elevated, red_status
from grit.pool import Pool
from grit.report.brief import red_escalation, red_summary


@pytest.fixture
def pool(tmp_path, monkeypatch):
    path = tmp_path / "pool.sqlite"
    monkeypatch.setenv("GRIT_DB", str(path))
    pool = Pool(path)
    signal_id = pool.add_signal(url="https://example.com/a", title="Depot fire", text="A fire closed the depot.", publisher="Example",
                                published_at="2026-11-01T00:00:00Z", lane="BRAVE", content_hash="h1")
    return pool, signal_id


def test_the_latest_decision_stands_and_decisions_cannot_be_edited(pool):
    pool, signal_id = pool
    assert pool.red_decisions("urs") == {}
    pool.record_red_decision(pack="urs", signal_id=signal_id, decision="CONFIRMED", impact="Depot closed.", decided_by="Jony Ling")
    pool.record_red_decision(pack="urs", signal_id=signal_id, decision="NOT_CONFIRMED", impact="Reopened the same day.", decided_by="Jony Ling")
    assert pool.red_decisions("urs")[signal_id]["decision"] == "NOT_CONFIRMED"
    assert pool.red_decisions("dsc") == {}
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        pool.conn.execute("UPDATE red_decisions SET decision = 'CONFIRMED'")
    with pytest.raises(ValueError):
        pool.record_red_decision(pack="urs", signal_id=signal_id, decision="CONFIRMED", impact=" ", decided_by="Jony Ling")


def test_only_a_confirmed_candidate_is_red_and_a_rejected_one_raises_nothing():
    candidate = {"red": 1, "tier": 3}
    assert red_status({"red": 0}) is None
    assert red_status(candidate) == "CANDIDATE" and _elevated([candidate])
    assert red_status({**candidate, "red_status": "CONFIRMED"}) == "CONFIRMED"
    assert not _elevated([{**candidate, "red_status": "NOT_CONFIRMED"}])


def test_report_wording_waits_for_the_r_and_a_decision():
    assert red_summary([{"status": "CANDIDATE"}]) == "1 RED candidate(s) await R&A impact confirmation."
    assert red_summary([{"status": "CONFIRMED"}]) == "R&A confirmed the RED impact of 1 item(s)."
    assert red_escalation([{"status": "NOT_CONFIRMED"}]).startswith("Nothing new this week")


def _red(*argv: str) -> int:
    from grit.cli import build_parser

    args = build_parser().parse_args(["red", *argv])
    return args.handler(args)


def test_only_a_named_confirmer_can_record_a_decision(pool, monkeypatch, capsys):
    pool, signal_id = pool
    confirm = ("confirm", "--pack", "urs", "--signal", signal_id, "--impact", "Depot closed.", "--trigger", "ASSET_DAMAGED")
    monkeypatch.setenv("GRIT_RED_CONFIRMERS", "")
    with pytest.raises(SystemExit, match="GRIT_RED_CONFIRMERS"):
        _red(*confirm, "--by", "Jony Ling")
    monkeypatch.setenv("GRIT_RED_CONFIRMERS", "Jony Ling")
    with pytest.raises(SystemExit, match="not a named RED confirmer"):
        _red(*confirm, "--by", "Someone Else")
    with pytest.raises(SystemExit, match="needs --impact"):
        _red("reject", "--pack", "urs", "--signal", signal_id, "--by", "Jony Ling")
    assert _red(*confirm, "--by", "jony ling") == 0
    assert Pool().red_decisions("urs")[signal_id]["decision"] == "CONFIRMED"
    assert "Recorded CONFIRMED" in capsys.readouterr().out


def test_the_red_list_leaves_out_a_candidate_the_review_excluded():
    from grit.report.brief import red_items

    row = {"title": "T", "url": "https://news.example/t", "red": 1, "red_status": "CANDIDATE"}
    routed = [{**row, "signal_id": "SIG-x", "editorial": {"decision": "EXCLUDE"}}, {**row, "signal_id": "SIG-y", "editorial": {"decision": "WATCH"}},
              {**row, "signal_id": "SIG-z"}, {**row, "signal_id": "SIG-n", "red": 0}]
    assert [item["signal_id"] for item in red_items(routed)] == ["SIG-y", "SIG-z"]


def test_confirming_red_needs_a_known_trigger(pool, monkeypatch, capsys):
    from grit.core.red_events import red_triggers

    pool, signal = pool
    monkeypatch.setenv("GRIT_RED_CONFIRMERS", "Jony Ling")
    assert "STAFF_HARMED" in red_triggers() and len(red_triggers()) == 10
    with pytest.raises(SystemExit, match="needs --trigger"):
        _red("confirm", "--pack", "urs", "--signal", signal, "--by", "Jony Ling", "--impact", "Office closed.")
    with pytest.raises(SystemExit, match="not a RED trigger"):
        _red("confirm", "--pack", "urs", "--signal", signal, "--by", "Jony Ling", "--impact", "Office closed.", "--trigger", "HEADLINE")
    assert _red("confirm", "--pack", "urs", "--signal", signal, "--by", "Jony Ling", "--impact", "Office closed.", "--trigger", "asset_damaged") == 0
    assert Pool().red_decisions("urs")[signal]["trigger_id"] == "ASSET_DAMAGED"
    assert _red("reject", "--pack", "urs", "--signal", signal, "--by", "Jony Ling", "--impact", "Reopened the same day.") == 0, "rejecting needs no trigger"
