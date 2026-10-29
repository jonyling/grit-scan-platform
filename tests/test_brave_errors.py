"""Brave's error reasons reach the log, so an invalid key does not read like a bad query."""

from types import SimpleNamespace

from grit.acquisition.brave import _reason


def test_an_invalid_key_says_so_and_says_where_to_fix_it() -> None:
    body = {"type": "ErrorResponse", "error": {"status": 422, "detail": "The provided API key is invalid.", "code": "SUBSCRIPTION_TOKEN_INVALID"}}
    text = _reason(SimpleNamespace(json=lambda: body))
    assert text.startswith(" (SUBSCRIPTION_TOKEN_INVALID: The provided API key is invalid.).") and "BRAVE_SEARCH_API_KEY" in text


def test_a_body_that_is_not_json_adds_nothing() -> None:
    def broken() -> dict:
        raise ValueError("not JSON")

    assert _reason(SimpleNamespace(json=broken)) == ""
    assert _reason(SimpleNamespace(json=lambda: {"error": {"code": "RATE_LIMITED", "detail": "Too many requests"}})) == " (RATE_LIMITED: Too many requests)."
