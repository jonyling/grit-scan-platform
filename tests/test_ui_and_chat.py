"""The dashboard helpers and the chat agent. No Streamlit, no network."""

from __future__ import annotations

from pathlib import Path

from grit.agents.chat import answer_question, check_answer, parse_command, retrieve
from grit.pipeline.reader_scan import stub_client
from grit.ui.view_model import NOT_ESTABLISHED, shown

ROWS = [
    {"signal_id": "SIG-a", "title": "PLA aircraft near Taiwan Strait", "text": "Taiwan counted 25 aircraft.", "publisher": "focustaiwan.tw", "url": "https://focustaiwan.tw/a"},
    {"signal_id": "SIG-b", "title": "Fed raises rates", "text": "The Federal Reserve raised rates.", "publisher": "reuters.com", "url": "https://reuters.com/b"},
]


def test_absence_reads_as_not_established() -> None:
    for value in (None, "", [], "NOT_ASSESSED", "INSUFFICIENT_DATA"):
        assert shown(value) == NOT_ESTABLISHED
    assert shown(["A", "B"]) == "A, B"


def test_chat_command_becomes_a_request_draft() -> None:
    assert parse_command("/concern Taiwan blockade: blockade of Taiwan, quarantine of Taiwan") == {"kind": "CONCERN", "title": "Taiwan blockade", "keywords": ["blockade of Taiwan", "quarantine of Taiwan"]}
    assert parse_command("/keyword platform screen doors") == {"kind": "KEYWORD", "title": "platform screen doors", "keywords": ["platform screen doors"]}
    assert parse_command("what moved this week?") is None


def test_retrieval_falls_back_to_word_overlap() -> None:
    assert retrieve("What did the Federal Reserve do?", ROWS)[0]["signal_id"] == "SIG-b"


def test_chat_answer_cites_only_supplied_records() -> None:
    assert check_answer({"answer": "Rates rose [S1]."}, {"S1"}) == []
    assert any(item.startswith("UNKNOWN_SOURCE") for item in check_answer({"answer": "Rates rose [S9]."}, {"S1"}))
    assert any(item.startswith("BANNED_TERM") for item in check_answer({"answer": "The severity is high [S1]."}, {"S1"}))

    client = stub_client(lambda _system, payload: {"answer": "The Federal Reserve raised rates [S1].", "source_ids": ["S1"]})
    result = answer_question("What did the Federal Reserve do?", ROWS, client=client, model="stub", profile_label="Wilson")
    assert result["status"] == "VALIDATED"
    assert result["citations"][0]["url"] == "https://reuters.com/b"
    bad = stub_client(lambda _system, payload: {"answer": "See https://example.com [S1]"})
    assert answer_question("Fed?", ROWS, client=bad, model="stub", profile_label="Wilson")["status"] == "WITHHELD"


def test_screen_modules_do_not_write_the_ledger() -> None:
    root = Path(__file__).parents[1] / "grit" / "ui"
    for name in ("actions.py", "app.py", "view_model.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "ledger.append" not in text and "WorkflowRow(" not in text and "append_workflow_row" not in text
    assert "review_package" in (root / "actions.py").read_text(encoding="utf-8")
