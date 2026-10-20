"""The nightly index job (spec 42). Each step is deterministic and safe to repeat.

| Step | What it does |
|---|---|
| Full text | Adds new signals to the FTS5 index. |
| Events | Joins each newly kept signal to an event when it tells the same story as one: the same title, or cosine 0.90 or more with a member published within 8 days. Otherwise it starts an event. The lead is the best-labelled member: lowest tier, then earliest. |
| Digests | Per pack and day, from the runs dated that day: newly kept tier 1 and 2 events, RED patterns and drafts, moved standing items, and watchpoint updates. |
| Retention | After 90 days, a page that no run kept keeps its title and the first 500 characters of its text. Kept pages and all vectors stay whole. |
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from grit.copilot.search import SAME_STORY, SAME_STORY_DAYS, ensure_fts
from grit.pool import Pool, utc_now

RETENTION_DAYS = 90
RETAINED_CHARS = 500
DIGEST_EVENTS = 25


def _day(text: Any) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _title_key(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip()


def cluster_events(pool: Pool) -> dict[str, int]:
    """Assign every kept signal that has no event yet."""
    import numpy as np

    from grit.retrieval.embedder import DEFAULT_DIMENSIONS, DEFAULT_MODEL

    todo = pool.rows(
        """SELECT s.signal_id, s.title, COALESCE(s.published_at, s.first_seen_at) AS day, MIN(COALESCE(a.tier, 9)) AS tier
           FROM signals s JOIN assessments a ON a.signal_id = s.signal_id AND a.decision = 'KEEP'
           WHERE s.signal_id NOT IN (SELECT signal_id FROM event_members)
           GROUP BY s.signal_id ORDER BY day, s.signal_id"""
    )
    if not todo:
        return {"joined": 0, "new": 0}
    members = pool.rows(
        """SELECT m.signal_id, m.event_id, s.title, COALESCE(s.published_at, s.first_seen_at) AS day FROM event_members m JOIN signals s ON s.signal_id = m.signal_id"""
    )
    events = {row["event_id"]: row for row in pool.rows("SELECT * FROM events")}
    lead_tier = {row["signal_id"]: row["tier"] for row in pool.rows(
        "SELECT a.signal_id, MIN(COALESCE(a.tier, 9)) AS tier FROM assessments a WHERE a.signal_id IN (SELECT lead_signal_id FROM events) GROUP BY a.signal_id")}
    ids = [row["signal_id"] for row in todo] + [row["signal_id"] for row in members]
    vectors = {key: np.asarray(value, dtype=np.float32) for key, value in pool.vectors(ids, model=DEFAULT_MODEL, task="RETRIEVAL_DOCUMENT", dimensions=DEFAULT_DIMENSIONS).items()}
    by_day: dict[int, list[dict[str, Any]]] = {}
    by_title: dict[str, list[dict[str, Any]]] = {}

    def remember(member: dict[str, Any]) -> None:
        when = _day(member["day"])
        by_day.setdefault(when.toordinal() if when else -1, []).append(member)
        by_title.setdefault(_title_key(member["title"]), []).append(member)

    for member in members:
        remember(dict(member))
    joined = new = 0
    now = utc_now()
    with pool.transaction() as conn:
        for row in todo:
            day = _day(row["day"])
            key = _title_key(row["title"])
            target = None
            for member in reversed(by_title.get(key, []) if key else []):
                other = _day(member["day"])
                if day is None or other is None or abs((day - other).days) <= SAME_STORY_DAYS:
                    target = member["event_id"]
                    break
            if target is None and day is not None and row["signal_id"] in vectors:
                mine = vectors[row["signal_id"]]
                nearby = [member for offset in range(-SAME_STORY_DAYS, SAME_STORY_DAYS + 1) for member in by_day.get(day.toordinal() + offset, [])]
                best = max(((float(mine @ vectors[member["signal_id"]]), member["event_id"]) for member in nearby if member["signal_id"] in vectors), default=(0.0, None))
                if best[0] >= SAME_STORY:
                    target = best[1]
            text_day = day.isoformat() if day else None
            if target is None:
                target = f"EVT-{row['signal_id'][4:]}"
                conn.execute("INSERT INTO events (event_id, lead_signal_id, first_date, last_date, members, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                             (target, row["signal_id"], text_day, text_day, now, now))
                events[target] = {"event_id": target, "lead_signal_id": row["signal_id"], "first_date": text_day, "last_date": text_day, "members": 1}
                lead_tier[row["signal_id"]] = row["tier"]
                new += 1
            else:
                event = events[target]
                first = min(filter(None, [event.get("first_date"), text_day]), default=None)
                last = max(filter(None, [event.get("last_date"), text_day]), default=None)
                lead = event["lead_signal_id"]
                if row["tier"] < lead_tier.get(lead, 9):
                    lead = row["signal_id"]
                    lead_tier[lead] = row["tier"]
                conn.execute("UPDATE events SET lead_signal_id = ?, first_date = ?, last_date = ?, members = members + 1, updated_at = ? WHERE event_id = ?",
                             (lead, first, last, now, target))
                event.update({"lead_signal_id": lead, "first_date": first, "last_date": last, "members": int(event.get("members") or 1) + 1})
                joined += 1
            conn.execute("INSERT INTO event_members (signal_id, event_id, joined_at) VALUES (?, ?, ?)", (row["signal_id"], target, now))
            remember({"signal_id": row["signal_id"], "event_id": target, "title": row["title"], "day": row["day"]})
    return {"joined": joined, "new": new}


def build_digest(pool: Pool, pack: str, day: date) -> dict[str, Any]:
    """What was new for a pack on a day: pages kept by that day's runs (the daily run, and any weekly or monthly run dated
    that day) that no earlier run of the pack kept, and the standing items and watchpoints those runs moved."""
    text_day = day.isoformat()
    runs = pool.rows("SELECT run_id, kind FROM runs WHERE pack = ? AND run_date = ? AND status = 'COMPLETE'", (pack, text_day))
    run_ids = [row["run_id"] for row in runs]
    rows: list[dict[str, Any]] = []
    if run_ids:
        marks = ", ".join("?" for _ in run_ids)
        rows = pool.rows(
            f"""SELECT a.signal_id, MIN(COALESCE(a.tier, 9)) AS tier, a.rung, MAX(a.red) AS red, a.condition_ids_json, s.title, s.publisher, s.published_at, m.event_id
                FROM assessments a JOIN signals s ON s.signal_id = a.signal_id LEFT JOIN event_members m ON m.signal_id = a.signal_id
                WHERE a.run_id IN ({marks}) AND a.decision = 'KEEP'
                AND NOT EXISTS (SELECT 1 FROM assessments b JOIN runs q ON q.run_id = b.run_id
                                WHERE b.signal_id = a.signal_id AND b.pack = ? AND b.decision = 'KEEP' AND q.run_date < ?)
                GROUP BY a.signal_id ORDER BY tier, red DESC, s.published_at DESC""",
            (*run_ids, pack, text_day),
        )
    events: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("event_id") or row["signal_id"]
        held = events.get(key)
        if held is None:
            events[key] = {"signal_id": row["signal_id"], "title": row["title"], "outlet": row["publisher"], "published": (row["published_at"] or "")[:10] or None,
                           "tier": row["tier"], "rung": row["rung"], "red": bool(row["red"]), "standing_items": row.get("condition_ids") or [], "outlets": {row["publisher"]}}
        else:
            held["outlets"].add(row["publisher"])
            held["red"] = held["red"] or bool(row["red"])
    top = [dict(item, outlets=len({outlet for outlet in item["outlets"] if outlet})) for item in events.values() if (item["tier"] or 9) <= 2][:DIGEST_EVENTS]
    moved, watch = [], []
    for run in [row for row in runs if row["kind"] != "DAILY"]:
        moved += [{"run_id": run["run_id"], "condition_id": row["condition_id"], "name": row["condition_name"], "trend": row["trend_label"]}
                  for row in pool.conditions(run["run_id"]) if row.get("movement") in {"ESCALATING", "EASING"} and row["status"] == "VALIDATED"]
        watch += [{"run_id": run["run_id"], "watchpoint_id": row["watchpoint_id"], "note": row["note"]} for row in pool.watchpoint_updates(run["run_id"])]
    red = [dict(row) for run_id in run_ids for row in pool.rows("SELECT alert_id, signal_id, status FROM alerts WHERE run_id = ?", (run_id,))]
    return {
        "pack": pack, "day": text_day, "runs": run_ids, "kept_new": len(rows), "events_new": len(events), "tier_1_2_events": top,
        "red_patterns": sum(1 for item in events.values() if item["red"]), "red_drafts": red, "moved": moved, "watchpoint_updates": watch,
    }


def write_digests(pool: Pool, day: date, packs: list[str] | None = None) -> dict[str, int]:
    from grit.copilot.scope import pack_labels

    out = {}
    for pack in packs or sorted(pack_labels()):
        digest = build_digest(pool, pack, day)
        pool.upsert("digests", {"pack": pack, "day": day.isoformat(), "digest_json": digest, "created_at": utc_now()}, ["pack", "day"])
        out[pack] = len(digest["tier_1_2_events"])
    return out


def retain(pool: Pool, day: date, *, days: int = RETENTION_DAYS) -> int:
    """Cut the stored text of pages that no run kept and that are older than the retention period."""
    cutoff = (day - timedelta(days=days)).isoformat()
    rows = pool.rows(
        """SELECT signal_id, title, text FROM signals s WHERE substr(s.first_seen_at, 1, 10) < ? AND length(s.text) > ?
           AND NOT EXISTS (SELECT 1 FROM assessments a WHERE a.signal_id = s.signal_id AND a.decision = 'KEEP')""",
        (cutoff, RETAINED_CHARS),
    )
    if not rows:
        return 0
    ensure_fts(pool)
    with pool.transaction() as conn:
        for row in rows:
            short = row["text"][:RETAINED_CHARS]
            conn.execute("UPDATE signals SET text = ? WHERE signal_id = ?", (short, row["signal_id"]))
            conn.execute("DELETE FROM signal_fts WHERE signal_id = ?", (row["signal_id"],))
            conn.execute("INSERT INTO signal_fts (signal_id, title, text) VALUES (?, ?, ?)", (row["signal_id"], row["title"], short))
    return len(rows)


DIGEST_DAYS = 3  # a weekly or monthly run that finishes after the night's index still reaches its day's digest the next night


def nightly(pool: Pool, day: date, *, packs: list[str] | None = None) -> dict[str, Any]:
    events = cluster_events(pool)
    for back in range(DIGEST_DAYS - 1, 0, -1):
        write_digests(pool, day - timedelta(days=back), packs)
    result = {"fts_added": ensure_fts(pool), "events": events, "digests": write_digests(pool, day, packs), "retained": retain(pool, day)}
    pool.log(actor_type="FUNCTION", actor="NightlyIndex", status="SUCCEEDED", detail={"day": day.isoformat(), **result})
    return result


def digests(pool: Pool, packs: list[str], since: date, until: date | None = None) -> list[dict[str, Any]]:
    marks = ", ".join("?" for _ in packs)
    params: list[Any] = [*packs, since.isoformat()]
    where = ""
    if until:
        where = " AND day <= ?"
        params.append(until.isoformat())
    return [json.loads(row["digest_json"]) if isinstance(row.get("digest_json"), str) else row["digest"]
            for row in pool.rows(f"SELECT * FROM digests WHERE pack IN ({marks}) AND day >= ?{where} ORDER BY day DESC, pack", params)]
