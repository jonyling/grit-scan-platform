"""A Gemini call the server never answers must fail after a timeout, not hang the weekly run."""

from __future__ import annotations

from grit.agents.gemini import DEFAULT_TIMEOUT_SECONDS, _client, timeout_ms


def test_every_client_has_a_timeout(monkeypatch) -> None:
    assert timeout_ms({}) == DEFAULT_TIMEOUT_SECONDS * 1000
    assert timeout_ms({"GEMINI_TIMEOUT_SECONDS": "30"}) == 30000
    assert timeout_ms({"GEMINI_TIMEOUT_SECONDS": "zero"}) == DEFAULT_TIMEOUT_SECONDS * 1000
    client = _client({"GOOGLE_API_KEY": "AIza-test-key-not-real"})
    assert client._api_client._http_options.timeout == DEFAULT_TIMEOUT_SECONDS * 1000
