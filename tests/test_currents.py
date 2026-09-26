from acquisition.currents import CurrentsFetchError, SEARCH_URL, articles_from_payload, collect_currents


def _payload(*articles: dict) -> dict:
    return {"status": "ok", "news": list(articles)}


def test_currents_news_becomes_a_candidate_without_a_rank() -> None:
    payload = _payload(
        {
            "id": "ac4062d1-5fbb-4784-bf1d-6b50f5f643a0",
            "title": "Strait traffic slows",
            "description": "Ships waited outside the port.",
            "url": "https://www.reuters.com/world/strait",
            "author": "reuters.com",
            "image": "https://img.example/a.jpg",
            "category": ["general"],
            "published": "2026-09-07 14:22:08 +0000",
        }
    )
    calls: list[str] = []

    def getter(url: str, params: dict[str, str]) -> dict:
        calls.append(url)
        assert params["keywords"] == "Taiwan Strait"
        assert "apiKey" not in params
        assert "gdelt" not in url
        return payload

    candidates = collect_currents("Taiwan Strait", fetched_at="2026-09-26T00:00:00Z", getter=getter)
    assert calls == [SEARCH_URL]
    assert len(candidates) == 1
    candidate = candidates[0]
    record = candidate.as_record()
    assert candidate.source_lane == "WILD"
    assert candidate.publisher == "reuters.com"
    assert candidate.text == "Ships waited outside the port."
    assert candidate.published_at == "2026-09-07T14:22:08Z"
    assert "image" not in record
    assert "category" not in record
    assert "id" not in record
    mapped = articles_from_payload(payload)[0]
    assert "image" not in mapped


def test_currents_queries_stay_separate_and_a_third_pull_is_refused() -> None:
    calls: list[str] = []

    def getter(url: str, params: dict[str, str]) -> dict:
        calls.append(params["keywords"])
        assert "gdeltproject.org" not in url
        return _payload({"title": params["keywords"], "url": f"https://www.example.com/{len(calls)}"})

    candidates = collect_currents(["one condition", "two condition"], fetched_at="2026-09-26T00:00:00Z", getter=getter)
    assert calls == ["one condition", "two condition"]
    assert len(candidates) == 2
    try:
        collect_currents(["one", "two", "three"], fetched_at="2026-09-26T00:00:00Z", getter=getter)
    except ValueError as exc:
        assert "two pulls" in str(exc)
    else:
        raise AssertionError("Currents accepted a firehose")
    assert calls == ["one condition", "two condition"]


def test_currents_failure_is_not_an_empty_week() -> None:
    def getter(url: str, params: dict[str, str]) -> dict:
        raise CurrentsFetchError("Currents returned HTTP 401")

    try:
        collect_currents("rail", fetched_at="2026-09-26T00:00:00Z", getter=getter)
    except CurrentsFetchError as exc:
        assert "401" in str(exc)
        assert "CURRENTS_API_KEY" not in str(exc)
    else:
        raise AssertionError("a failed Currents fetch was treated as no news")


def test_missing_currents_key_fails_before_a_call() -> None:
    import os

    saved = os.environ.pop("CURRENTS_API_KEY", None)
    try:
        from acquisition.currents import require_key

        require_key()
    except CurrentsFetchError as exc:
        assert str(exc) == "Missing environment variable: CURRENTS_API_KEY"
    else:
        raise AssertionError("a missing Currents key was accepted")
    finally:
        if saved is not None:
            os.environ["CURRENTS_API_KEY"] = saved
