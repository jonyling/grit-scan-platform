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
    assert "1 new tier 1 or 2, RED or management-request events" in day["summary"], "the digest is a citable source, so its counts can be cited"
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


def test_the_weekly_sweep_reaches_blocked_sources_once_a_week_for_every_pack_they_serve(tmp_path: Path, monkeypatch) -> None:
    from grit.acquisition.brave_plan import BraveResponse
    from grit.daily.lane import due_sweeps, sweep_sources

    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("grit.daily.lane.SWEEP_DAYS", 7)
    pool = Pool(tmp_path / "grit.sqlite")
    isw = Feed("isw-test", "ISW", None, ("urs-tw",), "test", "BLOCKED", "403", "site:understandingwar.org")
    rusi = Feed("rusi-test", "RUSI", None, ("urs-tw",), "test", "BLOCKED", "no feed", "site:rusi.org")
    sent: list[str] = []

    def brave(_url: str, params: dict) -> BraveResponse:
        sent.append(params["q"])
        if "rusi" in params["q"]:
            return BraveResponse(500, None, {})
        results = [{"title": "Russian Offensive Campaign Assessment", "url": "https://understandingwar.org/research/russia-ukraine/assessment-1/", "description": "PLA aircraft near Taiwan Strait.",
                    "page_age": f"{DAY.isoformat()}T01:00:00", "meta_url": {"hostname": "understandingwar.org"}},
                   {"title": "Off-site copy", "url": "https://mirror.example/isw", "description": "x", "page_age": f"{DAY.isoformat()}T01:00:00", "meta_url": {"hostname": "mirror.example"}}]
        return BraveResponse(200, {"results": results}, {"x-ratelimit-limit": "50"})

    pages = sweep_sources(pool, DAY, feeds=[isw, rusi], getter=brave, usage_path=tmp_path / "u.json", log=lambda _m: None)
    assert sent == ["site:understandingwar.org", "site:rusi.org"]
    assert list(pages) == ["isw-test"] and [page["url"] for page in pages["isw-test"]] == ["https://understandingwar.org/research/russia-ukraine/assessment-1"], "only the site's own pages"
    assert {row["feed_id"]: row["status"] for row in pool.rows("SELECT feed_id, status FROM source_sweeps")} == {"isw-test": "OK", "rusi-test": "FAILED"}
    assert [feed.feed_id for feed in due_sweeps(pool, DAY + timedelta(days=1), [isw, rusi])] == ["rusi-test"], "a failed sweep is retried; a done one waits a week"
    assert [feed.feed_id for feed in due_sweeps(pool, DAY + timedelta(days=7), [isw, rusi])] == ["isw-test", "rusi-test"]
    monkeypatch.setattr("grit.daily.lane.FEEDS", (isw, rusi))
    result = run_daily(pool, "urs-tw", DAY, feeds=[], use_currents=False, embedder=_Embedder(), score_harm_fn=lambda *a, **k: {"status": "NOT_RUN"}, log=lambda _m: None, swept_pages=pages)
    assert result.feeds == {"sweep:isw-test": 1}
    assert pool.one("SELECT lane, query FROM sightings WHERE query_id = 'sweep-isw-test'") == {"lane": "SWEEP", "query": "site:understandingwar.org"}


def test_cisa_kev_entries_become_dated_items_linked_to_nvd() -> None:
    import json as _json

    from grit.daily.feeds import FEEDS, read_feed

    kev = next(feed for feed in FEEDS if feed.feed_id == "cisa-kev")
    raw = _json.dumps({"vulnerabilities": [
        {"cveID": "CVE-2026-1", "vendorProject": "PHP Group", "product": "PHP", "vulnerabilityName": "PHP Remote Code Execution", "dateAdded": DAY.isoformat(),
         "shortDescription": "PHP contains a flaw.", "requiredAction": "Apply updates.", "dueDate": "2026-11-12", "knownRansomwareCampaignUse": "Known"},
        {"cveID": "CVE-2020-1", "vendorProject": "Old", "product": "Thing", "vulnerabilityName": "Old flaw", "dateAdded": "2020-01-01"},
    ]}).encode()
    items = read_feed(kev, since=datetime.combine(DAY - timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc), getter=lambda _url: raw)
    assert [item["url"] for item in items] == ["https://nvd.nist.gov/vuln/detail/CVE-2026-1"]
    assert items[0]["title"].startswith("CISA adds CVE-2026-1 (PHP Group PHP)") and "ransomware" in items[0]["text"] and items[0]["publisher"] == "cisa.gov"


