"""The dashboard helpers and the chat commands. No Streamlit, no network. The Co-Pilot is tested in test_copilot.py."""

from __future__ import annotations

from pathlib import Path

from grit.agents.chat import parse_command
import pytest

from grit.ui.view_model import NOT_ESTABLISHED, screen_context, shown, table_html



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


def test_trend_cells_keep_the_word_and_a_matching_colour() -> None:
    rendered = table_html([{"Trend": "Escalating"}, {"Trend": "Easing"}, {"Trend": "Unchanged"}])
    assert "Escalating" in rendered and "#F8D0D0" in rendered
    assert "Easing" in rendered and "#D8F3DC" in rendered
    assert "Unchanged" in rendered and "#EEF2F6" in rendered
    assert "<td class='fit'>" in rendered
    assert "<script>" not in table_html([{"Item": "<script>alert(1)</script>"}])


def test_short_columns_stay_on_one_line_and_narrative_columns_wrap() -> None:
    rendered = table_html([{"Title": "A long title", "Tier": 1, "keywords": "export control"}])
    assert "<th class='wrap'>Title</th>" in rendered and "<td class='wrap'><div>A long title</div></td>" in rendered
    assert "<th class='fit'>Tier</th>" in rendered and "<td class='fit'>1</td>" in rendered
    assert "<td class='wrap'><div>export control</div></td>" in rendered
    assert "grit-table-scroll" in rendered
    text = (Path(__file__).parents[1] / "grit" / "ui" / "app.py").read_text(encoding="utf-8")
    assert "word-break:break-word" not in text
    assert "st.html(body)" in text and "def show_table" in text
    assert "white-space:nowrap" in text and "td.wrap>div" in text and "overflow-x:auto" in text


def test_screen_context_names_the_open_tab_and_cuts_a_long_field() -> None:
    context = screen_context("Signals", [{"Title": "A" * 300}, {"Title": "short"}])
    assert context == {"tab": "Signals", "shown": 2, "records": [{"Title": "A" * 240}, {"Title": "short"}]}
    many = screen_context("Signals", [{"Title": str(number)} for number in range(41)])
    assert many["shown"] == 41 and many["omitted"] == 1 and len(many["records"]) == 40


def test_a_reader_cannot_approve_a_package() -> None:
    from types import SimpleNamespace

    from grit.workflow.packages import review_package

    reader = SimpleNamespace(label="A reader", profile_id="reader", review_role="READER")
    with pytest.raises(PermissionError, match="reader"):
        review_package(None, package_id="PKG", version=1, reviewer=reader, intent="APPROVE")


def test_the_dashboard_uses_the_open_tab_and_gates_approval() -> None:
    text = (Path(__file__).parents[1] / "grit" / "ui" / "app.py").read_text(encoding="utf-8")
    assert "Show Co-Pilot" in text and "Co-Pilot width" in text
    assert "screen=screens.get(active_tab)" in text
    assert 'button("Approve"' in text
    assert '_apply_review(db, profile, row, "APPROVE")' in text
    assert "A reader cannot approve a pack." in text


def test_an_incomplete_reasoned_run_is_listed_with_its_ready_report(tmp_path):
    from types import SimpleNamespace

    from grit.pool import Pool
    from grit.ui.runs import listed_runs, ready_snapshot

    pool = Pool(tmp_path / "pool.sqlite")
    pool.start_run("W", run_date="2026-11-10", pack="ste", kind="READER_SCAN")
    pool.start_run("X", run_date="2026-11-09", pack="ste", kind="READER_SCAN")
    call = pool.insert("weekly_calls", {"run_id": "W", "version": 1, "role": "Critic", "task": "final-wilson", "prompt_version": "t",
                                        "model": "m", "input_hash": "i", "input_json": {}, "output_json": {}, "status": "VALIDATED",
                                        "created_at": "2026-11-10T07:47:00Z"})
    for version, status in ((1, "READY"), (3, "INCOMPLETE")):
        pool.insert("weekly_reports", {"run_id": "W", "version": version, "profile_id": "wilson", "content_hash": f"h{version}",
                                       "selection_hash": "", "critique_call_id": call, "status": status,
                                       "brief_json": {"v": version}, "routed_json": [], "created_at": "2026-11-10T07:47:01Z"})
    reader = SimpleNamespace(profile_id="wilson", brief_for=lambda pack: pack == "ste")
    assert [row["run_id"] for row in listed_runs(pool, reader)] == ["W"]
    assert ready_snapshot(pool, "W", "wilson")["brief"] == {"v": 1}
    other = SimpleNamespace(profile_id="mkteo", brief_for=lambda pack: True)
    assert listed_runs(pool, other) == []
