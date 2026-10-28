"""Send a daily run's RED drafts to the R&A duty team through Microsoft Graph.

Nothing is sent unless every mail setting is present. Recipients are the addresses in GRIT_RED_MAIL_TO and no one else.
A missing address stays missing: this module never invents one. A named person forwards the message or dismisses it.
"""

from __future__ import annotations

import os
import re
from email import message_from_bytes, policy
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from grit.pool import Pool, utc_now

TIMEOUT_SECONDS = 60
GRAPH_ENV = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_MAIL_SENDER")
TO_ENV = "GRIT_RED_MAIL_TO"
SCOPE = "https://graph.microsoft.com/.default"
_MAILBOX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NOTICE = (
    "GRIT-Scan sent this RED alert to the R&A duty team only.",
    "A named person checks the source and forwards it. It has not been sent to anyone else.",
    "",
)
Poster = Callable[..., tuple[int, str]]


class RedMailError(RuntimeError):
    """Graph mail did not accept the message. The text does not include a credential."""


def duty_recipients() -> list[str] | None:
    """The duty-team mailboxes, or None when the setting is blank. An unusable address is an error, not a guess."""
    raw = os.environ.get(TO_ENV, "")
    addresses = [item.strip() for item in raw.split(",") if item.strip()]
    if not addresses:
        return None
    if any(_MAILBOX.fullmatch(address) is None for address in addresses):
        raise RedMailError("GRIT_RED_MAIL_TO is not a list of mailboxes")
    return addresses


def graph_env() -> dict[str, str] | None:
    values = {name: os.environ.get(name, "").strip() for name in GRAPH_ENV}
    if any(not value for value in values.values()):
        return None
    return values


def _token(env: dict[str, str]) -> str:
    import msal

    app = msal.ConfidentialClientApplication(
        env["GRAPH_CLIENT_ID"], authority="https://login.microsoftonline.com/" + env["GRAPH_TENANT_ID"],
        client_credential=env["GRAPH_CLIENT_SECRET"], timeout=TIMEOUT_SECONDS,
    )
    result = app.acquire_token_for_client(scopes=[SCOPE])
    token = result.get("access_token")
    if not token:
        raise RedMailError("Token acquisition failed: " + str(result.get("error") or "unknown_error"))
    return str(token)


def _post(url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> tuple[int, str]:
    import requests

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise RedMailError("Graph request failed") from exc
    return response.status_code, ""


def _body(path: str | None, title: str) -> str:
    text = ""
    if path:
        try:
            message = message_from_bytes(Path(path).read_bytes(), policy=policy.default)
            content = message.get_content()
            text = content if isinstance(content, str) else ""
        except (OSError, KeyError, LookupError):
            text = ""
    return "\n".join([*NOTICE, text or f"Event: {title}", "", "Record the forward or the dismissal in the GRIT-Scan dashboard."])


def send_daily_red_alerts(
    pool: Pool, run_id: str, *, poster: Poster | None = None, token_getter: Callable[[dict[str, str]], str] | None = None,
) -> dict[str, Any]:
    """Send this run's DRAFT alerts. Missing settings leave every draft unsent. A sent alert is not sent again."""
    try:
        recipients = duty_recipients()
        env = graph_env()
    except RedMailError as exc:
        return {"status": "REFUSED", "sent": 0, "failed": 0, "error": str(exc)}
    if recipients is None or env is None:
        return {"status": "NOT_CONFIGURED", "sent": 0, "failed": 0}
    drafts = pool.rows("SELECT * FROM alerts WHERE run_id = ? AND status = 'DRAFT'", (run_id,))
    if not drafts:
        return {"status": "NONE", "sent": 0, "failed": 0}
    try:
        token = (token_getter or _token)(env)
    except RedMailError as exc:
        return {"status": "FAILED", "sent": 0, "failed": len(drafts), "error": str(exc)[:120]}
    send = poster or _post
    url = "https://graph.microsoft.com/v1.0/users/" + quote(env["GRAPH_MAIL_SENDER"]) + "/sendMail"
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    sent = failed = 0
    error = None
    for row in drafts:
        rule = row.get("rule") or {}
        label = rule.get("label") or "Urgent event"
        signal = pool.one("SELECT title FROM signals WHERE signal_id = ?", (row["signal_id"],)) or {}
        title = str(signal.get("title") or label)
        payload = {
            "message": {
                "subject": f"[GRIT-Scan RED] {label}: {title}"[:180],
                "body": {"contentType": "Text", "content": _body(row.get("draft_path"), title)},
                "toRecipients": [{"emailAddress": {"address": address}} for address in recipients],
            },
            "saveToSentItems": True,
        }
        try:
            status, _text = send(url, headers=headers, payload=payload, timeout=TIMEOUT_SECONDS)
        except RedMailError as exc:
            failed += 1
            error = str(exc)[:120]
            continue
        if status not in {202, 200}:
            failed += 1
            error = f"HTTP {status}"
            continue
        with pool.transaction() as conn:
            conn.execute(
                "UPDATE alerts SET status = ?, decided_by = ?, decided_at = ? WHERE alert_id = ?",
                ("SENT_TO_DUTY", "graph-mail", utc_now(), row["alert_id"]),
            )
        sent += 1
    outcome: dict[str, Any] = {"status": "SENT" if sent and not failed else "PARTIAL" if sent else "FAILED", "sent": sent, "failed": failed}
    if error and failed:
        outcome["error"] = error
    return outcome
