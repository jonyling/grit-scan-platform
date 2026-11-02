"""RED alert drafts. One .eml file per RED signal, addressed to every profile that receives RED for the pack.

This module does not send the draft. A daily run may send it to the R&A duty team through Microsoft Graph when mail is
configured (`grit.report.red_mail`). A named person forwards it or dismisses it. A weekly run leaves it as a draft.
"""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.core.red_alert import red_recipients
from grit.pool import Pool, utc_now
from grit.report.brief import fmt_date


def _address(profile: Any) -> str:
    name = profile.name or profile.role
    return f"{name} <{profile.email}>" if profile.email else f"{name} (address not recorded)"


def draft_red_alerts(pool: Pool, run_id: str, pack: str, profiles: Sequence[Any], folder: Path) -> list[dict[str, Any]]:
    recipients = [profile for profile in profiles if profile.profile_id in red_recipients(profiles, pack)]
    drafts = []
    reasoned = ((pool.run(run_id) or {}).get("config") or {}).get("weekly_mode") == "reasoned"
    for row in pool.assessments(run_id, kept_only=not reasoned):
        if not row.get("red"):
            continue
        alert_id = f"RED-{run_id}-{row['signal_id']}"
        # One draft per signal and pack: a page read again by a later daily run, or by the weekly scan, is not drafted twice.
        if pool.one("SELECT 1 FROM alerts a JOIN runs r ON r.run_id = a.run_id WHERE a.signal_id = ? AND r.pack = ?", (row["signal_id"], pack)):
            continue
        rule: Mapping[str, Any] = row.get("red_rule") or {}
        message = EmailMessage()
        message["Subject"] = f"[GRIT-Scan RED — draft] {rule.get('label') or 'Urgent event'}: {row['title']}"
        message["To"] = ", ".join(_address(profile) for profile in recipients)
        message["X-GRIT-Status"] = "DRAFT - not sent. A named person must review and send."
        outlets = ", ".join(item.get("publisher") or "" for item in (row["link"].get("also_reported_by") or []))
        body = [
            "This is a GRIT-Scan RED alert draft. It was raised by rule and has not been sent.",
            "",
            f"Event: {row['title']}",
            f"Source: {row['publisher']}, {fmt_date(row['published_at']) or 'date not stated'}. {row['url']}",
            f"Also carried by: {outlets or 'no other outlet found'}.",
            f"Rule: {rule.get('rule_id')} ({rule.get('label')}). Matched: '{rule.get('matched')}'.",
            *(f"- {reason}" for reason in rule.get("reasons") or []),
            "",
            f"Why it matters to STE: {row.get('relevance_basis') or 'not established'}",
            f"Harm scorer: {row.get('harm_verdict') or 'not established'}. Severity stays not established until a structured observation is recorded.",
            "",
            "Before sending: open the source, confirm the event, and edit this text. Record the decision in the GRIT-Scan dashboard.",
        ]
        message.set_content("\n".join(body))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{alert_id}.eml"
        path.write_bytes(bytes(message))
        pool.insert("alerts", {
            "alert_id": alert_id, "run_id": run_id, "signal_id": row["signal_id"], "recipients_json": [profile.profile_id for profile in recipients],
            "rule_json": dict(rule), "status": "DRAFT", "draft_path": str(path), "created_at": utc_now(),
        })
        drafts.append({"alert_id": alert_id, "path": str(path), "title": row["title"]})
    return drafts
