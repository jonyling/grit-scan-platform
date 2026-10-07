import json
from pathlib import Path

import pytest

from grit.acquisition.brave import (
    MAX_QUERY_CHARS,
    NEWS_URL,
    BraveFetchError,
    articles_from_payload,
    collect_brave,
    freshness_for,
    plan_queries,
)

CONFIG = Path(__file__).resolve().parents[1] / "config"


def _lobs(*lobs: dict) -> dict:
    return {"lobs": list(lobs)}


def _lob(lob_id: str, keywords: list[str], *, active: bool = True, days: int = 7) -> dict:
    return {"lobId": lob_id, "lobName": lob_id, "active": active, "daysToScan": days, "keywords": keywords}


def test_plan_is_keywords_only_from_active_lobs() -> None:
    plan = plan_queries(
        _lobs(
            _lob("LOB-A", ["railway signalling", "CBTC"]),
            _lob("LOB-B", ["never queried"], active=False),
        )
    )
    assert [(item.lob_id, item.query) for item in plan] == [("LOB-A", '"railway signalling" OR "CBTC"')]
    assert "site:" not in plan[0].query


def test_plan_splits_a_long_keyword_list_and_drops_none() -> None:
    keywords = [f"keyword number {index:02d} for rail" for index in range(30)]
    plan = plan_queries(_lobs(_lob("LOB-A", keywords)))
    assert len(plan) > 1
    assert all(len(item.query) <= MAX_QUERY_CHARS and len(item.query.split()) <= 50 for item in plan)
    planned = {part.strip('"') for item in plan for part in item.query.split(" OR ")}
    assert planned == set(keywords)


def test_plan_dedupes_and_strips_quotes() -> None:
    plan = plan_queries(_lobs(_lob("LOB-A", ['CBTC', 'cbtc', ' "rail  OT" ', ""])))
    assert plan[0].query == '"CBTC" OR "rail OT"'


def test_plan_can_name_an_lob_and_refuses_an_inactive_or_unknown_one() -> None:
    lobs = _lobs(_lob("LOB-A", ["alpha rail"]), _lob("LOB-B", ["beta rail"]), _lob("LOB-C", ["gamma"], active=False))
    assert [item.lob_id for item in plan_queries(lobs, ["LOB-B"])] == ["LOB-B"]
    for bad in ("LOB-C", "LOB-Z"):
        with pytest.raises(ValueError, match="No active LOB"):
            plan_queries(lobs, [bad])


def test_plan_reads_the_real_packs() -> None:
    for pack, file in (("urs-tw", "URS-TW-LOB-Profiles.json"), ("urs", "URS-LOB-Profiles.json")):
        profiles = json.loads((CONFIG / pack / file).read_text(encoding="utf-8-sig"))
        active = {lob["lobId"] for lob in profiles["lobs"] if lob["active"] is True}
        plan = plan_queries(profiles)
        assert {item.lob_id for item in plan} == active
        assert all('"' in item.query and "site:" not in item.query for item in plan)


def test_freshness_covers_the_scan_window() -> None:
    assert [freshness_for(day) for day in (1, 7, 14, 31, 90)] == ["pd", "pw", "pm", "pm", "py"]


def test_news_result_becomes_a_candidate_without_a_rank() -> None:
    payload = {
        "type": "news",
        "results": [
            {
                "title": "Signalling <strong>fault</strong> halts line",
                "url": "https://www.example.com/rail/fault",
                "description": "A CBTC <strong>outage</strong> &amp; delays.",
                "age": "2 days ago",
                "page_age": "2026-09-30T16:03:15",
                "meta_url": {"hostname": "www.example.com"},
                "thumbnail": {"src": "https://img.example/a.jpg"},
            }
        ],
    }
    seen: list[dict[str, str]] = []

    def getter(url: str, params: dict[str, str]) -> dict:
        assert url == NEWS_URL
        seen.append(params)
        return payload

    candidates = collect_brave('"CBTC"', fetched_at="2026-10-02T00:00:00Z", getter=getter)
    assert seen == [{"q": '"CBTC"', "count": "10", "search_lang": "en", "freshness": "pw"}]
    record = candidates[0].as_record()
    assert record["source_lane"] == "WILD"
    assert record["title"] == "Signalling fault halts line"
    assert record["text"] == "A CBTC outage & delays."
    assert record["publisher"] == "example.com"
    assert record["published_at"] == "2026-09-30T16:03:15Z"
    assert "thumbnail" not in record and "age" not in record


def test_missing_page_age_stays_missing() -> None:
    records = articles_from_payload({"results": [{"title": "T", "url": "https://a.example/x"}]})
    assert records[0]["published_at"] is None


def test_queries_stay_separate_and_a_pull_cap_is_enforced() -> None:
    calls: list[str] = []

    def getter(url: str, params: dict[str, str]) -> dict:
        calls.append(params["q"])
        return {"results": [{"title": params["q"], "url": f"https://a.example/{len(calls)}"}]}

    assert len(collect_brave(["one", "two"], fetched_at="x", getter=getter)) == 2
    assert calls == ["one", "two"]
    with pytest.raises(ValueError, match="pulls"):
        collect_brave([f"q{i}" for i in range(7)], fetched_at="x", getter=getter)


def test_bad_queries_and_bounds_are_refused() -> None:
    for bad in ("", "  ", "two\nlines", "x" * (MAX_QUERY_CHARS + 1), " ".join(["w"] * 51)):
        with pytest.raises(ValueError):
            collect_brave(bad, fetched_at="x", getter=lambda u, p: {"results": []})
    with pytest.raises(ValueError):
        collect_brave("ok", fetched_at="x", max_records=21, getter=lambda u, p: {"results": []})
    with pytest.raises(ValueError):
        collect_brave("ok", fetched_at="x", freshness="pq", getter=lambda u, p: {"results": []})


def test_bad_payload_is_an_error_not_an_empty_week() -> None:
    for payload in ({}, {"results": "no"}, []):
        with pytest.raises(BraveFetchError):
            articles_from_payload(payload)


def test_missing_key_is_an_error(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    with pytest.raises(BraveFetchError, match="BRAVE_SEARCH_API_KEY"):
        collect_brave("ok", fetched_at="x")
