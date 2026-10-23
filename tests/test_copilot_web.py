"""Co-Pilot web search and Add to pool (spec 41), offline: fake Brave and Currents getters, a stub model."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from grit.acquisition.brave_plan import BraveResponse
from grit.copilot.agent import ask
from grit.copilot.scope import scope_for
from grit.copilot.tools import Toolbox
from grit.copilot.web import WebSearch, add_to_pool, budget, search_web, take_additions
from grit.pipeline import reader_scan
from grit.pipeline.reader_scan import stub_client
from test_copilot import LABELS, TODAY, _profile

ARTICLE = {"title": "Kaohsiung metro opens bids for Yellow Line systems", "url": "https://example-news.com/yellow", "description": "Bids for the NT$12bn systems package close on 30 October.",
           "page_age": "2026-10-09T08:00:00", "meta_url": {"hostname": "example-news.com"}}


def _brave(results: list[dict], status: int = 200):  # noqa: ANN202
    calls: list[dict] = []

    def get(url: str, params: dict) -> BraveResponse:
        calls.append(params)
        return BraveResponse(status, {"results": results} if status == 200 else None, {"x-ratelimit-limit": "50"})

    return get, calls


def _currents(news: list[dict]):  # noqa: ANN202
    calls: list[dict] = []

    def get(url: str, params: dict) -> dict:
        calls.append(params)
        return {"status": "ok", "news": news}

    return get, calls


CURRENTS_NEWS = [{"title": "Currents: Yellow Line bids", "url": "https://other.example/yellow", "description": "Bids close on 30 October.", "published": "2026-10-09 08:00:00 +0000"}]


def test_brave_answers_first_and_currents_takes_over_near_the_limit_or_on_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_WEB_ORDER", "brave")
    brave, brave_calls = _brave([ARTICLE])
    currents, currents_calls = _currents(CURRENTS_NEWS)
    usage = tmp_path / "usage.json"
    found = search_web("Yellow Line bids", brave_getter=brave, currents_getter=currents, usage_path=usage, near_limit=False)
    assert found["engine"] == "BRAVE" and found["results"][0]["url"] == ARTICLE["url"] and not currents_calls
    assert brave_calls[0]["count"] == "8" and usage.is_file(), "a Co-Pilot search counts against Brave's monthly guard"
    near = search_web("Yellow Line bids", brave_getter=brave, currents_getter=currents, usage_path=usage, near_limit=True)
    assert near["engine"] == "CURRENTS" and len(brave_calls) == 1
    broken, _ = _brave([], status=500)
    failed_over = search_web("Yellow Line bids", brave_getter=broken, currents_getter=currents, usage_path=usage, near_limit=False)
    assert failed_over["engine"] == "CURRENTS" and failed_over["tried"][0].startswith("BRAVE:")
    empty, _ = _brave([])
    assert search_web("x", brave_getter=empty, currents_getter=currents, usage_path=usage, near_limit=False)["engine"] == "CURRENTS", "an empty Brave answer goes to Currents"


def test_no_key_means_no_search_and_a_plain_reason(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("CURRENTS_API_KEY", raising=False)
    found = search_web("anything", near_limit=False)
    assert found["engine"] == "NONE" and "No web search key" in found["tried"][0]


def test_the_daily_budget_and_the_per_question_cap(scanned) -> None:
    pool, _run = scanned
    profile = _profile()
    assert budget(profile) == 17
    calls: list[str] = []
    web = WebSearch(pool, profile, today=TODAY, search=lambda query: calls.append(query) or {"engine": "BRAVE", "results": [{"url": "https://a.example/1", "title": "A"}]})
    assert web("first")["remaining_today"] == 16 and web("second")["engine"] == "BRAVE"
    assert "At most 2" in web("third")["error"] and len(calls) == 2
    for number in range(15):
        pool.insert("copilot_web_searches", {"profile_id": "wilson", "day": TODAY.isoformat(), "engine": "BRAVE", "query": f"q{number}", "results": 1, "status": "OK", "searched_at": "x"})
    later = WebSearch(pool, profile, today=TODAY, search=lambda query: pytest.fail("over budget"))
    assert later.remaining() == 0 and "used up" in later("again")["error"]
    assert WebSearch(pool, _profile("mkteo"), today=TODAY).remaining() == 17, "each reader has their own budget"


@pytest.fixture
def scanned(tmp_path: Path, monkeypatch):  # noqa: ANN201
    from test_reader_scan import _scanned

    return _scanned(tmp_path, monkeypatch)


def test_web_results_are_unvetted_sources_and_the_answer_says_so(scanned) -> None:
    pool, _run = scanned
    scope = scope_for(_profile(), today=TODAY, labels=LABELS)
    web = WebSearch(pool, scope.profile, today=TODAY, search=lambda query: {"engine": "BRAVE", "results": [
        {"url": "https://example-news.com/yellow", "title": "Kaohsiung metro opens bids for Yellow Line systems", "text": "Bids close on 30 October.", "publisher": "example-news.com", "published_at": "2026-10-09T08:00:00Z"}]})
    client = stub_client(lambda _s, payload: (
        {"tool_calls": [{"tool": "search_signals", "args": {"query": "Yellow Line"}}, {"tool": "web_search", "args": {"query": "Kaohsiung Yellow Line bids"}}]}
        if not payload.get("results") else
        {"final": {"answer": "Bids for the Yellow Line systems package close on 30 October [W1].", "evidence": ["Kaohsiung metro opened bids [W1]."],
                   "implications": ["URS Taiwan could bid if the package fits its scope [W1]."], "uncertain": ["GRIT-Scan's records do not hold this tender."]}}))
    assert scope_for(_profile(), today=TODAY, labels=LABELS)
    result = ask(pool, scope, "Is the Yellow Line tendering?", client=client, model="stub", web=web)
    assert result["status"] == "VALIDATED", result["markdown"]
    assert "[Web, unvetted: example-news.com, 2026-10-09](https://example-news.com/yellow)" in result["markdown"]
    assert "unvetted: GRIT-Scan's rules have not assessed them" in result["markdown"] and "searched the web" in result["steps"]
    citation = result["citations"][0]
    assert citation["kind"] == "WEB" and citation["packs"] == ["urs-tw"] and citation["title"].startswith("Kaohsiung")
    tools = Toolbox(pool, scope)
    assert "off" in tools.call("web_search", {"query": "x"})["error"]


def test_an_added_page_waits_for_the_next_scan_and_the_rules_decide(scanned, tmp_path: Path, monkeypatch) -> None:
    pool, run = scanned
    page = {"url": "https://example-news.com/yellow", "title": "Kaohsiung metro opens bids for Yellow Line systems", "text": "Bids close on 30 October.", "publisher": "example-news.com"}
    added = add_to_pool(pool, _profile(), page, ["urs-tw"])
    assert added["queued"] == ["urs-tw"] and pool.signal(added["signal_id"])["lane"] == "CHAT"
    assert add_to_pool(pool, _profile(), page, ["urs-tw"])["queued"] == [], "a page is queued once per pack"
    with pytest.raises(ValueError):
        add_to_pool(pool, _profile(), {"url": "ftp://x", "title": "t"}, ["urs-tw"])
    later = reader_scan.new_run(pool, date(2026, 10, 14), "urs-tw")
    result = reader_scan.acquire(pool, later, [], getter=_brave([])[0], usage_path=tmp_path / "u.json", sleep=lambda _s: None, log=lambda _m: None)
    assert result["added_from_copilot"] == 1
    assert added["signal_id"] in {row["signal_id"] for row in pool.signals_for_run(later.run_id)}
    assert pool.one("SELECT status, run_id FROM pool_additions") == {"status": "SCANNED", "run_id": later.run_id}
    assert take_additions(pool, later.run_id, "urs-tw") == 0
    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    from test_reader_scan import _Embedder

    reader_scan.embed(pool, later, embedder=_Embedder())
    reader_scan.assess(pool, later, score_harm_fn=lambda *a, **k: {"status": "NOT_RUN", "reason": "test"})
    decided = {row["signal_id"]: row for row in pool.assessments(later.run_id)}
    assert decided[added["signal_id"]]["decision"] in {"KEEP", "DROP"}, "the run's deterministic rules assessed the added page"


def test_a_question_about_now_over_stale_records_triggers_a_web_search() -> None:
    from grit.copilot.agent import web_trigger

    assert web_trigger("Has the rule been postponed since our last scan?", "2026-10-13", date(2026, 10, 20))
    assert web_trigger("What is the latest on HBM?", None, date(2026, 10, 20))
    assert web_trigger("What is the latest on HBM?", "2026-10-19", date(2026, 10, 20)) is None, "fresh records need no web"
    assert web_trigger("Why did GPU access move?", "2026-10-01", date(2026, 10, 20)) is None, "a question about why, not now, needs no web"


def test_added_urls_are_canonical_so_a_known_page_keeps_one_signal(scanned) -> None:
    pool, _run = scanned
    known = pool.add_signal(url="https://example-news.com/story", title="A story", text="", publisher="example-news.com", published_at=None, lane="BRAVE", content_hash="h")
    added = add_to_pool(pool, _profile(), {"url": "https://example-news.com/story/?utm_source=x", "title": "A story"}, ["urs-tw"])
    assert added["signal_id"] == known


def test_concurrent_searches_all_count_against_the_monthly_guard(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_WEB_ORDER", "brave")
    from grit.acquisition.brave_plan import _load_usage

    usage = tmp_path / "usage.json"
    calls = {"n": 0}

    def get(url: str, params: dict) -> BraveResponse:
        calls["n"] += 1
        if calls["n"] == 1:  # another process searches while this one is running
            search_web("other", brave_getter=_brave([ARTICLE])[0], usage_path=usage, near_limit=False, now=None)
        return BraveResponse(200, {"results": [ARTICLE]}, {"x-ratelimit-limit": "50"})

    search_web("mine", brave_getter=get, usage_path=usage, near_limit=False)
    from datetime import datetime, timezone

    assert _load_usage(usage, datetime.now(timezone.utc).strftime("%Y-%m")) == 2


def test_the_copilot_tries_currents_first_by_default_to_save_brave_queries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("COPILOT_WEB_ORDER", raising=False)
    brave, brave_calls = _brave([ARTICLE])
    currents, _ = _currents(CURRENTS_NEWS)
    assert search_web("Yellow Line bids", brave_getter=brave, currents_getter=currents, usage_path=tmp_path / "u.json", near_limit=False)["engine"] == "CURRENTS" and not brave_calls
    empty, _ = _currents([])
    assert search_web("x", brave_getter=brave, currents_getter=empty, usage_path=tmp_path / "u.json", near_limit=False)["engine"] == "BRAVE", "Brave answers when Currents has nothing"
