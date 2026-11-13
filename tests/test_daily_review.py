"""The daily two-stage review (spec 48): the clock, the deadlines, approved-only packs, and the memory loop. No network."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from grit.pool import Pool
from grit.profiles import Profile
from grit.review import sharepoint
from grit.review.argue import argue, argue_batch
from grit.review.batch import candidates, decisions, items
from grit.review.config import BP, MANAGER, batch_id, load_review_config
from grit.review.cycle import Services, tick
from grit.review.graph import GraphClient, GraphError
from grit.review.pack import FINAL, build_pack

SGT = ZoneInfo("Asia/Singapore")
FRIDAY, MONDAY = date(2026, 11, 13), date(2026, 11, 16)
PAGE = "Floods closed the depot on Tuesday. The operator said 40 buses were moved to a second site while crews pumped water out."


def _config(tmp_path: Path, **overrides) -> Path:
    raw = {
        "pack": "urs", "businessArea": "Urban Solutions", "timezone": "Asia/Singapore",
        "stages": {
            "BP": {"profileId": "bp", "displayName": "B P", "role": "R&A BP", "listId": "list-bp", "publishAt": "08:30", "mailAt": "09:00", "closeAt": "12:00"},
            "MANAGER": {"profileId": "mgr", "displayName": "M G", "role": "Head", "listId": "list-mgr", "mailAt": "13:00", "closeAt": "17:00"},
        },
        "argueAt": "06:30", "finalMailAt": "18:00", "maxSignals": 60, "holidays": ["2026-11-09"],
    }
    raw.update(overrides)
    folder = tmp_path / "review"
    folder.mkdir(exist_ok=True)
    (folder / "urs.json").write_text(json.dumps(raw), encoding="utf-8")
    return folder


def _profile(profile_id: str, email: str | None = None) -> Profile:
    return Profile(profile_id=profile_id, name=profile_id, role="r", aperture="MATERIAL_APERTURE", packs=("urs",), business_scope={},
                   review_role="REVIEWER", red_alerts={}, email=email, briefs={}, raw={"signalReview": True})


def _signal(pool: Pool, day: date, n: int, *, tier: int = 3, red: int = 0, publisher: str = "straitstimes.com") -> str:
    run_id = f"{day.isoformat()}-urs-daily"
    if pool.run(run_id) is None:
        pool.start_run(run_id, run_date=day.isoformat(), pack="urs", kind="DAILY")
        pool.finish_run(run_id)
    signal_id = pool.add_signal(url=f"https://example.com/{day}/{n}", title=f"Depot flood {n}", text=PAGE, publisher=publisher,
                                published_at=f"{day.isoformat()}T0{n % 10}:00:00Z", lane="FEED", content_hash=f"h-{day}-{n}")
    pool.save_assessment({"signal_id": signal_id, "run_id": run_id, "pack": "urs", "decision": "KEEP", "tier": tier, "red": red})
    return signal_id


class FakeGemini:
    """Answers the Proposer and the Critic in turn from a script of JSON replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.models = self

    def generate_content(self, **_kwargs):
        reply = self.replies.pop(0) if self.replies else {"case_for": "x"}
        return type("R", (), {"text": json.dumps(reply)})()


GOOD_FOR = {"case_for": "Floods closed a depot and buses were moved.", "quotes": ["The operator said 40 buses were moved to a second site"]}
GOOD_AGAINST = {"case_against": "A one-day closure with no lasting damage.", "view": "WEAK", "quotes": []}


