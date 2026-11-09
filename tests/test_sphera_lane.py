"""Sphera reads event messages from secrets/sphera.env. Scores stay out of the candidate and the status."""

from __future__ import annotations

from datetime import date

import pytest

from grit.acquisition import sphera_lane

DAY = date(2026, 11, 9)
SECRET = "super-secret-value"
SCORE = "sphera-score-99"


def _licence(**extra: str) -> str:
    lines = [
        "SPHERA_AUTH_URL=https://auth.example/oauth/token",
        "SPHERA_API_URL=https://api.example/v2/risk_objects",
        "SPHERA_API_URL=https://api.example/v2/score_cards",
        "SPHERA_API_URL=https://api.example/v2/event_messages",
        "SPHERA_CLIENT_ID=client",
        f"SPHERA_CLIENT_SECRET={SECRET}",
        "SPHERA_USERNAME=reader@example.com",
        "SPHERA_PASSWORD=Cusp#Lion*18",
    ]
    lines.extend(f"{key}={value}" for key, value in extra.items())
    return "\n".join(lines) + "\n"


def test_a_missing_file_is_not_configured(tmp_path) -> None:
    assert "missing" in sphera_lane.sphera_status(tmp_path / "sphera.env")
    assert sphera_lane.sphera_configured(tmp_path / "sphera.env") is False


def test_an_empty_placeholder_waits_for_a_licence(tmp_path) -> None:
    path = tmp_path / "sphera.env"
    path.write_text(sphera_lane.sphera_env_example(), encoding="utf-8")
    assert "waiting for a licence" in sphera_lane.sphera_status(path)


def test_an_api_key_file_is_not_a_licence_and_its_values_stay_out_of_the_status(tmp_path) -> None:
    path = tmp_path / "sphera.env"
    path.write_text(f"SPHERA_API_URL=https://example.invalid/api\nSPHERA_API_KEY={SECRET}\n", encoding="utf-8")
    status = sphera_lane.sphera_status(path)
    assert "waiting for a licence" in status
    assert SECRET not in status and "example.invalid" not in status


def test_a_filled_oauth_file_is_configured_without_echoing_a_secret(tmp_path) -> None:
    path = tmp_path / "sphera.env"
    path.write_text(_licence(), encoding="utf-8")
    status = sphera_lane.sphera_status(path)
    assert "configured" in status and "scores are not read" in status
    assert SECRET not in status and "Cusp#Lion*18" not in status and "reader@example.com" not in status
    assert sphera_lane.sphera_configured(path) is True


def test_event_messages_keep_the_supplier_and_drop_the_score(tmp_path) -> None:
    path = tmp_path / "sphera.env"
    path.write_text(_licence(), encoding="utf-8")
    calls: list[str] = []

    def getter(url: str, params: dict[str, str]) -> dict:
        calls.append(url)
        if url.endswith("/risk_objects"):
            return {"data": [{"id": "supplier-1", "type": "risk_object", "attributes": {"name": "Acme Metals", "total_risk_score": {"value": SCORE}}}],
                    "meta": {"total_pages": 1}}
        if url.endswith("/event_messages") and params["page[number]"] == "1":
            return {
                "data": [
                    _message("Acme plant stopped", "2026-11-08T10:00:00+00:00"),
                    _message("Old notice", "2026-11-06T10:00:00+00:00", url=""),
                ],
                "meta": {"total_pages": 3},
            }
        raise AssertionError(f"unexpected {url} {params}")

    pages = sphera_lane.alert_pages(DAY, 2, path=path, getter=getter)
    assert [page["title"] for page in pages] == ["Acme plant stopped"]
    assert pages[0]["url"] == "https://www.example.com/story"
    assert pages[0]["publisher"] == "Example News"
    assert "Affected supplier: Acme Metals." in pages[0]["text"]
    assert "Plant stopped." in pages[0]["text"]
    assert "<p>" not in pages[0]["text"] and SCORE not in pages[0]["text"] and "high" not in pages[0]["text"]
    assert not any(url.endswith("/score_cards") for url in calls)
    assert sum(url.endswith("/event_messages") for url in calls) == 1


def test_a_failed_auth_does_not_repeat_the_password(tmp_path, monkeypatch) -> None:
    path = tmp_path / "sphera.env"
    path.write_text(_licence(), encoding="utf-8")

    class Response:
        status_code = 401

        def json(self) -> dict:
            return {"error": "invalid_grant"}

    import requests

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response())
    with pytest.raises(sphera_lane.SpheraFetchError) as caught:
        sphera_lane.alert_pages(DAY, 2, path=path)
    assert "HTTP 401" in str(caught.value)
    assert "Cusp#Lion*18" not in str(caught.value) and SECRET not in str(caught.value)


def _message(subject: str, reported_at: str, *, url: str = "https://www.example.com/story?utm_source=sphera") -> dict:
    return {
        "id": "message-1",
        "type": "event_message",
        "attributes": {
            "subject": subject,
            "body": "<p>Plant <b>stopped</b>.</p>",
            "source_name": "Example News",
            "source_url": url,
            "reported_at": reported_at,
            "priority": "high",
        },
        "relationships": {"affected_risk_objects": {"data": [{"type": "risk_object", "id": "supplier-1"}]}},
    }