def test_a_source_without_a_feed_is_read_daily_through_currents(tmp_path: Path, monkeypatch) -> None:
    from grit.daily import lane

    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    source = Feed("csa-test", "CSA", None, ("urs-tw",), "test", "BLOCKED", "no feed", None, currents="Cyber Security Agency of Singapore")
    monkeypatch.setattr(lane, "FEEDS", (source,))
    asked: list[str] = []

    def currents(_url: str, params: dict) -> dict:
        asked.append(params["keywords"])
        return CURRENTS if params["keywords"] == "Cyber Security Agency of Singapore" else {"status": "ok", "news": []}

    pool = Pool(tmp_path / "grit.sqlite")
    result = run_daily(pool, "urs-tw", DAY, feeds=[], currents_getter=currents, embedder=_Embedder(), score_harm_fn=lambda *a, **k: {"status": "NOT_RUN"}, log=lambda _m: None)
    assert asked[0] == "Cyber Security Agency of Singapore" and result.feeds["currents:csa-test"] == 1
    assert pool.one("SELECT lane FROM sightings WHERE query_id = 'source-csa-test'") == {"lane": "SOURCE"}


def test_a_page_matching_an_active_request_is_new_whatever_its_tier(tmp_path: Path, monkeypatch) -> None:
    pool, _result = _daily(tmp_path, monkeypatch)
    assert build_digest(pool, "urs-tw", DAY)["tier_1_2_events"] == [], "kept pages are tier 3 and 4 in this fixture"
    pool.insert("management_requests", {"request_id": "MR-T", "title": "Strait", "requested_by": "test", "request_date": DAY.isoformat(), "kind": "CONCERN",
                                        "keywords_json": ["PLA aircraft"], "status": "ACTIVE", "origin": "CHAT"})
    digest = build_digest(pool, "urs-tw", DAY)
    assert [item["request"] for item in digest["tier_1_2_events"]] == ["MR-T"] and digest["request_events"] == 1


def test_the_daily_run_writes_its_kept_signals_to_a_csv(tmp_path: Path, monkeypatch) -> None:
    import csv

    from grit.daily.lane import CSV_COLUMNS

    pool, result = _daily(tmp_path, monkeypatch)
    path = Path(result.csv)
    assert path == tmp_path / "runs" / daily_run_id(DAY, "urs-tw") / f"{daily_run_id(DAY, 'urs-tw')}-signals.csv"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf"), "Excel needs the byte-order mark for Chinese titles"
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert tuple(rows[0]) == CSV_COLUMNS and len(rows) == result.kept == 2
    assert {row["Title"] for row in rows} == {"Taiwan reports 30 PLA aircraft near Taiwan Strait", "Taichung MRT Blue Line BC21 tender stays open until 12 October"}
    assert all(row["URL"].startswith("https://") and row["Found by"] for row in rows)


def _since(days: int) -> datetime:
    return datetime.combine(DAY - timedelta(days=days), datetime.min.time(), tzinfo=timezone.utc)


