"""The daily lane, the nightly index and whats_new (spec 42), offline: fixture feeds, a stub Currents, a fake embedder."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from grit.daily.feeds import FEEDS, Feed, active, read_feed
from grit.daily.index import build_digest, cluster_events, digests, nightly, retain, write_digests
from grit.daily.lane import daily_run_id, queue_kept, run_daily
from grit.pipeline import reader_scan
from grit.pool import Pool

DAY = date.today()


def _rss(items: list[tuple[str, str, str | None, str]]) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link>{f'<pubDate>{when}</pubDate>' if when else ''}<description>{text}</description></item>"
        for title, link, when, text in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture</title>{body}</channel></rss>'.encode()


def _when(days_ago: int) -> str:
    return (datetime.combine(DAY, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=days_ago) + timedelta(hours=3)).strftime("%a, %d %b %Y %H:%M:%S +0000")


FEED = Feed("fixture", "Fixture feed", "https://feeds.example/rss", ("urs-tw",), "test")
ITEMS = [
    ("Taiwan reports 30 PLA aircraft near Taiwan Strait", "https://focustaiwan.tw/cross-strait/daily-1/?utm_source=rss", _when(1), "Taiwan's defence ministry said 30 PLA aircraft and 9 ships operated around Taiwan."),
    ("A recipe for mooncakes", "https://food.example/mooncakes", _when(0), "<p>Bake at <b>180C</b>.</p>"),
    ("Old strait story", "https://focustaiwan.tw/old", _when(20), "PLA aircraft near Taiwan."),
    ("Undated strait story", "https://focustaiwan.tw/undated", None, "PLA aircraft near Taiwan."),
]
CURRENTS = {"status": "ok", "news": [{"title": "Taichung MRT Blue Line BC21 tender stays open until 12 October", "url": "https://www.taipeitimes.com/News/taiwan/archives/daily-blue",
                                      "description": "The Taichung MRT Blue Line civil package BC21, worth NT$13.9bn, closes bids on 12 October.", "published": f"{DAY.isoformat()} 01:00:00 +0000"}]}


def test_the_register_keeps_every_source_with_its_status_and_basis() -> None:
    assert all(feed.basis for feed in FEEDS) and {feed.status for feed in FEEDS} == {"ACTIVE", "STALE", "BLOCKED"}
    assert all(feed.url for feed in active()) and not any(feed.status != "ACTIVE" for feed in active())
    assert {feed.feed_id for feed in active(["dsc"])} < {feed.feed_id for feed in active(["ste"])}


def test_read_feed_keeps_dated_recent_items_with_clean_text_and_canonical_urls() -> None:
    items = read_feed(FEED, since=datetime.combine(DAY - timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc), getter=lambda _url: _rss(ITEMS))
    assert [item["title"] for item in items] == ["A recipe for mooncakes", "Taiwan reports 30 PLA aircraft near Taiwan Strait"], "old and undated items are skipped"
    assert items[1]["url"] == "https://focustaiwan.tw/cross-strait/daily-1" and items[1]["publisher"] == "focustaiwan.tw"
    assert items[0]["text"] == "Bake at 180C ." and items[1]["published_at"].endswith("Z")


class _Embedder:
    model, dimensions, batch_size = "gemini-embedding-001", 768, 50

    def embed(self, texts: list[str], *, task: str) -> list[list[float]]:
        out = []
        for text in texts:
            vector = [0.0] * 768
            vector[1 if "PLA" in text else 2 if "Blue Line" in text else (hash(text) % 700) + 10] = 1.0
            out.append(vector)
        return out


def _daily(tmp_path: Path, monkeypatch, pool: Pool | None = None) -> tuple[Pool, object]:
    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    pool = pool or Pool(tmp_path / "grit.sqlite")
    result = run_daily(pool, "urs-tw", DAY, feeds=[FEED], feed_getter=lambda _url: _rss(ITEMS), currents_getter=lambda _url, _params: CURRENTS,
                       embedder=_Embedder(), score_harm_fn=lambda *a, **k: {"status": "NOT_RUN", "reason": "test"}, log=lambda _m: None)
    return pool, result


def test_the_daily_run_assesses_by_the_same_rules_and_queues_what_it_keeps(tmp_path: Path, monkeypatch) -> None:
    pool, result = _daily(tmp_path, monkeypatch)
    run = pool.run(daily_run_id(DAY, "urs-tw"))
    assert run["kind"] == "DAILY" and run["status"] == "COMPLETE"
    decided = {row["title"]: row for row in pool.assessments(run["run_id"])}
    assert decided["Taiwan reports 30 PLA aircraft near Taiwan Strait"]["decision"] == "KEEP"
    assert decided["A recipe for mooncakes"]["decision"] == "DROP"
    assert decided["Taichung MRT Blue Line BC21 tender stays open until 12 October"]["rung"] == "DIRECT", "Currents results get the scan's link ladder"
    assert result.feeds == {"fixture": 2} and result.currents["sent"] >= 1 and result.kept == 2 and result.queued == 2
    queued = pool.rows("SELECT added_by, status FROM pool_additions")
    assert len(queued) == 2 and {row["added_by"] for row in queued} == {"daily-lane"}
    again = run_daily(pool, "urs-tw", DAY, feeds=[FEED], feed_getter=lambda _url: _rss(ITEMS), currents_getter=lambda _url, _params: pytest.fail("a query is sent once a day"),
                      embedder=_Embedder(), score_harm_fn=lambda *a, **k: {"status": "NOT_RUN"}, log=lambda _m: None)
    assert again.new_signals == 0 and again.queued == 0, "a second pass on the same day adds nothing new"


def test_weekly_code_and_the_dashboard_ignore_daily_runs(tmp_path: Path, monkeypatch) -> None:
    from grit.copilot.scope import scope_for
    from grit.copilot.tools import Toolbox
    from test_copilot import LABELS, _profile

    pool, _result = _daily(tmp_path, monkeypatch)
    assert pool.latest_run("urs-tw", kind="READER_SCAN") is None
    assert "No completed run" in Toolbox(pool, scope_for(_profile(), today=DAY, labels=LABELS)).call("get_state", {"pack": "urs-tw"})["error"]
    assert pool.rows("SELECT run_id FROM runs WHERE status = 'COMPLETE' AND kind != 'DAILY'") == []
    events = Toolbox(pool, scope_for(_profile(), today=DAY, labels=LABELS)).call("search_signals", {"query": "PLA aircraft Taiwan Strait"})["events"]
    assert events and events[0]["title"].startswith("Taiwan reports 30"), "the Co-Pilot searches daily pages"


def test_the_next_weekly_scan_takes_the_daily_pages(tmp_path: Path, monkeypatch) -> None:
    from grit.acquisition.brave_plan import BraveResponse

    pool, _result = _daily(tmp_path, monkeypatch)
    week = reader_scan.new_run(pool, DAY + timedelta(days=1), "urs-tw")
    found = reader_scan.acquire(pool, week, [], getter=lambda _u, _p: BraveResponse(200, {"results": []}, {}), usage_path=tmp_path / "u.json", sleep=lambda _s: None, log=lambda _m: None)
    assert found["added_from_copilot"] == 2
    assert {row["lane"] for row in pool.rows("SELECT lane FROM sightings WHERE run_id = ?", (week.run_id,))} == {"DAILY"}


def test_events_join_the_same_story_and_digests_report_new_tier_1_2_events(tmp_path: Path, monkeypatch) -> None:
    from grit.copilot.scope import scope_for
    from grit.copilot.tools import Toolbox
    from test_copilot import LABELS, _profile

    pool, _result = _daily(tmp_path, monkeypatch)
    strait = pool.one("SELECT signal_id FROM signals WHERE title LIKE 'Taiwan reports 30%'")["signal_id"]
    copy = pool.add_signal(url="https://other.example/strait", title="Taiwan counts 30 PLA aircraft around the island", text="", publisher="other.example",
                           published_at=f"{DAY.isoformat()}T02:00:00Z", lane="FEED", content_hash="c")
    pool.put_vector(copy, model="gemini-embedding-001", task="RETRIEVAL_DOCUMENT", dimensions=768, text_hash="c", vector=[0.0, 1.0] + [0.0] * 766)
    run_id = daily_run_id(DAY, "urs-tw")
    pool.save_assessment({"signal_id": copy, "run_id": run_id, "pack": "urs-tw", "decision": "KEEP", "tier": 2})
    with pool.transaction() as conn:
        conn.execute("UPDATE assessments SET tier = 2 WHERE signal_id = ?", (strait,))
    clustered = cluster_events(pool)
    assert clustered == {"joined": 1, "new": 2} and cluster_events(pool) == {"joined": 0, "new": 0}, "safe to repeat"
    event = pool.one("SELECT * FROM events WHERE members = 2")
    assert event["lead_signal_id"] in {strait, copy}
    digest = build_digest(pool, "urs-tw", DAY)
    top = digest["tier_1_2_events"]
    assert len(top) == 1 and top[0]["outlets"] == 2 and digest["runs"] == [run_id]
    write_digests(pool, DAY, ["urs-tw"])
    assert digests(pool, ["urs-tw"], DAY)[0]["tier_1_2_events"][0]["outlets"] == 2
    news = Toolbox(pool, scope_for(_profile(), today=DAY, labels=LABELS)).call("whats_new", {})
    day = news["days"][0]
    assert day["id"] == "B1" and day["tier_1_2_events"][0]["id"] == "S1" and day["label"] == "URS Taiwan"
    assert "1 new tier 1 or 2 events" in day["summary"], "the digest is a citable source, so its counts can be cited"
    assert "outside" in Toolbox(pool, scope_for(_profile(), today=DAY, labels=LABELS)).call("whats_new", {"pack": "urs"})["error"]
    later = build_digest(pool, "urs-tw", DAY + timedelta(days=1))
    assert later["tier_1_2_events"] == [], "a page kept on an earlier day is not new again"


def test_retention_trims_old_dropped_pages_only(tmp_path: Path, monkeypatch) -> None:
    pool, _result = _daily(tmp_path, monkeypatch)
    old = (DAY - timedelta(days=120)).isoformat() + "T00:00:00Z"
    with pool.transaction() as conn:
        conn.execute("UPDATE signals SET text = ?, first_seen_at = ?", ("x" * 2000, old))
    assert retain(pool, DAY) == 1, "only the dropped mooncake page"
    lengths = {row["title"]: len(row["text"]) for row in pool.rows("SELECT title, text FROM signals")}
    assert lengths["A recipe for mooncakes"] == 500 and lengths["Taiwan reports 30 PLA aircraft near Taiwan Strait"] == 2000
    assert retain(pool, DAY) == 0
    result = nightly(pool, DAY, packs=["urs-tw"])
    assert set(result) == {"fts_added", "events", "digests", "retained"}


def test_queue_kept_skips_pages_already_queued(tmp_path: Path, monkeypatch) -> None:
    pool, _result = _daily(tmp_path, monkeypatch)
    assert queue_kept(pool, daily_run_id(DAY, "urs-tw"), "urs-tw") == 0


def test_a_red_page_is_drafted_once_per_pack_across_runs(tmp_path: Path, monkeypatch) -> None:
    from grit.profiles import load_profiles
    from grit.report.red import draft_red_alerts

    pool, _result = _daily(tmp_path, monkeypatch)
    strait = pool.one("SELECT signal_id FROM signals WHERE title LIKE 'Taiwan reports 30%'")["signal_id"]
    with pool.transaction() as conn:
        conn.execute("UPDATE assessments SET red = 1, red_rule_json = '{\"rule_id\": \"T\", \"label\": \"Test\"}' WHERE signal_id = ?", (strait,))
    profiles = list(load_profiles().values())
    first = draft_red_alerts(pool, daily_run_id(DAY, "urs-tw"), "urs-tw", profiles, tmp_path / "a")
    week = reader_scan.new_run(pool, DAY + timedelta(days=1), "urs-tw")
    pool.save_assessment({"signal_id": strait, "run_id": week.run_id, "pack": "urs-tw", "decision": "KEEP", "red": 1, "red_rule_json": {"rule_id": "T"}})
    assert len(first) == 1 and draft_red_alerts(pool, week.run_id, "urs-tw", profiles, tmp_path / "b") == []


def test_the_nightly_index_rebuilds_recent_days_so_a_late_run_reaches_its_digest(tmp_path: Path, monkeypatch) -> None:
    pool, _result = _daily(tmp_path, monkeypatch)
    nightly(pool, DAY, packs=["urs-tw"])
    with pool.transaction() as conn:
        conn.execute("UPDATE assessments SET tier = 2")
    nightly(pool, DAY + timedelta(days=1), packs=["urs-tw"])
    assert digests(pool, ["urs-tw"], DAY, DAY)[0]["tier_1_2_events"], "yesterday's digest was rebuilt with what changed after it was first written"
