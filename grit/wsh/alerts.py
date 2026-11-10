"""Pack-local WSH alerts (spec 47). This module only drafts alerts. It does not set business-report RED status."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.pool import Pool, utc_now
from grit.wsh.scope import classify, match_alert


def _address(profile: Any) -> str:
    name = profile.name or profile.role
    return f"{name} <{profile.email}>" if profile.email else f"{name} (address not recorded)"


def draft_wsh_alerts(
    pool: Pool,
    run_id: str,
    pack: Any,
    profiles: Sequence[Any],
    folder: Path,
    *,
    signals: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Draft serious-incident and regulatory alerts for James Lek. Method papers never alert."""
    patterns = pack.alert_patterns or {}
    scope = pack.scope or {}
    recipients = [
        profile for profile in profiles
        if getattr(profile, "receives_red", False) and pack.pack in (profile.red_alerts.get("packs") or profile.packs)
    ]
    if not recipients:
        recipients = [profile for profile in profiles if pack.pack in profile.packs]
    rows = list(signals) if signals is not None else []
    if not rows:
        for assessment in pool.assessments(run_id, kept_only=False):
            rows.append({
                "signal_id": assessment["signal_id"], "title": assessment.get("title"), "text": assessment.get("text"),
                "publisher": assessment.get("publisher"), "url": assessment.get("url"), "published_at": assessment.get("published_at"),
            })
    drafts: list[dict[str, Any]] = []
    folder.mkdir(parents=True, exist_ok=True)
    for row in rows:
        classification = classify(row.get("title") or "", row.get("text") or "", scope=scope)
        hit = match_alert(row.get("title") or "", row.get("text") or "", classification, patterns)
        if not hit:
            continue
        signal_id = row["signal_id"]
        alert_id = f"WSH-{hit['kind']}-{run_id}-{signal_id}"
        if pool.one("SELECT 1 FROM alerts a JOIN runs r ON r.run_id = a.run_id WHERE a.signal_id = ? AND r.pack = ?", (signal_id, pack.pack)):
            continue
        message = EmailMessage()
        message["Subject"] = f"[GRIT-Scan WSH alert — draft] {hit.get('label')}: {row.get('title')}"
        message["To"] = ", ".join(_address(profile) for profile in recipients) or "James Lek (address not recorded)"
        message["X-GRIT-Status"] = "DRAFT - not sent. A named person must review and send."
        message["X-GRIT-Alert-Kind"] = str(hit.get("kind") or "")
        body = [
            "This is a GRIT-Scan Workplace Safety & Health alert draft. It was raised by a pack-local rule and has not been sent.",
            "It does not set business-report RED status.",
            "",
            f"Event: {row.get('title')}",
            f"Source: {row.get('publisher')}, {str(row.get('published_at') or 'date not stated')[:10]}. {row.get('url')}",
            f"Rule: {hit.get('rule_id')} ({hit.get('label')}). Matched: '{hit.get('matched')}'.",
            *(f"- {reason}" for reason in hit.get("reasons") or []),
            "",
            "Before sending: open the source, confirm the event or regulatory change, and edit this text.",
        ]
        message.set_content("\n".join(body))
        path = folder / f"{alert_id}.eml"
        path.write_bytes(bytes(message))
        pool.insert("alerts", {
            "alert_id": alert_id, "run_id": run_id, "signal_id": signal_id,
            "recipients_json": [profile.profile_id for profile in recipients],
            "rule_json": dict(hit), "status": "DRAFT", "draft_path": str(path), "created_at": utc_now(),
        })
        drafts.append({"alert_id": alert_id, "path": str(path), "title": row.get("title"), "kind": hit.get("kind")})
    return drafts