class FakeGraph:
    def __init__(self, *, fail_create: set[str] | None = None):
        self.created: list[tuple[str, dict]] = []
        self.rows: dict[str, dict] = {}
        self.patched: list[tuple[str, str, dict]] = []
        self.mail: list[tuple] = []
        self.fail_create = fail_create or set()
        self.fail_get = False
        self.history: dict[str, list[dict]] = {}
        self.lost_answer: set[str] = set()  # the row is created, but the answer never arrives

    def create_item(self, list_id, fields):
        if fields["SignalId"] in self.fail_create:
            raise GraphError("HTTP 503")
        item_id = f"{list_id}-{len(self.created) + 1}"
        self.created.append((list_id, dict(fields)))
        self.rows[item_id] = {"fields": dict(fields), "lastModifiedDateTime": "2026-11-13T00:30:00Z"}
        self.history[item_id] = [{"fields": dict(fields), "lastModifiedDateTime": "2026-11-13T00:30:00Z"}]
        if fields["SignalId"] in self.lost_answer:
            raise GraphError("Graph POST failed: ReadTimeout", None)
        return item_id

    def get_item(self, _list_id, item_id):
        if self.fail_get:
            raise GraphError("HTTP 503", 503)
        return self.rows[item_id]

    def item_versions(self, _list_id, item_id):
        return list(reversed(self.history.get(item_id, [])))

    def find_item(self, list_id, signal_id, review_date):
        return next((item_id for item_id, row in self.rows.items() if row["fields"]["SignalId"] == signal_id and item_id.startswith(list_id)), None)

    def update_fields(self, list_id, item_id, fields):
        self.patched.append((list_id, item_id, fields))

    def send_mail(self, to, subject, html, attachments):
        self.mail.append((tuple(to), subject, [a.name for a in attachments]))
        return "INLINE"

    def decide(self, signal_id, decision, at, *, modified=None, decided_at=True):
        for item_id, row in self.rows.items():
            if row["fields"]["SignalId"] == signal_id:
                row["fields"].update({"Decision": decision, **({"DecidedAt": at} if decided_at else {})})
                row["lastModifiedDateTime"] = modified or at
                self.history[item_id].append({"fields": dict(row["fields"]), "lastModifiedDateTime": modified or at})


def _services(graph=None, gemini=None):
    return Services(profiles={"bp": _profile("bp", "bp@example.com"), "mgr": _profile("mgr", "mgr@example.com")}, graph=graph, gemini=gemini)


def _at(day: date, clock: str) -> datetime:
    hours, minutes = map(int, clock.split(":"))
    return datetime(day.year, day.month, day.day, hours, minutes, tzinfo=SGT)


# The clock ------------------------------------------------------------------------------------------------------

def test_monday_covers_the_weekend_and_the_day_after_a_holiday_covers_it(tmp_path) -> None:
    config = load_review_config("urs", _config(tmp_path))
    assert config.covered_days(MONDAY) == [date(2026, 11, 14), date(2026, 11, 15), MONDAY]
    assert config.covered_days(date(2026, 11, 10)) == [date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 10)]
    assert not config.working_day(date(2026, 11, 9))


def test_times_out_of_order_are_refused(tmp_path) -> None:
    folder = _config(tmp_path, finalMailAt="16:00")
    with pytest.raises(ValueError, match="times must run"):
        load_review_config("urs", folder)


def test_cap_never_drops_a_p1_or_red_signal_and_a_published_signal_is_not_published_again(tmp_path) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    config = load_review_config("urs", _config(tmp_path, maxSignals=2))
    ordinary = [_signal(pool, FRIDAY, n) for n in range(3)]
    red = _signal(pool, FRIDAY, 7, tier=2, red=1)
    p1 = _signal(pool, FRIDAY, 8, tier=1)
    published, overflow = candidates(pool, config, FRIDAY)
    assert {row["signal_id"] for row in published} == {red, p1} and {row["signal_id"] for row in overflow} == set(ordinary)
    pool.insert("review_items", {"batch_id": batch_id(FRIDAY, "urs"), "signal_id": p1, "stage": BP, "position": 1})
    _signal(pool, MONDAY, 8, tier=1)  # the same page, kept again on Monday
    assert p1 not in {row["signal_id"] for row in candidates(pool, config, MONDAY)[0]}


# Gemini ---------------------------------------------------------------------------------------------------------

def test_an_argued_signal_has_both_cases_and_a_quote_from_the_page() -> None:
    record = {"title": "Depot flood", "text": PAGE, "tier": 3}
    result = argue(FakeGemini([GOOD_FOR, GOOD_AGAINST]), "m", record, "Urban Solutions", {})
    assert result["status"] == "ARGUED" and result["critic_view"] == "WEAK" and result["quotes"]


def test_an_invented_quote_or_number_is_withheld_after_one_retry() -> None:
    bad = {"case_for": "Floods moved 400 buses.", "quotes": ["This sentence is not on the page at all."]}
    result = argue(FakeGemini([bad, bad]), "m", {"title": "Depot flood", "text": PAGE}, "Urban Solutions", {})
    assert result["status"] == "WITHHELD"
    assert any(p.startswith("NOT_VERBATIM") for p in result["problems"]) and any("400" in p for p in result["problems"])


def test_without_gemini_every_signal_still_goes_to_the_reviewer(tmp_path) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    config = load_review_config("urs", _config(tmp_path))
    _signal(pool, FRIDAY, 1)
    outcome = argue_batch(pool, config, FRIDAY, client=None, model="m")
    assert outcome["NOT_CONFIGURED"] == 1
    assert len(sharepoint.stage_rows(pool, config, FRIDAY, BP)) == 1


