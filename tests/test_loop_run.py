import json
from pathlib import Path
from tempfile import TemporaryDirectory

from grit.acquisition.brave_plan import BraveResponse
from grit.agents.loop.run import brave_search

_HEADERS = {"x-ratelimit-limit": "50, 0", "x-ratelimit-remaining": "49, 0"}


class _Getter:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, url, params):
        self.calls.append(params["q"])
        return BraveResponse(200, {"type": "news", "results": self.results}, _HEADERS)


_ARTICLE = {"title": "Worker dies at Tanjong Rhu site", "url": "https://straitstimes.com/a?utm_source=x", "description": "The Housing Board said so.", "extra_snippets": [], "page_age": "2026-09-28T08:00:00", "meta_url": {"hostname": "straitstimes.com"}}


def test_a_follow_up_search_is_logged_counted_and_reused_for_the_same_query_without_a_second_call() -> None:
    getter = _Getter(_ARTICLE)
    with TemporaryDirectory() as directory:
        folder, usage = Path(directory), Path(directory) / "usage.json"
        search = brave_search(folder, getter=getter, usage_path=usage)
        first = search("worker dies Tanjong Rhu BTO")
        again = brave_search(folder, getter=getter, usage_path=usage)("worker dies Tanjong Rhu BTO")
        assert getter.calls == ["worker dies Tanjong Rhu BTO"]  # the second call read the log and sent nothing
        assert [c["url"] for c in first] == ["https://straitstimes.com/a"] == [c["url"] for c in again] and first[0]["publisher"] == "straitstimes.com"
        assert json.loads(usage.read_text())["used"] == 1
        search("a different query")
        assert len(getter.calls) == 2 and len((folder / "followups.jsonl").read_text().splitlines()) == 2


def test_a_failed_follow_up_returns_nothing_and_is_logged_so_the_loop_continues() -> None:
    def failing(url, params):
        return BraveResponse(500, None, _HEADERS)

    with TemporaryDirectory() as directory:
        folder = Path(directory)
        assert brave_search(folder, getter=failing, usage_path=folder / "usage.json")("any query") == []
        entry = json.loads((folder / "followups.jsonl").read_text().splitlines()[0])
        assert entry["query"] == "any query" and "HTTP 500" in entry["error"]


def test_the_same_signal_reuses_its_earlier_follow_up_even_when_the_model_words_the_query_differently() -> None:
    getter = _Getter(_ARTICLE)
    with TemporaryDirectory() as directory:
        folder, usage = Path(directory), Path(directory) / "usage.json"
        search = brave_search(folder, getter=getter, usage_path=usage)
        search("worker dies Tanjong Rhu BTO", key="https://a.com/1")
        reused = search("a completely different wording of the query", key="https://a.com/1")
        assert getter.calls == ["worker dies Tanjong Rhu BTO"] and [c["url"] for c in reused] == ["https://straitstimes.com/a"]
        search("another query", key="https://b.com/2")  # a different signal is a new search
        assert len(getter.calls) == 2 and json.loads(usage.read_text())["used"] == 2
        entries = [json.loads(line) for line in (folder / "followups.jsonl").read_text().splitlines()]
        assert [entry["signal_url"] for entry in entries] == ["https://a.com/1", "https://b.com/2"]


def test_a_half_written_log_line_is_skipped_and_the_valid_entries_are_still_reused() -> None:
    getter = _Getter(_ARTICLE)
    with TemporaryDirectory() as directory:
        folder, usage = Path(directory), Path(directory) / "usage.json"
        brave_search(folder, getter=getter, usage_path=usage)("worker dies Tanjong Rhu BTO", key="https://a.com/1")
        log = folder / "followups.jsonl"
        log.write_text('{"query": "broken", "results": [{"ra\n' + log.read_text(), encoding="utf-8")
        again = brave_search(folder, getter=getter, usage_path=usage)("any wording", key="https://a.com/1")
        assert len(getter.calls) == 1 and len(again) == 1
        assert brave_search(folder, getter=getter, usage_path=usage)("broken", key=None) is not None  # a corrupt line never raises