def test_usgs_keeps_taiwan_region_earthquakes_of_magnitude_5_or_more() -> None:
    import json

    from grit.daily.feeds import read_usgs

    def quake(mag: float, lon: float, lat: float, days_ago: int, place: str) -> dict:
        when = datetime.combine(DAY, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=days_ago) + timedelta(hours=5)
        return {"properties": {"mag": mag, "place": place, "time": int(when.timestamp() * 1000), "url": f"https://earthquake.usgs.gov/earthquakes/eventpage/{place[:3]}{mag}", "alert": "yellow" if mag > 6 else None, "tsunami": 0},
                "geometry": {"coordinates": [lon, lat, 15.0]}}

    raw = json.dumps({"features": [quake(6.4, 121.7, 23.9, 0, "20 km E of Hualien City, Taiwan"), quake(4.8, 121.7, 24.0, 0, "Yilan small"),
                                   quake(6.0, 140.0, 35.0, 0, "Tokyo, Japan"), quake(5.5, 121.0, 22.5, 9, "Taitung old")]}).encode()
    items = read_usgs(raw, _since(2))
    assert [item["title"] for item in items] == ["USGS: magnitude 6.4 earthquake, 20 km E of Hualien City, Taiwan"]
    assert "PAGER alert level yellow" in items[0]["text"] and "15 km deep" in items[0]["text"] and items[0]["publisher"] == "usgs.gov"