# SharePoint -----------------------------------------------------------------------------------------------------

def _argued(tmp_path, count=3):
    pool = Pool(tmp_path / "grit.sqlite")
    config = load_review_config("urs", _config(tmp_path))
    ids = [_signal(pool, FRIDAY, n) for n in range(count)]
    argue_batch(pool, config, FRIDAY, client=None, model="m")
    return pool, config, ids


def test_publishing_twice_sends_each_row_once_and_a_failed_row_is_retried(tmp_path) -> None:
    pool, config, ids = _argued(tmp_path)
    graph = FakeGraph(fail_create={ids[1]})
    first = sharepoint.publish(pool, config, FRIDAY, BP, _profile("bp"), client=graph)
    assert first["status"] == "FAILED" and first["sent"] == 2
    graph.fail_create.clear()
    second = sharepoint.publish(pool, config, FRIDAY, BP, _profile("bp"), client=graph)
    assert second == {"status": "DONE", "rows": 3, "sent": 1, "failed": 0}
    assert sorted(fields["SignalId"] for _list, fields in graph.created) == sorted(ids)


def test_a_late_decision_does_not_count_and_silence_is_not_feedback(tmp_path) -> None:
    pool, config, ids = _argued(tmp_path)
    graph = FakeGraph()
    sharepoint.publish(pool, config, FRIDAY, BP, _profile("bp"), client=graph)
    graph.decide(ids[0], "Approved", "2026-11-13T03:59:00Z")  # 11:59 SGT
    graph.decide(ids[1], "Rejected", "2026-11-13T04:01:00Z")  # 12:01 SGT, after the deadline
    outcome = sharepoint.close(pool, config, FRIDAY, BP, client=graph)
    assert (outcome["approve"], outcome["reject"], outcome["no_decision"]) == (1, 0, 2)
    assert {row["signal_id"]: row["decision"] for row in pool.feedback("bp")} == {ids[0]: "APPROVE"}
    assert len(graph.patched) == 3 and all(fields == {"Locked": True} for _l, _i, fields in graph.patched)
    graph.decide(ids[2], "Approved", "2026-11-13T03:00:00Z")  # a change after close is ignored
    sharepoint.collect(pool, config, FRIDAY, BP, client=graph)
    assert decisions(pool, batch_id(FRIDAY, "urs"), BP)[ids[2]]["decision"] == "NO_DECISION"


def test_the_manager_sees_only_bp_approvals_and_the_final_pack_only_double_approvals(tmp_path) -> None:
    pool, config, ids = _argued(tmp_path)
    graph = FakeGraph()
    sharepoint.publish(pool, config, FRIDAY, BP, _profile("bp"), client=graph)
    graph.decide(ids[0], "Approved", "2026-11-13T02:00:00Z")
    graph.decide(ids[1], "Approved", "2026-11-13T02:00:00Z")
    graph.decide(ids[2], "Rejected", "2026-11-13T02:00:00Z")
    sharepoint.close(pool, config, FRIDAY, BP, client=graph)
    sharepoint.publish(pool, config, FRIDAY, MANAGER, _profile("mgr"), client=graph)
    manager_rows = [fields for list_id, fields in graph.created if list_id == "list-mgr"]
    assert {fields["SignalId"] for fields in manager_rows} == {ids[0], ids[1]} and manager_rows[0]["BpDecision"].startswith("Approved")
    graph.decide(ids[1], "Rejected", "2026-11-13T07:00:00Z")
    graph.decide(ids[0], "Approved", "2026-11-13T07:00:00Z")
    sharepoint.close(pool, config, FRIDAY, MANAGER, client=graph)
    final = build_pack(pool, config, FRIDAY, FINAL)
    assert [row.signal_id for row in final.rows] == [ids[0]] and final.approved_hash
    assert pool.latest_decisions("mgr") == {("mgr", ids[0]): "APPROVE", ("mgr", ids[1]): "REJECT"}


