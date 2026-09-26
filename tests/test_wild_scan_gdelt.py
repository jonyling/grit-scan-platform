import hashlib
import io
import zipfile

from acquisition.gdelt import (
    GdeltFetchError,
    LASTUPDATE_URL,
    articles_from_payload,
    collect_export_scan,
    collect_wild_scan,
)


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


def _export_row(*, url: str, actor: str = "", geo: str = "", added: str = "20260926121500", tone: str = "-4.2") -> str:
    fields = [""] * 61
    fields[6] = actor
    fields[30] = "3.1"
    fields[34] = tone
    fields[52] = geo
    fields[59] = added
    fields[60] = url
    return "\t".join(fields)


def _export_zip(*lines: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("20260926121500.export.CSV", "\n".join(lines))
    return buffer.getvalue()


def _listing(digest: str) -> bytes:
    url = "http://data.gdeltproject.org/gdeltv2/20260926121500.export.CSV.zip"
    return (
        f"10 {digest} {url}\n"
        "20 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa http://data.gdeltproject.org/gdeltv2/20260926121500.mentions.CSV.zip\n"
        "30 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb http://data.gdeltproject.org/gdeltv2/20260926121500.gkg.csv.zip\n"
    ).encode("utf-8")


def test_export_scan_filters_locally_and_drops_tone() -> None:
    payload = _export_zip(
        _export_row(url="https://www.reuters.com/world/strait", actor="CHINA", geo="Taiwan Strait"),
        _export_row(url="https://www.example.com/other", actor="FRANCE", geo="Paris"),
        _export_row(url="https://www.gdeltproject.org/about", actor="TAIWAN", geo="Taiwan Strait"),
    )
    digest = hashlib.md5(payload).hexdigest()
    calls: list[str] = []

    def getter(url: str) -> bytes:
        calls.append(url)
        if url == LASTUPDATE_URL:
            return _listing(digest)
        assert url.endswith(".export.CSV.zip")
        assert "mentions" not in url and "gkg" not in url
        assert "api.gdeltproject.org" not in url
        return payload

    hits = collect_export_scan(["Taiwan Strait", "France rail"], max_records=10, getter=getter)
    assert [hit.url for hit in hits] == ["https://www.reuters.com/world/strait"]
    assert hits[0].publisher == "reuters.com"
    assert hits[0].seen_at == "2026-09-26T12:15:00Z"
    assert hits[0].matched_queries == ("Taiwan Strait",)
    assert not hasattr(hits[0], "title")
    assert "tone" not in hits[0].__dict__
    assert calls[0] == LASTUPDATE_URL
    assert len(calls) == 2


def test_export_checksum_failure_is_not_an_empty_slice() -> None:
    payload = _export_zip(_export_row(url="https://www.reuters.com/a", geo="Taiwan Strait"))

    def getter(url: str) -> bytes:
        if url == LASTUPDATE_URL:
            return _listing("0" * 32)
        return payload

    try:
        collect_export_scan("Taiwan Strait", getter=getter)
    except GdeltFetchError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("a bad GDELT checksum was treated as no news")


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
