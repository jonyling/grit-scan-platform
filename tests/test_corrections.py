"""The corrections ledger (URS methodology, section 22): R&A records a material correction once, and every later report of
the pack carries it, until R&A closes it."""

from __future__ import annotations

import sqlite3

import pytest

from grit.core.corrections import as_admitted, ledger_view, retired_in
from grit.pool import Pool

TAIWAN = {"earlier": "Taiwan opportunity loss S$600m", "why": "Item 5 final v2 revised the figure", "corrected": "S$645m opportunity loss",
          "source": "Item 5 final v2", "recorded_by": "Jony Ling"}


@pytest.fixture
def pool(tmp_path, monkeypatch):
    path = tmp_path / "pool.sqlite"
    monkeypatch.setenv("GRIT_DB", str(path))
    return Pool(path)


def _corrections(*argv: str) -> int:
    from grit.cli import build_parser

    args = build_parser().parse_args(["corrections", *argv])
    return args.handler(args)


def test_a_correction_is_carried_until_closed_and_never_edited(pool):
    first = pool.record_correction(pack="urs", **TAIWAN, phrase="S$600m", update_old="Earlier Taiwan scans")
    second = pool.record_correction(pack="urs", earlier="KKIA reopened 18 Oct", why="Sources conflict", corrected="Partly reopened 25 Oct",
                                    source="Reuters, 26 Oct", recorded_by="Jony Ling")
    assert (first["correction_id"], second["correction_id"]) == ("COR-urs-001", "COR-urs-002")
    assert pool.corrections("dsc") == []
    pool.close_correction(pack="urs", correction_id="COR-urs-001", closed_by="Jony Ling", reason="Every deck now shows S$645m.")
    assert [row["correction_id"] for row in pool.corrections("urs")] == ["COR-urs-002"]
    closed = pool.corrections("urs", include_closed=True)[0]
    assert (closed["closed_by"], closed["close_reason"]) == ("Jony Ling", "Every deck now shows S$645m.")
    with pytest.raises(ValueError, match="not an open correction"):
        pool.close_correction(pack="urs", correction_id="COR-urs-001", closed_by="Jony Ling", reason="Again.")
    with pytest.raises(ValueError, match="needs why"):
        pool.record_correction(pack="urs", **{**TAIWAN, "why": " "})
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        pool.conn.execute("UPDATE corrections SET corrected = 'S$700m'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        pool.conn.execute("DELETE FROM corrections")


def test_the_cli_records_lists_and_closes_corrections(pool, capsys):
    with pytest.raises(SystemExit, match="needs --why, --corrected, --source"):
        _corrections("add", "--pack", "urs", "--earlier", "S$600m", "--by", "Jony Ling")
    assert _corrections("add", "--pack", "urs", "--earlier", TAIWAN["earlier"], "--why", TAIWAN["why"], "--corrected", TAIWAN["corrected"],
                        "--source", TAIWAN["source"], "--by", "Jony Ling", "--phrase", "S$600m") == 0
    assert "Recorded COR-urs-001" in capsys.readouterr().out
    _corrections("list", "--pack", "urs")
    listed = capsys.readouterr().out
    assert "COR-urs-001  OPEN" in listed and "Retired phrase: S$600m" in listed
    with pytest.raises(SystemExit, match="needs --reason"):
        _corrections("close", "--pack", "urs", "--id", "COR-urs-001", "--by", "Jony Ling")
    assert _corrections("close", "--pack", "urs", "--id", "COR-urs-001", "--by", "Jony Ling", "--reason", "Decks updated.") == 0
    capsys.readouterr()
    _corrections("list", "--pack", "urs")
    assert "No open correction for urs." in capsys.readouterr().out
    with pytest.raises(SystemExit, match="not an open correction"):
        _corrections("close", "--pack", "urs", "--id", "COR-urs-001", "--by", "Jony Ling", "--reason", "Again.")


def test_agents_receive_open_corrections_with_the_admitted_ones(pool):
    from grit.pipeline.weekly import memory_context

    pool.start_run("R", run_date="2026-11-09", pack="urs", kind="READER_SCAN")
    assert memory_context(pool, "R") == []
    pool.record_correction(pack="urs", **TAIWAN, phrase="S$600m")
    pool.record_correction(pack="dsc", **TAIWAN)
    [note] = memory_context(pool, "R")
    assert note["kind"] == "CORRECTION" and note["ledger_id"] == "COR-urs-001"
    assert "Corrected fact: S$645m opportunity loss" in note["text"] and 'Do not write "S$600m".' in note["text"]


def test_a_retired_phrase_is_found_whatever_its_case_and_spacing(pool):
    row = ledger_view(pool.record_correction(pack="urs", **TAIWAN, phrase="S$600m  loss"))
    assert row["id"] == "COR-urs-001" and row["recorded_on"] == row["recorded_on"][:10]
    assert retired_in("The opportunity s$600m LOSS remains.", [row]) == [row]
    assert retired_in("The opportunity loss is S$645m.", [row]) == []
    assert retired_in("Anything", [ledger_view(pool.record_correction(pack="urs", **TAIWAN))]) == [], "no phrase retires nothing"
    assert as_admitted(pool.corrections("urs")[1])["text"].endswith("(source: Item 5 final v2).")


def test_the_scorecard_flags_a_report_that_repeats_a_retired_phrase():
    from grit.report.scorecard import scorecard

    brief = {"bottom_line": {"text": "Taiwan losses could reach S$600m.", "escalation": "Finance should confirm."}, "v2": {}}
    ledger = [{"id": "COR-urs-001", "phrase": "S$600m", "corrected": "S$645m"}]
    result = scorecard(brief, ledger)
    assert result["metrics"]["retired_phrases_used"] == ["S$600m"] and not result["checks"]["no phrase the corrections ledger retired"]
    assert scorecard(brief)["checks"]["no phrase the corrections ledger retired"], "default: the ledger the brief was built with"
    assert scorecard({**brief, "corrections": ledger})["metrics"]["open_corrections"] == 1