def test_memory_moves_an_outlet_the_reviewer_rejected_down_within_its_tier_and_hides_nothing(tmp_path) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    old = _signal(pool, date(2026, 11, 12), 1, publisher="tabloid.example")
    pool.add_feedback("bp", old, "REJECT")
    paper = _signal(pool, FRIDAY, 2, publisher="straitstimes.com")
    tabloid = _signal(pool, FRIDAY, 3, publisher="tabloid.example")  # newer, so first without memory
    rows = pool.assessments(f"{FRIDAY}-urs-daily", kept_only=True)
    assert [row["signal_id"] for row in rows] == [tabloid, paper]
    ordered = sharepoint.ordered_for(pool, _profile("bp"), rows)
    assert [row["signal_id"] for row, _note in ordered] == [paper, tabloid]
    assert "outlet:tabloid.example (-1)" in ordered[1][1]


# The clock end to end -------------------------------------------------------------------------------------------

def test_one_working_day_runs_in_order_and_mails_three_packs(tmp_path, monkeypatch) -> None:
    from grit import settings

    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "out")
    pool, config, ids = _argued(tmp_path, count=2)
    pool.conn.execute("DELETE FROM review_arguments")
    pool.conn.commit()
    graph = FakeGraph()
    services = _services(graph=graph, gemini=FakeGemini([GOOD_FOR, GOOD_AGAINST] * 2))
    assert tick(pool, config, FRIDAY, _at(FRIDAY, "06:00"), services, say=lambda _t: None) == []
    ran = tick(pool, config, FRIDAY, _at(FRIDAY, "09:05"), services, say=lambda _t: None)
    assert ran == [("ARGUE", "DONE"), ("PUBLISH_BP", "DONE"), ("MAIL_BP", "DONE")]
    graph.decide(ids[0], "Approved", "2026-11-13T02:00:00Z")
    tick(pool, config, FRIDAY, _at(FRIDAY, "13:00"), services, say=lambda _t: None)
    graph.decide(ids[0], "Approved", "2026-11-13T06:00:00Z")
    tick(pool, config, FRIDAY, _at(FRIDAY, "18:00"), services, say=lambda _t: None)
    assert [mail[0] for mail in graph.mail] == [("bp@example.com",), ("mgr@example.com",), ("bp@example.com", "mgr@example.com")]
    assert all(len(mail[2]) == 3 for mail in graph.mail)
    assert "1 signal(s) approved by both" in graph.mail[-1][1]
    assert len(items(pool, batch_id(FRIDAY, "urs"), BP)) == 2
    assert tick(pool, config, date(2026, 11, 14), _at(date(2026, 11, 14), "09:00"), services, say=lambda _t: None) == []


def test_a_graph_token_failure_is_retried_not_skipped(tmp_path) -> None:
    pool, config, _ids = _argued(tmp_path, count=1)
    services = _services()
    services.graph_failed, services.graph_error = True, "Token acquisition failed: invalid_client"
    ran = tick(pool, config, FRIDAY, _at(FRIDAY, "08:45"), services, say=lambda _t: None)
    assert ran == [("ARGUE", "DONE"), ("PUBLISH_BP", "FAILED")]


# Graph ----------------------------------------------------------------------------------------------------------

