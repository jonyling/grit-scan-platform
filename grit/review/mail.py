"""Mail a daily review pack through Microsoft Graph (spec 48). The files are written whether or not mail is configured."""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path
from typing import Any, Mapping

from grit.pool import Pool
from grit.profiles import Profile
from grit.review.config import BP, MANAGER, ReviewConfig
from grit.review.graph import Attachment, GraphError
from grit.review.pack import FINAL, PACK_TITLES, Pack, build_pack, write_pack

CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
LISTED = 15


def recipients(config: ReviewConfig, stage: str, profiles: Mapping[str, Profile]) -> tuple[list[str], list[str]]:
    """(addresses, profile ids with no address). The BP pack goes to the BP, the Manager pack to the Manager, the final pack to both."""
    wanted = {BP: [config.bp.profile_id], MANAGER: [config.manager.profile_id], FINAL: [config.bp.profile_id, config.manager.profile_id]}[stage]
    found, gaps = [], []
    for profile_id in wanted:
        profile = profiles.get(profile_id)
        address = str(getattr(profile, "email", None) or "").strip()
        (found if "@" in address else gaps).append(address if "@" in address else profile_id)
    return found, gaps


def subject(pack: Pack) -> str:
    count = len(pack.rows)
    if pack.stage == BP:
        tail = f"{count} signal(s), decide by {pack.config.bp.close_at:%H:%M} SGT"
    elif pack.stage == MANAGER:
        tail = f"{count} signal(s) approved by the R&A BP, decide by {pack.config.manager.close_at:%H:%M} SGT"
    else:
        tail = f"{count} signal(s) approved by both" if count else "no signal approved by both"
    return f"[GRIT-Scan] {pack.config.business_area} daily review, {PACK_TITLES[pack.stage]}, {pack.day:%d %b %Y}: {tail}"


def body(pack: Pack, list_url: str | None) -> str:
    parts = [f"<p><b>{escape(pack.title)}</b></p>"] + [f"<p>{escape(line)}</p>" for line in pack.lines()]
    if list_url and pack.stage != FINAL:
        parts.append(f'<p><a href="{escape(list_url, quote=True)}">Open the review list in SharePoint</a> to approve or reject each signal.</p>')
    if pack.rows:
        parts.append("<ol>" + "".join(
            f'<li><a href="{escape(row.url, quote=True)}">{escape(row.title)}</a> ({escape(row.publisher)}, P{row.tier}{", RED" if row.red else ""})</li>'
            for row in pack.rows[:LISTED]) + "</ol>")
        if len(pack.rows) > LISTED:
            parts.append(f"<p>{len(pack.rows) - LISTED} more in the attached files.</p>")
    parts.append("<p>The Word, PowerPoint and Excel files are attached. GRIT-Scan sent this message; a named person approves each signal.</p>")
    return "\n".join(parts)


def mail_pack(pool: Pool, config: ReviewConfig, day: date, stage: str, profiles: Mapping[str, Profile], *, client: Any | None,
              list_urls: Mapping[str, str | None] | None = None, root: Path | None = None) -> dict[str, Any]:
    pack = build_pack(pool, config, day, stage)
    files = write_pack(pack, root)
    outcome: dict[str, Any] = {"rows": len(pack.rows), "files": {kind: str(path) for kind, path in files.items()}}
    if pack.approved_hash:
        outcome["approved_hash"] = pack.approved_hash
    to, gaps = recipients(config, stage, profiles)
    if gaps:
        return {**outcome, "status": "NOT_CONFIGURED", "reason": "no email address in the profile of " + ", ".join(gaps)}
    if client is None:
        return {**outcome, "status": "NOT_CONFIGURED", "reason": "Graph mail is not configured"}
    attachments = [Attachment(path.name, path.read_bytes(), CONTENT_TYPES[kind]) for kind, path in files.items()]
    try:
        route = client.send_mail(to, subject(pack), body(pack, (list_urls or {}).get(stage)), attachments)
    except GraphError as exc:
        # After an unclear answer the mail may have gone. Sending again risks a duplicate, so the operator checks Sent Items.
        return {**outcome, "status": "UNCERTAIN" if exc.uncertain else "FAILED", "error": str(exc)}
    return {**outcome, "status": "DONE", "to": to, "route": route}