def test_ncdr_keeps_hazard_alerts_with_english_categories_and_skips_routine_notices() -> None:
    from grit.daily.feeds import read_ncdr

    when = (datetime.combine(DAY, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def entry(title: str, term: str, author: str, ident: str) -> str:
        return (f"<entry><id>{ident}</id><title>{title}</title><updated>{when}</updated><author><name>{author}</name></author>"
                f'<link rel="alternate" href="https://alerts.ncdr.nat.gov.tw/Capstorage/{ident}.cap"/><summary type="html">{title}，花蓮縣</summary><category term="{term}"/></entry>')

    raw = ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>NCDR</title>'
           + entry("颱風警報", "颱風", "中央氣象署", "typhoon1") + entry("道路封閉", "道路封閉", "交通部公路局", "road1") + entry("停電", "停電", "台灣電力公司", "power1") + "</feed>").encode()
    items = read_ncdr(raw, _since(2))
    assert {item["title"] for item in items} == {"Taiwan official alert: Typhoon, issued by 中央氣象署", "Taiwan official alert: Power outage, issued by 台灣電力公司"}
    assert all(item["url"].endswith(".cap") and item["publisher"] == "alerts.ncdr.nat.gov.tw" for item in items)


MND_LIST = """<a href="/en/News/PLAAct/88069" class="news_list">
    <div class="date headline-h5 h5">{today}</div><h2>PLA activities in the waters and airspace around Taiwan</h2></a>
<a href="/en/News/PLAAct/88001" class="news_list">
    <div class="date headline-h5 h5">{old}</div><h2>PLA activities in the waters and airspace around Taiwan</h2></a>"""
MND_PAGE = """<nav>Menu</nav><span aria-current="page">PLA activities</span><script>var x = 1;</script>
<p>1.Date: 6 a.m. Oct. 25 (Sun.) to 6 a.m. Oct. 26 (Mon.) (UTC+8)</p><p>2.PLA activities: 3 sorties of PLA aircraft, 1 PLAN ship and 9 official ships
operating around Taiwan were detected as of 6 a.m. (UTC+8) today. 1 out of 3 sorties crossed the median line of the Taiwan Strait.</p><p>Keywords: Taiwan Strait</p>"""


def test_mnd_reports_become_one_item_a_day_with_the_counts_in_the_title() -> None:
    from grit.daily.feeds import FEEDS as REGISTER, read_feed

    mnd = next(feed for feed in REGISTER if feed.kind == "mnd")
    pages = {mnd.url: MND_LIST.format(today=DAY.strftime("%Y.%m.%d"), old=(DAY - timedelta(days=9)).strftime("%Y.%m.%d")).encode(),
             "https://www.mnd.gov.tw/en/News/PLAAct/88069": MND_PAGE.encode()}
    items = read_feed(mnd, since=_since(2), getter=lambda url: pages[url])
    assert len(items) == 1 and items[0]["url"] == "https://www.mnd.gov.tw/en/News/PLAAct/88069"
    assert items[0]["title"] == "Taiwan MND: 3 PLA aircraft sorties, 1 PLAN ship, 9 official ships around Taiwan; 1 crossed the Strait median line"
    assert items[0]["text"].startswith("1.Date") and "Keywords" not in items[0]["text"] and "var x" not in items[0]["text"]
    assert items[0]["published_at"] == (datetime.combine(DAY, datetime.min.time()) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"), "06:00 Taiwan time"


def test_firms_reports_days_with_enough_high_confidence_detections_and_never_shows_the_key(monkeypatch) -> None:
    from grit.daily.feeds import FEEDS as REGISTER, read_feed

    firms = next(feed for feed in REGISTER if feed.kind == "firms")
    day, old = DAY.isoformat(), (DAY - timedelta(days=9)).isoformat()
    rows = ["latitude,longitude,bright_ti4,acq_date,acq_time,confidence,frp"]
    rows += [f"23.{i},120.{i},330,{day},0500,h,{10 + i}" for i in range(5)] + [f"24.0,121.0,300,{day},0500,n,99", f"22.5,120.5,330,{old},0500,h,50"] * 5
    seen: list[str] = []
    monkeypatch.setenv("FIRMS_MAP_KEY", "secret-key")
    items = read_feed(firms, since=_since(2), getter=lambda url: seen.append(url) or "\n".join(rows).encode())
    assert "secret-key" in seen[0] and len(items) == 1 and items[0]["title"] == f"NASA FIRMS: 5 high-confidence fire detections in Taiwan on {day}"
    assert "14 MW" in items[0]["text"] and "lat=23.4" in items[0]["url"]

    def fail(url: str) -> bytes:
        raise ConnectionError(f"cannot reach {url}")

    with pytest.raises(RuntimeError) as refused:
        read_feed(firms, since=_since(2), getter=fail)
    assert "secret-key" not in str(refused.value)
    monkeypatch.delenv("FIRMS_MAP_KEY")
    with pytest.raises(RuntimeError, match="FIRMS_MAP_KEY is not set"):
        read_feed(firms, since=_since(2))


def test_urs_taiwan_has_a_daily_run_fed_by_official_hazard_and_cross_strait_sources() -> None:
    from grit.daily.lane import daily_packs

    assert "urs-tw" in daily_packs()
    assert {"usgs-taiwan", "ncdr-taiwan", "tw-mnd", "nasa-firms"} <= {feed.feed_id for feed in active(["urs-tw"])}
    assert next(feed for feed in FEEDS if feed.feed_id == "cwa").status == "BLOCKED"


def _gdelt_payload(*articles: dict) -> dict:
    return {"articles": list(articles)}


def _seen(day: date) -> str:
    return day.strftime("%Y%m%d") + "T030000Z"


def test_gdelt_pages_follow_the_keep_rule_and_a_failed_fetch_is_not_an_empty_day(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from grit.acquisition.gdelt import GdeltFetchError
    from grit.daily import lane

    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(lane, "currents_specs", lambda pool, run: [SimpleNamespace(query_id="q-strait", query="Taiwan Strait", lane="CONDITION", sources=({"target": "TW-GLOBAL-03"},))])
    pool = Pool(tmp_path / "grit.sqlite")
    recent, future, old = "https://focustaiwan.tw/gdelt/recent", "https://focustaiwan.tw/gdelt/future", "https://focustaiwan.tw/gdelt/old"
    payload = _gdelt_payload(
        {"url": recent, "title": "Taiwan reports 30 PLA aircraft near Taiwan Strait", "seendate": _seen(DAY), "domain": "focustaiwan.tw"},
        {"url": future, "title": "Taiwan reports 40 PLA aircraft near Taiwan Strait", "seendate": _seen(DAY + timedelta(days=1)), "domain": "focustaiwan.tw"},
        {"url": old, "title": "Taiwan reports 20 PLA aircraft near Taiwan Strait", "seendate": _seen(DAY - timedelta(days=40)), "domain": "focustaiwan.tw"},
    )
    result = run_daily(
        pool, "urs-tw", DAY, feeds=[], use_currents=False, use_gdelt=True, gdelt_getter=lambda url, params: payload, gdelt_pause=0,
        embedder=_Embedder(), score_harm_fn=lambda *a, **k: {"status": "NOT_RUN"}, log=lambda _m: None,
    )
    decided = {row["title"]: row for row in pool.assessments(result.run_id)}
    assert decided["Taiwan reports 30 PLA aircraft near Taiwan Strait"]["decision"] == "KEEP"
    assert decided["Taiwan reports 40 PLA aircraft near Taiwan Strait"]["drop_reason"] == "AFTER_RUN_DATE"
    assert decided["Taiwan reports 20 PLA aircraft near Taiwan Strait"]["drop_reason"] == "STALE"
    assert pool.one("SELECT lane FROM signals WHERE url = ?", (recent,))["lane"] == "GDELT"
    assert result.gdelt["stopped"] is None and result.gdelt["sent"] == 1

    def fail(url, params):
        raise GdeltFetchError("GDELT request failed")

    failed = Pool(tmp_path / "failed.sqlite")
    stopped = run_daily(
        failed, "urs-tw", DAY, feeds=[], use_currents=False, use_gdelt=True, gdelt_getter=fail, gdelt_pause=0,
        embedder=_Embedder(), score_harm_fn=lambda *a, **k: {"status": "NOT_RUN"}, log=lambda _m: None,
    )
    assert stopped.gdelt["stopped"] == "GDELT request failed" and stopped.gdelt["sent"] == 0 and stopped.signals == 0


def test_the_daily_run_sends_red_mail_only_when_asked(tmp_path: Path, monkeypatch) -> None:
    import grit.report.red_mail as red_mail

    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    pool = Pool(tmp_path / "grit.sqlite")
    called: list[int] = []
    monkeypatch.setattr(red_mail, "send_daily_red_alerts", lambda *args, **kwargs: called.append(1) or {"status": "NOT_CONFIGURED", "sent": 0, "failed": 0})
    quiet = run_daily(pool, "urs-tw", DAY, feeds=[], use_currents=False, profiles=[], embedder=_Embedder(), score_harm_fn=lambda *a, **k: {"status": "NOT_RUN"}, log=lambda _m: None)
    assert called == [] and quiet.red_mail == {}
    run_daily(pool, "urs-tw", DAY, feeds=[], use_currents=False, profiles=[], send_mail=True, embedder=_Embedder(), score_harm_fn=lambda *a, **k: {"status": "NOT_RUN"}, log=lambda _m: None)
    assert called == [1]


def test_the_gdelt_lane_pauses_between_queries_without_failing(tmp_path: Path, monkeypatch) -> None:
    """The pause runs the real sleep. A datetime import once shadowed the time module, so the second query crashed the lane."""
    from types import SimpleNamespace

    from grit.daily import lane

    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    specs = [SimpleNamespace(query_id=f"q-{n}", query=f"Taiwan Strait {n}", lane="CONDITION", sources=({"target": "TW-GLOBAL-03"},)) for n in range(3)]
    monkeypatch.setattr(lane, "currents_specs", lambda pool, run: specs)
    pool = Pool(tmp_path / "grit.sqlite")
    payload = _gdelt_payload({"url": "https://focustaiwan.tw/gdelt/one", "title": "Taiwan reports 30 PLA aircraft near Taiwan Strait", "seendate": _seen(DAY), "domain": "focustaiwan.tw"})
    result = run_daily(pool, "urs-tw", DAY, feeds=[], use_currents=False, use_gdelt=True, gdelt_getter=lambda url, params: payload, gdelt_pause=0.01,
                       embedder=_Embedder(), score_harm_fn=lambda *a, **k: {"status": "NOT_RUN"}, log=lambda _m: None)
    assert result.gdelt["sent"] == 3 and result.gdelt["stopped"] is None