def test_graph_retries_a_throttled_call_and_sends_large_files_through_an_upload_session() -> None:
    from grit.review.graph import INLINE_ATTACHMENTS_MAX, UPLOAD_CHUNK, Attachment

    calls = []
    replies = iter([(429, None, {"Retry-After": "1"}), (201, {"id": "item-1"}, {})])

    def transport(method, url, *, headers, json=None, data=None, timeout=60):
        calls.append((method, url, "Authorization" in headers))
        if "/items" in url:
            return next(replies)
        if url.endswith("/messages"):
            return 201, {"id": "msg-1"}, {}
        if "createUploadSession" in url:
            return 201, {"uploadUrl": "https://upload.example/session"}, {}
        return (201 if method == "PUT" else 202), None, {}

    client = GraphClient("token", site_id="site", sender="grit@example.com", transport=transport, sleep=lambda _s: None)
    assert client.create_item("list", {"Title": "x"}) == "item-1" and len(calls) == 2
    calls.clear()
    big = Attachment("deck.pptx", b"x" * (INLINE_ATTACHMENTS_MAX + 1))
    assert client.send_mail(["a@example.com"], "s", "<p>b</p>", [big]) == "UPLOAD_SESSION"
    puts = [call for call in calls if call[0] == "PUT"]
    assert len(puts) == -(-len(big.content) // UPLOAD_CHUNK) and not any(auth for _m, _u, auth in puts)
    assert calls[-1][1].endswith("/messages/msg-1/send")


def test_a_daily_reviewer_profile_turns_memory_on() -> None:
    assert _profile("bp").signal_buttons
    assert not Profile(profile_id="x", name=None, role="r", aperture="MATERIAL_APERTURE", packs=("urs",), business_scope={},
                       review_role="READER", red_alerts={}, email=None, briefs={}).signal_buttons


# What the review of the first build found ------------------------------------------------------------------------

def test_a_graph_error_at_close_does_not_turn_a_decision_into_no_decision(tmp_path) -> None:
    pool, config, ids = _argued(tmp_path)
    graph = FakeGraph()
    sharepoint.publish(pool, config, FRIDAY, BP, _profile("bp"), client=graph)
    graph.decide(ids[0], "Approved", "2026-11-13T03:00:00Z")
    graph.fail_get = True
    assert sharepoint.close(pool, config, FRIDAY, BP, client=graph)["status"] == "FAILED"
    assert decisions(pool, batch_id(FRIDAY, "urs"), BP) == {} and graph.patched == []
    graph.fail_get = False
    assert sharepoint.close(pool, config, FRIDAY, BP, client=graph)["approve"] == 1


def test_a_change_after_the_deadline_is_read_as_the_row_stood_at_the_deadline(tmp_path) -> None:
    pool, config, ids = _argued(tmp_path)
    graph = FakeGraph()
    sharepoint.publish(pool, config, FRIDAY, BP, _profile("bp"), client=graph)
    graph.decide(ids[0], "Approved", "2026-11-13T03:00:00Z")  # 11:00 SGT
    graph.decide(ids[0], "Pending", None, modified="2026-11-13T04:10:00Z", decided_at=False)  # 12:10, set back
    graph.decide(ids[1], "Approved", "2026-11-13T03:00:00Z")
    graph.decide(ids[1], "Rejected", None, modified="2026-11-13T04:10:00Z", decided_at=False)  # 12:10, edit form keeps DecidedAt
    outcome = sharepoint.close(pool, config, FRIDAY, BP, client=graph)
    found = decisions(pool, batch_id(FRIDAY, "urs"), BP)
    assert (found[ids[0]]["decision"], found[ids[1]]["decision"]) == ("APPROVE", "APPROVE") and outcome["approve"] == 2


def test_a_create_with_no_answer_is_looked_up_and_not_created_twice(tmp_path) -> None:
    pool, config, ids = _argued(tmp_path, count=1)
    graph = FakeGraph()
    graph.lost_answer.add(ids[0])
    outcome = sharepoint.publish(pool, config, FRIDAY, BP, _profile("bp"), client=graph)
    assert outcome["status"] == "DONE" and len(graph.created) == 1
    assert items(pool, batch_id(FRIDAY, "urs"), BP)[0]["list_item_id"] == "list-bp-1"


def test_a_server_error_on_a_post_is_not_retried_and_an_unclear_mail_is_not_sent_again(tmp_path, monkeypatch) -> None:
    from grit import settings
    from grit.review.mail import mail_pack

    calls = []

    def transport(method, url, *, headers, json=None, data=None, timeout=60):
        calls.append(method)
        return 503, None, {}

    client = GraphClient("token", site_id="site", sender="grit@example.com", transport=transport, sleep=lambda _s: None)
    with pytest.raises(GraphError) as caught:
        client.send_mail(["a@example.com"], "s", "<p>b</p>")
    assert calls == ["POST"] and caught.value.uncertain
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "out")
    pool, config, _ids = _argued(tmp_path, count=1)
    sharepoint.publish(pool, config, FRIDAY, BP, _profile("bp"), client=None)
    services = _services()
    assert mail_pack(pool, config, FRIDAY, BP, services.profiles, client=client)["status"] == "UNCERTAIN"


def test_the_dashboard_panel_shows_only_the_readers_own_stage(tmp_path, monkeypatch) -> None:
    from grit import settings
    from grit.ui.daily_review import panel_data

    pool, config, ids = _argued(tmp_path)
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    graph = FakeGraph()
    sharepoint.publish(pool, config, FRIDAY, BP, _profile("bp"), client=graph)
    graph.decide(ids[0], "Approved", "2026-11-13T02:00:00Z")
    sharepoint.close(pool, config, FRIDAY, BP, client=graph)
    [entry] = panel_data(pool, _profile("bp"), now=_at(FRIDAY, "12:30"))
    assert (entry["stage"], entry["rows"], entry["decisions"]) == (BP, 3, {"APPROVE": 1, "NO_DECISION": 2})
    assert entry["learning"]["approved"] == 1 and entry["learning"]["no_decision"] == 2
    assert panel_data(pool, _profile("someone-else"), now=_at(FRIDAY, "12:30")) == []
