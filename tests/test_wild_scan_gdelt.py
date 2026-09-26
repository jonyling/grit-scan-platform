from acquisition.gdelt import GdeltFetchError, articles_from_payload, collect_wild_scan


def _payload(*articles: dict) -> dict:
    return {"articles": list(articles)}


def test_gdelt_artlist_becomes_wild_candidates_without_tone() -> None:
    payload = _payload(
        {
            "url": "https://www.reuters.com/world/rail",
            "title": "Vietnam opens a rail programme",
            "seendate": "20260925T153000Z",
            "domain": "reuters.com",
            "tone": -4.2,
            "goldstein": 3.1,
            "socialimage": "https://img.example/a.jpg",
            "language": "English",
        }
    )
    candidates = collect_wild_scan(
        "Vietnam rail",
        fetched_at="2026-09-26T00:00:00Z",
        getter=lambda url, params: payload,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    record = candidate.as_record()
    assert candidate.source_lane == "WILD"
    assert candidate.url == "https://www.reuters.com/world/rail"
    assert candidate.publisher == "reuters.com"
    assert candidate.text == ""
    assert candidate.published_at is None
    assert candidate.seen_at == "2026-09-25T15:30:00Z"
    assert "tone" not in record
    assert "goldstein" not in record
    assert "socialimage" not in record
    mapped = articles_from_payload(payload)[0]
    assert "tone" not in mapped
    assert mapped["published_at"] is None


def test_queries_stay_separate_and_duplicate_urls_collapse() -> None:
    calls: list[str] = []

    def getter(url: str, params: dict[str, str]) -> dict:
        calls.append(params["query"])
        assert params["mode"] == "artlist"
        assert params["timespan"] == "7d"
        assert "timelinetone" not in params["mode"]
        title = "First" if len(calls) == 1 else "Repeat"
        return _payload({"url": "https://www.example.com/story", "title": title, "domain": "example.com"})

    candidates = collect_wild_scan(
        ["condition one", "condition two"],
        fetched_at="2026-09-26T00:00:00Z",
        getter=getter,
    )
    assert calls == ["condition one", "condition two"]
    assert len(candidates) == 1
    assert candidates[0].title == "First"


def test_failed_fetch_is_not_an_empty_week() -> None:
    def getter(url: str, params: dict[str, str]) -> dict:
        raise GdeltFetchError("GDELT returned HTTP 429")

    try:
        collect_wild_scan("rail", fetched_at="2026-09-26T00:00:00Z", getter=getter)
    except GdeltFetchError as exc:
        assert "429" in str(exc)
    else:
        raise AssertionError("a failed GDELT fetch was treated as no news")


def test_index_pages_and_missing_queries_are_refused() -> None:
    payload = _payload(
        {"url": "https://www.gdeltproject.org/about", "title": "About GDELT", "domain": "gdeltproject.org"},
        {"url": "not-a-url", "title": "Broken"},
    )
    assert articles_from_payload(payload) == []
    try:
        collect_wild_scan("  ", fetched_at="2026-09-26T00:00:00Z", getter=lambda url, params: payload)
    except ValueError as exc:
        assert "one query" in str(exc)
    else:
        raise AssertionError("an empty wild-scan query was accepted")
