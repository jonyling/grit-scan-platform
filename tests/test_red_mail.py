"""Daily RED mail goes only to the configured duty mailboxes, and only when Graph mail is configured."""

from __future__ import annotations

from grit.pool import Pool
from grit.report.red_mail import TIMEOUT_SECONDS, send_daily_red_alerts

ENV = {
    "GRAPH_TENANT_ID": "tenant",
    "GRAPH_CLIENT_ID": "client",
    "GRAPH_CLIENT_SECRET": "secret",
    "GRAPH_MAIL_SENDER": "duty@example.com",
    "GRIT_RED_MAIL_TO": "duty@example.com, cover@example.com",
}


def _draft(pool: Pool, folder) -> str:
    signal_id = pool.add_signal(
        url="https://example.com/pandemic", title="WHO declares a pandemic", text="The agency declared a pandemic.",
        publisher="reuters.com", published_at="2026-10-28T03:00:00Z", lane="FEED", content_hash="hash-1",
    )
    path = folder / "draft.eml"
    path.write_text("Subject: draft\n\nEvent: WHO declares a pandemic\nTo the profiles on the draft.\n", encoding="utf-8")
    pool.insert("alerts", {
        "alert_id": "RED-1", "run_id": "run-1", "signal_id": signal_id, "recipients_json": ["wilson"],
        "rule_json": {"rule_id": "RED-PANDEMIC", "label": "Pandemic declared"}, "status": "DRAFT",
        "draft_path": str(path), "created_at": "2026-10-28T04:00:00Z",
    })
    return signal_id


def _env(monkeypatch, **overrides) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in {**ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_mail_is_not_sent_when_a_setting_is_missing(tmp_path, monkeypatch) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    _draft(pool, tmp_path)
    _env(monkeypatch, GRIT_RED_MAIL_TO=None)
    calls = []
    outcome = send_daily_red_alerts(pool, "run-1", poster=lambda *a, **k: calls.append(1) or (202, ""), token_getter=lambda env: "token")
    assert outcome["status"] == "NOT_CONFIGURED" and calls == []
    assert pool.one("SELECT status FROM alerts WHERE alert_id = 'RED-1'")["status"] == "DRAFT"


def test_a_bad_recipient_is_refused_and_nothing_is_sent(tmp_path, monkeypatch) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    _draft(pool, tmp_path)
    _env(monkeypatch, GRIT_RED_MAIL_TO="not a mailbox")
    outcome = send_daily_red_alerts(pool, "run-1", poster=lambda *a, **k: (202, ""), token_getter=lambda env: "token")
    assert outcome["status"] == "REFUSED" and "mailbox" in outcome["error"]
    assert pool.one("SELECT status FROM alerts")["status"] == "DRAFT"


def test_graph_send_goes_to_the_duty_team_once_and_uses_a_timeout(tmp_path, monkeypatch) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    _draft(pool, tmp_path)
    _env(monkeypatch)
    sent = []

    def poster(url, *, headers, payload, timeout):
        sent.append({"url": url, "timeout": timeout, "payload": payload, "auth": headers["Authorization"]})
        return 202, ""

    outcome = send_daily_red_alerts(pool, "run-1", poster=poster, token_getter=lambda env: "token-value")
    assert outcome == {"status": "SENT", "sent": 1, "failed": 0}
    assert sent[0]["timeout"] == TIMEOUT_SECONDS
    assert sent[0]["url"].endswith("/users/duty%40example.com/sendMail")
    recipients = [item["emailAddress"]["address"] for item in sent[0]["payload"]["message"]["toRecipients"]]
    assert recipients == ["duty@example.com", "cover@example.com"]
    assert "wilson" not in sent[0]["payload"]["message"]["subject"]
    assert "WHO declares a pandemic" in sent[0]["payload"]["message"]["body"]["content"]
    assert "duty team" in sent[0]["payload"]["message"]["body"]["content"]
    assert pool.one("SELECT status, decided_by FROM alerts") == {"status": "SENT_TO_DUTY", "decided_by": "graph-mail"}
    again = send_daily_red_alerts(pool, "run-1", poster=poster, token_getter=lambda env: "token-value")
    assert again["status"] == "NONE" and len(sent) == 1


def test_a_graph_refusal_leaves_the_draft(tmp_path, monkeypatch) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    _draft(pool, tmp_path)
    _env(monkeypatch)
    outcome = send_daily_red_alerts(pool, "run-1", poster=lambda *a, **k: (403, "no"), token_getter=lambda env: "token")
    assert outcome["status"] == "FAILED" and outcome["error"] == "HTTP 403" and outcome["sent"] == 0
    assert pool.one("SELECT status FROM alerts")["status"] == "DRAFT"


def test_graph_post_uses_a_timeout(monkeypatch) -> None:
    import requests

    from grit.report.red_mail import _post

    seen = {}

    class Response:
        status_code = 202
        text = ""

    def fake_post(url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        seen["url"] = url
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    status, _text = _post("https://graph.microsoft.com/v1.0/users/a/sendMail", headers={}, payload={}, timeout=TIMEOUT_SECONDS)
    assert status == 202 and seen["timeout"] == TIMEOUT_SECONDS
