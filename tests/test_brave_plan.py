import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from acquisition.brave import BraveFetchError
from acquisition.brave_plan import BraveResponse, canonical_url, collect_plan, main

_OK_HEADERS = {"x-ratelimit-limit": "50, 0", "x-ratelimit-remaining": "49, 0"}
_AT = "2026-10-12T07:00:00Z"


def _q(query_id, query, exact=False):
    return SimpleNamespace(query_id=query_id, query=query, exact=exact)


def _result(title, url, description="", extras=(), page_age=None, host=None):
    item = {"title": title, "url": url, "description": description, "extra_snippets": list(extras), "page_age": page_age}
    if host:
        item["meta_url"] = {"hostname": host}
    return item


def _ok(*results, headers=None):
    return BraveResponse(200, {"type": "news", "results": list(results)}, headers or _OK_HEADERS)


class _Getter:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, params):
        self.calls.append(params)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def _usage(directory):
    return Path(directory) / "usage.json"


def _collect(queries, getter, directory, **options):
    return collect_plan(queries, fetched_at=_AT, getter=getter, sleep=lambda _seconds: None, usage_path=_usage(directory), **options)


def test_names_are_quoted_topics_are_not_and_the_week_filter_is_sent() -> None:
    getter = _Getter(_ok())
    with TemporaryDirectory() as directory:
        _collect([_q("a", "Railway Cybersecurity"), _q("b", "LEONARDO S.P.A.", exact=True)], getter, directory)
        _collect([_q("c", "Metro")], getter, directory, country="ALL")
    assert getter.calls[0]["q"] == "Railway Cybersecurity" and getter.calls[1]["q"] == '"LEONARDO S.P.A."'
    assert getter.calls[0]["freshness"] == "pw" and getter.calls[0]["count"] == "20" and "country" not in getter.calls[0]
    assert getter.calls[2]["country"] == "ALL"


def test_results_become_wild_candidates_with_extra_snippets_and_a_utc_time() -> None:
    result = _result(
        "Platform screen doors fail",
        "https://www.example.com/news/psd",
        "A <strong>PSD</strong> failure.",
        extras=["Second snippet", "<b>Third</b> snippet &amp; more"],
        page_age="2026-10-02T12:20:12",
        host="www.example.com",
    )
    with TemporaryDirectory() as directory:
        run = _collect([_q("a", "Platform Screen Doors")], _Getter(_ok(result)), directory)
    (candidate,) = run.candidates
    assert candidate.source_lane == "WILD" and candidate.publisher == "example.com"
    assert candidate.published_at == "2026-10-02T12:20:12Z" and candidate.seen_at == _AT
    assert candidate.text == "A PSD failure.\nSecond snippet\nThird snippet & more"


def test_a_page_found_by_two_queries_is_one_candidate_and_both_hits_are_kept() -> None:
    first = _ok(_result("Other", "https://a.com/x", "one"), _result("Story", "https://news.com/story/", "text"))
    second = _ok(_result("Story again", "https://NEWS.com/story?utm_source=feed#top", "text"))
    with TemporaryDirectory() as directory:
        run = _collect([_q("a", "one"), _q("b", "two")], _Getter(first, second), directory)
    assert [candidate.url for candidate in run.candidates] == ["https://a.com/x", "https://news.com/story"]
    assert run.hits["https://news.com/story"] == [{"query_id": "a", "rank": 2}, {"query_id": "b", "rank": 1}]


def test_results_without_a_title_or_an_http_url_are_skipped_and_an_empty_answer_is_valid() -> None:
    getter = _Getter(_ok(_result("", "https://x.com/a"), _result("Title", "ftp://x.com/a")), _ok())
    with TemporaryDirectory() as directory:
        run = _collect([_q("a", "one"), _q("b", "two")], getter, directory)
    assert run.candidates == [] and run.queries_sent == 2 and run.stopped is None


def test_a_429_is_retried_then_the_run_stops_with_a_reason() -> None:
    limited = BraveResponse(429, None, {"x-ratelimit-reset": "3, 100"})
    getter = _Getter(_ok(_result("Story", "https://a.com/s", "t")), limited)
    sleeps: list[float] = []
    with TemporaryDirectory() as directory:
        run = collect_plan(
            [_q("a", "one"), _q("b", "two")], fetched_at=_AT, getter=getter, sleep=sleeps.append, usage_path=_usage(directory)
        )
    assert run.stopped == "RATE_LIMITED" and run.queries_sent == 1 and len(run.candidates) == 1
    assert len(getter.calls) == 4 and sleeps[-2:] == [3.0, 3.0]


