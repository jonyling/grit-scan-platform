"""The SharePoint side of the daily review (spec 48): publish a stage's rows, read the reviewers' decisions, close at the deadline.

Each stage has its own list, and only that stage's reviewer and the R&A owners can open it. A row's Approve and Reject buttons
set Decision, DecidedBy (@me) and DecidedAt (@now) in place (docs/review/decision-column.json). GRIT reads the rows; nothing
calls in. A decision counts when DecidedAt, or the row's last change, is at or before the stage deadline. At the deadline
GRIT freezes the decisions, records a missing one as NO_DECISION, locks the rows and writes APPROVE and REJECT to the
reviewer's feedback, which is the Profiler's memory. NO_DECISION is not feedback.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from grit.pool import Pool, utc_now
from grit.profiles import Profile
from grit.profiles.emphasis import profile_effect
from grit.profiles.memory import build_memory
from grit.review.argue import withheld_text
from grit.review.batch import decisions, items, protected, records
from grit.review.config import BP, MANAGER, ReviewConfig, Stage, batch_id
from grit.review.graph import GraphError

DECISIONS = {"Approved": "APPROVE", "Rejected": "REJECT"}
LABELS = {"APPROVE": "Approved", "REJECT": "Rejected", "NO_DECISION": "No decision by the deadline"}
TEXT_MAX = 4000

# Internal column names, in the order the setup guide lists them. The site owner creates them once per list.
COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("Title", "Single line of text", "The page's headline."),
    ("SignalId", "Single line of text", "GRIT's key for the signal. Indexed. Hidden from the default view."),
    ("ReviewDate", "Single line of text", "The working day, YYYY-MM-DD. Indexed."),
    ("Position", "Number", "Order within the day: tier, RED, then the reviewer's memory."),
    ("Source", "Single line of text", "The outlet."),
    ("SourceUrl", "Multiple lines of text (plain)", "The page. The view formats it as a link."),
    ("Published", "Single line of text", "When the outlet published it."),
    ("Tier", "Number", "Priority tier, 1 is highest."),
    ("Red", "Yes/No", "A RED event. Its alert has already gone to the duty team."),
    ("RiskLevel", "Single line of text", ""),
    ("Trend", "Single line of text", ""),
    ("Evidence", "Single line of text", "Evidence confidence."),
    ("Relevance", "Multiple lines of text (plain)", "Why the rules kept it."),
    ("CaseFor", "Multiple lines of text (plain)", "Gemini Proposer."),
    ("CaseAgainst", "Multiple lines of text (plain)", "Gemini Critic."),
    ("CriticView", "Single line of text", "HOLDS, WEAK or FAILS."),
    ("MemoryNote", "Multiple lines of text (plain)", "How this reviewer's past decisions moved the row."),
    ("BpDecision", "Multiple lines of text (plain)", "Manager list only: the R&A BP's decision and comment."),
    ("Decision", "Choice: Pending, Approved, Rejected (default Pending)", "Formatted with the Approve and Reject buttons."),
    ("Comment", "Multiple lines of text (plain)", "Optional. Read into the reviewer's feedback."),
    ("DecidedBy", "Person", "Set by the button (@me)."),
    ("DecidedAt", "Date and time", "Set by the button (@now)."),
    ("Locked", "Yes/No (default No)", "Set by GRIT at the deadline. The buttons hide when it is Yes."),
)


def _clip(value: Any, limit: int = TEXT_MAX) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def ordered_for(pool: Pool, profile: Profile, rows: Sequence[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], str]]:
    """Rows in the order this reviewer sees them, each with the memory's reasons. Memory reorders within a tier and never
    hides a row: a reviewer must see every signal to decide on it."""
    feedback = pool.feedback(profile.profile_id)
    memory = build_memory(profile.profile_id, feedback, pool.latest_assessments(row["signal_id"] for row in feedback)) if feedback else None
    scored = []
    for index, row in enumerate(rows):
        effect = profile_effect(row, profile, protected=protected(row), memory=memory)
        scored.append(((row.get("tier") or 9, -int(bool(row.get("red"))), -effect.affinity, index), row, " ".join(effect.reasons)))
    return [(row, note) for _key, row, note in sorted(scored, key=lambda item: item[0])]


def row_fields(record: Mapping[str, Any], *, day: date, position: int, memory_note: str, bp: Mapping[str, Any] | None = None) -> dict[str, Any]:
    case_for, case_against, view = withheld_text(record.get("argument"))
    fields: dict[str, Any] = {
        "Title": _clip(record.get("title"), 255),
        "SignalId": record["signal_id"],
        "ReviewDate": day.isoformat(),
        "Position": position,
        "Source": _clip(record.get("publisher"), 255),
        "SourceUrl": _clip(record.get("url")),
        "Published": _clip(record.get("published_at"), 40),
        "Tier": record.get("tier") or 9,
        "Red": bool(record.get("red")),
        "RiskLevel": _clip(record.get("risk_level"), 40),
        "Trend": _clip(record.get("trend"), 40),
        "Evidence": _clip(record.get("evidence_confidence"), 40),
        "Relevance": _clip(record.get("relevance_basis")),
        "CaseFor": _clip(case_for),
        "CaseAgainst": _clip(case_against),
        "CriticView": view,
        "MemoryNote": _clip(memory_note),
        "Decision": "Pending",
        "Locked": False,
    }
    if bp is not None:
        fields["BpDecision"] = _clip(f"{LABELS.get(bp['decision'], bp['decision'])}" + (f": {bp['comment']}" if bp.get("comment") else ""))
    return fields


def stage_rows(pool: Pool, config: ReviewConfig, day: date, stage: str) -> list[dict[str, Any]]:
    """The signals a stage reviews: every argued signal for the BP; for the Manager, only those the BP approved."""
    batch = batch_id(day, config.pack)
    found = records(pool, batch)
    if stage == BP:
        return list(found.values())
    approved = {signal_id for signal_id, row in decisions(pool, batch, BP).items() if row["decision"] == "APPROVE" and row["final"]}
    return [row for signal_id, row in found.items() if signal_id in approved]


def publish(pool: Pool, config: ReviewConfig, day: date, stage: str, profile: Profile, *, client: Any | None) -> dict[str, Any]:
    """Write the stage's rows to its list. Safe to repeat: a row that SharePoint accepted is never sent again. With no list
    or no Graph client, the rows are recorded locally so the mail pack still lists them, and nothing is sent."""
    batch = batch_id(day, config.pack)
    target: Stage = config.stages[stage]
    present = {row["signal_id"]: row for row in items(pool, batch, stage)}
    rows = stage_rows(pool, config, day, stage)
    bp = decisions(pool, batch, BP) if stage == MANAGER else {}
    if not present:
        for position, (record, note) in enumerate(ordered_for(pool, profile, rows), start=1):
            pool.insert("review_items", {"batch_id": batch, "signal_id": record["signal_id"], "stage": stage, "position": position,
                                         "list_item_id": None, "memory_note": note, "published_at": None})
        present = {row["signal_id"]: row for row in items(pool, batch, stage)}
    if client is None or not target.list_id:
        return {"status": "NOT_CONFIGURED", "rows": len(present), "sent": 0,
                "reason": "no SharePoint list id for this stage" if not target.list_id else "Graph is not configured"}
    by_id = {row["signal_id"]: row for row in rows}
    sent = failed = 0
    error = None
    for item in items(pool, batch, stage):
        if item["list_item_id"]:
            continue
        record = by_id.get(item["signal_id"])
        if record is None:
            continue
        fields = row_fields(record, day=day, position=item["position"], memory_note=item.get("memory_note") or "", bp=bp.get(item["signal_id"]))
        try:
            list_item_id = _find(client, target.list_id, item["signal_id"], day) if item.get("uncertain") else None
            list_item_id = list_item_id or client.create_item(target.list_id, fields)
        except GraphError as exc:
            list_item_id = _find(client, target.list_id, item["signal_id"], day) if exc.uncertain else None
            if not list_item_id:
                with pool.transaction() as conn:
                    conn.execute("UPDATE review_items SET uncertain = ? WHERE batch_id = ? AND signal_id = ? AND stage = ?",
                                 (int(exc.uncertain or bool(item.get("uncertain"))), batch, item["signal_id"], stage))
                failed += 1
                error = str(exc)
                continue
        with pool.transaction() as conn:
            conn.execute("UPDATE review_items SET list_item_id = ?, published_at = ?, uncertain = 0 WHERE batch_id = ? AND signal_id = ? AND stage = ?",
                         (list_item_id, utc_now(), batch, item["signal_id"], stage))
        sent += 1
    outcome: dict[str, Any] = {"status": "FAILED" if failed else "DONE", "rows": len(present), "sent": sent, "failed": failed}
    if error:
        outcome["error"] = error
    return outcome


def _find(client: Any, list_id: str, signal_id: str, day: date) -> str | None:
    """The row a create may already have made. A failed lookup counts as not found, and the create is tried."""
    try:
        return client.find_item(list_id, signal_id, day.isoformat())
    except GraphError:
        return None


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _actor(item: Mapping[str, Any]) -> str:
    user = ((item.get("lastModifiedBy") or {}).get("user") or {})
    return str(user.get("email") or user.get("displayName") or "")


def record_decision(pool: Pool, *, batch: str, signal_id: str, stage: str, profile_id: str, decision: str, comment: str | None,
                    actor: str, decided_at: datetime, deadline: datetime) -> bool:
    """Record a decision made while the stage is open. A late one, or a change to a frozen stage, is ignored."""
    current = pool.one("SELECT final FROM review_decisions WHERE batch_id = ? AND signal_id = ? AND stage = ?", (batch, signal_id, stage))
    if (current and current["final"]) or decided_at > deadline:
        return False
    pool.upsert("review_decisions", {
        "batch_id": batch, "signal_id": signal_id, "stage": stage, "profile_id": profile_id, "decision": decision,
        "comment": (comment or "").strip()[:TEXT_MAX] or None, "actor": actor[:200] or None,
        "decided_at": decided_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "on_time": 1, "final": 0, "recorded_at": utc_now(),
    }, ["batch_id", "signal_id", "stage"])
    return True


def as_at(versions: Sequence[Mapping[str, Any]], moment: datetime) -> dict[str, Any] | None:
    """The version of a row that was current at a moment: the newest one last changed at or before it."""
    dated = [(_parse(version.get("lastModifiedDateTime")), version) for version in versions]
    earlier = [(when, version) for when, version in dated if when is not None and when <= moment]
    return dict(max(earlier, key=lambda pair: pair[0])[1]) if earlier else None


def collect(pool: Pool, config: ReviewConfig, day: date, stage: str, *, client: Any | None) -> dict[str, Any]:
    """Read every published row of an open stage. A row set back to Pending withdraws its decision."""
    batch = batch_id(day, config.pack)
    target = config.stages[stage]
    if client is None or not target.list_id:
        return {"status": "NOT_CONFIGURED", "read": 0}
    deadline = config.at(day, target.close_at)
    read = recorded = failed = 0
    for item in items(pool, batch, stage):
        if not item["list_item_id"]:
            continue
        try:
            found = client.get_item(target.list_id, item["list_item_id"])
        except GraphError:
            failed += 1
            continue
        modified = _parse(found.get("lastModifiedDateTime"))
        if modified is not None and modified > deadline:
            # The row changed after the deadline. Read it as it stood at the deadline.
            try:
                found = as_at(client.item_versions(target.list_id, item["list_item_id"]), deadline)
            except GraphError:
                failed += 1
                continue
            if found is None:
                failed += 1
                continue
        read += 1
        fields = found.get("fields") or {}
        decision = DECISIONS.get(str(fields.get("Decision") or ""))
        when = _parse(fields.get("DecidedAt")) or _parse(found.get("lastModifiedDateTime"))
        if decision is None:
            with pool.transaction() as conn:
                conn.execute("DELETE FROM review_decisions WHERE batch_id = ? AND signal_id = ? AND stage = ? AND final = 0 "
                             "AND COALESCE(actor, '') NOT LIKE 'cli:%'",
                             (batch, item["signal_id"], stage))
            continue
        if when is not None and record_decision(pool, batch=batch, signal_id=item["signal_id"], stage=stage, profile_id=target.profile_id,
                                                decision=decision, comment=fields.get("Comment"), actor=_actor(found), decided_at=when, deadline=deadline):
            recorded += 1
    return {"status": "FAILED" if failed else "DONE", "read": read, "recorded": recorded, "failed": failed}


def close(pool: Pool, config: ReviewConfig, day: date, stage: str, *, client: Any | None) -> dict[str, Any]:
    """Freeze the stage at its deadline. Reads the rows one last time, records NO_DECISION for the rest, writes feedback,
    and locks the rows. Locking is best effort: GRIT ignores a later change whether or not the lock holds."""
    batch = batch_id(day, config.pack)
    target = config.stages[stage]
    read = collect(pool, config, day, stage, client=client)
    if read.get("status") == "FAILED":
        # Freezing now would record an unread on-time decision as NO_DECISION. The next tick tries again.
        return {"status": "FAILED", "error": f"could not read {read.get('failed')} row(s)", "read": read.get("read", 0)}
    present = decisions(pool, batch, stage)
    deadline = config.at(day, target.close_at).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {"APPROVE": 0, "REJECT": 0, "NO_DECISION": 0}
    for item in items(pool, batch, stage):
        row = present.get(item["signal_id"])
        if row is None:
            pool.upsert("review_decisions", {
                "batch_id": batch, "signal_id": item["signal_id"], "stage": stage, "profile_id": target.profile_id, "decision": "NO_DECISION",
                "comment": None, "actor": None, "decided_at": deadline, "on_time": 0, "final": 1, "recorded_at": utc_now(),
            }, ["batch_id", "signal_id", "stage"])
            counts["NO_DECISION"] += 1
            continue
        if row["final"]:
            counts[row["decision"]] = counts.get(row["decision"], 0) + 1
            continue
        with pool.transaction() as conn:
            conn.execute("UPDATE review_decisions SET final = 1 WHERE batch_id = ? AND signal_id = ? AND stage = ?", (batch, item["signal_id"], stage))
        pool.add_feedback(target.profile_id, item["signal_id"], row["decision"],
                          f"Daily review {batch}, {stage} stage" + (f": {row['comment']}" if row.get("comment") else ""))
        counts[row["decision"]] += 1
    locked = lock_failed = 0
    if client is not None and target.list_id:
        for item in items(pool, batch, stage):
            if not item["list_item_id"]:
                continue
            try:
                client.update_fields(target.list_id, item["list_item_id"], {"Locked": True})
                locked += 1
            except GraphError:
                lock_failed += 1
    return {"status": "DONE", "read": read.get("read", 0), **{key.lower(): value for key, value in counts.items()}, "locked": locked, "lock_failed": lock_failed}


def decide_locally(pool: Pool, config: ReviewConfig, day: date, stage: str, signal_id: str, decision: str, *, comment: str | None,
                   now: datetime | None = None, actor: str) -> bool:
    """Record a decision without SharePoint, for a pilot before the lists exist or a reviewer who cannot reach them."""
    if decision not in {"APPROVE", "REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")
    batch = batch_id(day, config.pack)
    if signal_id not in {row["signal_id"] for row in items(pool, batch, stage)}:
        raise ValueError(f"{signal_id} is not in the {stage} stage of {batch}")
    target = config.stages[stage]
    return record_decision(pool, batch=batch, signal_id=signal_id, stage=stage, profile_id=target.profile_id, decision=decision,
                           comment=comment, actor=actor, decided_at=now or datetime.now(timezone.utc), deadline=config.at(day, target.close_at))

