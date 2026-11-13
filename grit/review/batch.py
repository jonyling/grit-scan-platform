"""Which signals a working day's review holds, and the pool records behind them. No model, no network."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

from grit.pool import Pool, utc_now
from grit.review.config import BP, ReviewConfig, batch_id


def daily_run_ids(config: ReviewConfig, day: date) -> list[str]:
    return [f"{covered.isoformat()}-{config.pack}-daily" for covered in config.covered_days(day)]


def _rank(row: Mapping[str, Any]) -> tuple:
    """Tier first, RED first within a tier, newest first, then the id so the order never moves between calls."""
    return (row.get("tier") or 9, -int(bool(row.get("red"))), -_stamp(row), row["signal_id"])


def _stamp(row: Mapping[str, Any]) -> float:
    text = str(row.get("published_at") or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def protected(row: Mapping[str, Any]) -> bool:
    """A P1 or RED signal is always published, whatever the cap, and memory never moves it down."""
    return row.get("tier") == 1 or bool(row.get("red"))


def candidates(pool: Pool, config: ReviewConfig, day: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(published, overflow) for the day. Kept signals from the covered daily runs, one row per signal (the latest run's
    assessment), less any signal an earlier batch of this pack already published. The cap never drops a P1 or RED signal."""
    this_batch = batch_id(day, config.pack)
    seen = {row["signal_id"] for row in pool.rows(
        "SELECT DISTINCT signal_id FROM review_items WHERE stage = ? AND batch_id LIKE ? AND batch_id <> ?",
        (BP, f"%-{config.pack}-review", this_batch))}
    latest: dict[str, dict[str, Any]] = {}
    for run_id in daily_run_ids(config, day):
        for row in pool.assessments(run_id, kept_only=True):
            if row["signal_id"] not in seen:
                latest[row["signal_id"]] = row
    ordered = sorted(latest.values(), key=_rank)
    must = [row for row in ordered if protected(row)]
    rest = [row for row in ordered if not protected(row)]
    room = max(0, config.max_signals - len(must))
    published = sorted(must + rest[:room], key=_rank)
    return published, rest[room:]


def mark_step(pool: Pool, batch: str, step: str, status: str = "DONE", outcome: Mapping[str, Any] | None = None) -> None:
    pool.upsert("review_steps", {"batch_id": batch, "step": step, "status": status, "outcome_json": dict(outcome or {}), "done_at": utc_now()},
                ["batch_id", "step"])


def step_done(pool: Pool, batch: str, step: str) -> bool:
    row = pool.one("SELECT status FROM review_steps WHERE batch_id = ? AND step = ?", (batch, step))
    return bool(row and row["status"] in {"DONE", "SKIPPED", "UNCERTAIN"})


def steps(pool: Pool, batch: str) -> dict[str, dict[str, Any]]:
    return {row["step"]: row for row in pool.rows("SELECT * FROM review_steps WHERE batch_id = ?", (batch,))}


def arguments(pool: Pool, batch: str) -> dict[str, dict[str, Any]]:
    return {row["signal_id"]: row for row in pool.rows("SELECT * FROM review_arguments WHERE batch_id = ?", (batch,))}


def items(pool: Pool, batch: str, stage: str) -> list[dict[str, Any]]:
    return pool.rows("SELECT * FROM review_items WHERE batch_id = ? AND stage = ? ORDER BY position", (batch, stage))


def decisions(pool: Pool, batch: str, stage: str) -> dict[str, dict[str, Any]]:
    return {row["signal_id"]: row for row in pool.rows("SELECT * FROM review_decisions WHERE batch_id = ? AND stage = ?", (batch, stage))}


def records(pool: Pool, batch: str) -> dict[str, dict[str, Any]]:
    """Each argued signal with the assessment from the daily run the argument read."""
    out: dict[str, dict[str, Any]] = {}
    for argument in pool.rows("SELECT * FROM review_arguments WHERE batch_id = ?", (batch,)):
        row = pool.one(
            """SELECT a.*, s.title, s.url, s.publisher, s.published_at, s.text FROM assessments a JOIN signals s ON s.signal_id = a.signal_id
               WHERE a.signal_id = ? AND a.run_id = ?""",
            (argument["signal_id"], argument["run_id"]),
        )
        if row:
            out[argument["signal_id"]] = {**row, "argument": argument}
    return out