def test_any_other_status_raises_and_never_names_the_key() -> None:
    previous = os.environ.get("BRAVE_SEARCH_API_KEY")
    os.environ["BRAVE_SEARCH_API_KEY"] = "secret-key-value"
    try:
        with TemporaryDirectory() as directory:
            _collect([_q("a", "one")], _Getter(BraveResponse(401, None, {})), directory)
    except BraveFetchError as error:
        assert "HTTP 401" in str(error) and "secret-key-value" not in str(error)
    else:
        raise AssertionError("A 401 must raise")
    finally:
        if previous is None:
            os.environ.pop("BRAVE_SEARCH_API_KEY", None)
        else:
            os.environ["BRAVE_SEARCH_API_KEY"] = previous


def test_the_monthly_guard_stops_before_sending_and_a_new_month_resets_it() -> None:
    getter = _Getter(_ok())
    with TemporaryDirectory() as directory:
        _usage(directory).write_text('{"month": "2026-10", "used": 1999}\n', encoding="utf-8")
        queries = [_q("a", "one"), _q("b", "two"), _q("c", "three")]
        run = _collect(queries, getter, directory, monthly_limit=2000)
        assert run.stopped == "MONTHLY_LIMIT" and run.queries_sent == 1
        assert json.loads(_usage(directory).read_text()) == {"month": "2026-10", "used": 2000}
        assert _collect(queries, getter, directory, monthly_limit=2000).queries_sent == 0
        november = collect_plan(queries, fetched_at="2026-11-02T07:00:00Z", getter=getter, sleep=lambda _s: None, usage_path=_usage(directory), monthly_limit=2000)
        assert november.queries_sent == 3 and november.stopped is None


def test_the_pause_follows_the_per_second_limit_header() -> None:
    getter = _Getter(_ok(headers={"x-ratelimit-limit": "2, 0"}))
    sleeps: list[float] = []
    with TemporaryDirectory() as directory:
        collect_plan([_q("a", "one"), _q("b", "two"), _q("c", "three")], fetched_at=_AT, getter=getter, sleep=sleeps.append, usage_path=_usage(directory))
    assert sleeps == [0.5, 0.5]


def test_every_search_reaches_the_callback_before_a_later_failure() -> None:
    getter = _Getter(_ok(_result("Story", "https://a.com/s", "t")), BraveResponse(500, None, {}))
    seen: list[dict] = []
    with TemporaryDirectory() as directory:
        try:
            collect_plan([_q("a", "one"), _q("b", "two")], fetched_at=_AT, getter=getter, sleep=lambda _s: None, usage_path=_usage(directory), on_search=seen.append)
        except BraveFetchError:
            pass
        else:
            raise AssertionError("A 500 must raise")
    assert [entry["query_id"] for entry in seen] == ["a"] and seen[0]["results"][0]["rank"] == 1


def test_a_missing_key_and_bad_bounds_are_refused_before_any_call() -> None:
    previous = os.environ.pop("BRAVE_SEARCH_API_KEY", None)
    try:
        with TemporaryDirectory() as directory:
            for options, expected in (({}, BraveFetchError), ({"count": 21}, ValueError), ({"freshness": "week"}, ValueError)):
                try:
                    collect_plan([_q("a", "one")], fetched_at=_AT, getter=None if not options else _Getter(_ok()), usage_path=_usage(directory), **options)
                except expected:
                    pass
                else:
                    raise AssertionError(f"{options} must raise {expected.__name__}")
    finally:
        if previous is not None:
            os.environ["BRAVE_SEARCH_API_KEY"] = previous


def test_canonical_url_drops_tracking_fragment_and_trailing_slash_only() -> None:
    assert canonical_url("HTTPS://News.com/Story/?id=7&utm_medium=x&fbclid=y#top") == "https://news.com/Story?id=7"
    assert canonical_url("https://news.com/") == "https://news.com"


def test_the_command_runs_a_trial_logs_each_search_and_refuses_to_run_twice() -> None:
    class Numbered:
        def __init__(self):
            self.count = 0

        def __call__(self, url, params):
            self.count += 1
            return _ok(_result(f"Story {self.count}", f"https://news.com/s{self.count}", "text"))

    with TemporaryDirectory() as directory:
        folder = Path(directory) / "runs"
        args = ["--date", "2026-10-12", "--limit-queries", "3", "--output-dir", str(folder)]
        assert main([*args, "--dry-run"], usage_path=_usage(directory)) == 0 and not folder.exists()
        assert main(args, getter=Numbered(), sleep=lambda _s: None, usage_path=_usage(directory)) == 0
        day = folder / "2026-10-12"
        assert len((day / "searches.jsonl").read_text().splitlines()) == 3
        stored = json.loads((day / "candidates.json").read_text())
        assert stored["queriesSent"] == 3 and len(stored["candidates"]) == 3 and stored["stopped"] is None
        assert json.loads((day / "selection.json").read_text())["runDate"] == "2026-10-12"
        assert main(args, getter=Numbered(), sleep=lambda _s: None, usage_path=_usage(directory)) == 1
        assert len((day / "searches.jsonl").read_text().splitlines()) == 3
