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


def test_a_command_without_a_colon_keeps_the_titles_distinctive_terms() -> None:
    from grit.core.routing import match_request

    draft = parse_command("/concern Bugs in PHP, Node.js and GitLab.")
    assert draft == {"kind": "CONCERN", "title": "Bugs in PHP, Node.js and GitLab", "keywords": ["PHP", "Node.js", "GitLab"]}
    assert parse_command("/concern PHP flaws: PHP2Shell, CVE-2026-109618.")["keywords"] == ["PHP2Shell", "CVE-2026-109618"], "trailing punctuation is dropped"
    headline = {"title": "CVE-2026-109618 and CVE-2026-109619 (PHP2Shell): Patch PHP Now", "text": ""}
    request = {"request_id": "MR-1", "status": "ACTIVE", "keywords": draft["keywords"]}
    assert match_request(headline, [request], ["dsc"]) == ("MR-1", "PHP"), "the request now matches the headlines it was meant to catch"


def test_every_parsed_keyword_can_match_and_none_is_lost() -> None:
    from grit.core.routing import _usable

    cases = {
        "/concern AI chips": ["AI chips"],
        "/concern Q3 outages at Microsoft": ["Microsoft"],
        "/concern Huawei and ZTE export curbs": ["Huawei", "ZTE"],
        "/concern Taiwan blockade:": ["Taiwan"],
        "/keyword platform screen doors": ["platform screen doors"],
    }
    for command, keywords in cases.items():
        draft = parse_command(command)
        assert draft["keywords"] == keywords, command
        assert all(_usable(keyword) for keyword in draft["keywords"]), command
    assert parse_command("/concern .") is None, "a command needs a title"


def test_every_copilot_widget_is_keyed_by_the_reader() -> None:
    import re

    text = (Path(__file__).parents[1] / "grit" / "ui" / "app.py").read_text(encoding="utf-8")
    drawer = text[text.index("def copilot_drawer"):text.index("def _model_parts")]
    keys = re.findall(r'key=(f?"[^"]*")', drawer)
    assert keys and all("profile.profile_id" in key or "message_id" in key for key in keys), keys
