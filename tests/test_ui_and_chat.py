"""The dashboard helpers and the chat commands. No Streamlit, no network. The Co-Pilot is tested in test_copilot.py."""

from __future__ import annotations

from pathlib import Path

from grit.agents.chat import parse_command
from grit.ui.view_model import NOT_ESTABLISHED, shown



def test_absence_reads_as_not_established() -> None:
    for value in (None, "", [], "NOT_ASSESSED", "INSUFFICIENT_DATA"):
        assert shown(value) == NOT_ESTABLISHED
    assert shown(["A", "B"]) == "A, B"


def test_chat_command_becomes_a_request_draft() -> None:
    assert parse_command("/concern Taiwan blockade: blockade of Taiwan, quarantine of Taiwan") == {"kind": "CONCERN", "title": "Taiwan blockade", "keywords": ["blockade of Taiwan", "quarantine of Taiwan"]}
    assert parse_command("/keyword platform screen doors") == {"kind": "KEYWORD", "title": "platform screen doors", "keywords": ["platform screen doors"]}
    assert parse_command("what moved this week?") is None


def test_screen_modules_do_not_write_the_ledger() -> None:
    root = Path(__file__).parents[1] / "grit" / "ui"
    for name in ("actions.py", "app.py", "view_model.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "ledger.append" not in text and "WorkflowRow(" not in text and "append_workflow_row" not in text
    assert "review_package" in (root / "actions.py").read_text(encoding="utf-8")
