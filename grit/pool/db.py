"""The signal pool: a SQLite store shaped like the Dataverse model.

Every stage reads and writes through this class. It holds no rule. A judgement arrives already computed by a
deterministic function or proposed by an agent, and the pool records it with its inputs.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from grit.settings import db_path

SCHEMA = Path(__file__).with_name("schema.sql")
# Columns added after a table was first created. CREATE TABLE IF NOT EXISTS does not add them to an existing pool.
MIGRATIONS = {
    "weekly_reports": {"selection_hash": "TEXT NOT NULL DEFAULT ''"},
    "watchpoint_updates": {"cycles_before": "INTEGER NOT NULL DEFAULT 0"},
    "management_requests": {"pack": "TEXT", "queries_json": "TEXT NOT NULL DEFAULT '[]'", "source": "TEXT"},
    "condition_assessments": {"implication": "TEXT", "options_json": "TEXT NOT NULL DEFAULT '[]'", "opportunity": "TEXT", "counter_indicator": "TEXT"},
}
_JSON_SUFFIX = "_json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def signal_id_for(url: str) -> str:
    """A signal id from its canonical URL. The same page always has the same id."""
    return "SIG-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _row(row: sqlite3.Row) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key.endswith(_JSON_SUFFIX) and isinstance(value, str):
            record[key[: -len(_JSON_SUFFIX)]] = json.loads(value)
        else:
            record[key] = value
    return record


def _encode(row: Mapping[str, Any]) -> dict[str, Any]:
    """A value for a *_json column may be a Python object. It is stored as sorted JSON."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key.endswith(_JSON_SUFFIX):
            out[key] = value if isinstance(value, str) else _dump(value)
        else:
            out[key] = value
    return out


class Pool:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        for table, columns in MIGRATIONS.items():
            present = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in present:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # Generic access -------------------------------------------------------------------------------------------

    def rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [_row(row) for row in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        return _row(row) if row is not None else None

    def insert(self, table: str, row: Mapping[str, Any], *, replace: bool = False) -> int:
        encoded = _encode(row)
        columns = ", ".join(encoded)
        marks = ", ".join("?" for _ in encoded)
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        with self.transaction() as conn:
            cursor = conn.execute(f"{verb} INTO {table} ({columns}) VALUES ({marks})", tuple(encoded.values()))
        return int(cursor.lastrowid or 0)

    def upsert(self, table: str, row: Mapping[str, Any], keys: Sequence[str]) -> None:
        encoded = _encode(row)
        columns = ", ".join(encoded)
        marks = ", ".join("?" for _ in encoded)
        updates = ", ".join(f"{column} = excluded.{column}" for column in encoded if column not in keys) or f"{keys[0]} = excluded.{keys[0]}"
        with self.transaction() as conn:
            conn.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({marks}) ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates}",
                tuple(encoded.values()),
            )

    # Runs -----------------------------------------------------------------------------------------------------

    def start_run(self, run_id: str, *, run_date: str, pack: str, kind: str, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.upsert(
            "runs",
            {"run_id": run_id, "run_date": run_date, "pack": pack, "kind": kind, "status": "STARTED", "started_at": utc_now(), "config_json": dict(config or {})},
            ["run_id"],
        )
        return self.run(run_id) or {}

    def finish_run(self, run_id: str, status: str = "COMPLETE", notes: str | None = None) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE runs SET status = ?, finished_at = ?, notes = COALESCE(?, notes) WHERE run_id = ?", (status, utc_now(), notes, run_id))

    def update_run_config(self, run_id: str, **values: Any) -> None:
        """Merge values into a run's config record, such as the validated bottom line."""
        current = (self.run(run_id) or {}).get("config") or {}
        with self.transaction() as conn:
            conn.execute("UPDATE runs SET config_json = ? WHERE run_id = ?", (_dump({**current, **values}), run_id))

    def run(self, run_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM runs WHERE run_id = ?", (run_id,))

    def latest_run(self, pack: str | None = None, *, before: str | None = None, kind: str | None = None) -> dict[str, Any] | None:
        clauses, params = ["status = 'COMPLETE'"], []
        if pack:
            clauses.append("pack = ?")
            params.append(pack)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if before:
            clauses.append("run_date < ?")
            params.append(before)
        return self.one(f"SELECT * FROM runs WHERE {' AND '.join(clauses)} ORDER BY run_date DESC, started_at DESC LIMIT 1", params)

    # Signals --------------------------------------------------------------------------------------------------

    def add_signal(self, *, url: str, title: str, text: str, publisher: str | None, published_at: str | None, lane: str, content_hash: str, seen_at: str | None = None) -> str:
        """Insert a page or refresh its last-seen time. The first text seen is kept, so a signal's facts do not drift."""
        signal_id = signal_id_for(url)
        now = seen_at or utc_now()
        existing = self.one("SELECT signal_id FROM signals WHERE signal_id = ?", (signal_id,))
        with self.transaction() as conn:
            if existing:
                conn.execute("UPDATE signals SET last_seen_at = ? WHERE signal_id = ?", (now, signal_id))
            else:
                conn.execute(
                    "INSERT INTO signals (signal_id, url, title, publisher, published_at, lane, text, content_hash, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (signal_id, url, title, publisher, published_at, lane, text or "", content_hash, now, now),
                )
        return signal_id

    def add_sighting(self, signal_id: str, run_id: str, *, query_id: str, query: str, lane: str | None, target_id: str | None, rank: int | None) -> None:
        self.insert(
            "sightings",
            {"signal_id": signal_id, "run_id": run_id, "query_id": query_id, "query": query, "lane": lane, "target_id": target_id, "rank": rank},
            replace=True,
        )

    def signal(self, signal_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM signals WHERE signal_id = ?", (signal_id,))

    def signals_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return self.rows(
            "SELECT DISTINCT s.* FROM signals s JOIN sightings g ON g.signal_id = s.signal_id WHERE g.run_id = ? ORDER BY s.published_at DESC, s.signal_id",
            (run_id,),
        )

    def sightings(self, run_id: str, signal_id: str | None = None) -> list[dict[str, Any]]:
        if signal_id:
            return self.rows("SELECT * FROM sightings WHERE run_id = ? AND signal_id = ? ORDER BY rank", (run_id, signal_id))
        return self.rows("SELECT * FROM sightings WHERE run_id = ? ORDER BY query_id, rank", (run_id,))

    def set_doc_type(self, signal_id: str, doc_type: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE signals SET doc_type = ? WHERE signal_id = ?", (doc_type, signal_id))

    # Vectors --------------------------------------------------------------------------------------------------

    def put_vector(self, signal_id: str, *, model: str, task: str, dimensions: int, text_hash: str, vector: Sequence[float]) -> None:
        self.insert(
            "vectors",
            {"signal_id": signal_id, "model": model, "task": task, "dimensions": dimensions, "text_hash": text_hash, "vector_json": list(vector)},
            replace=True,
        )

    def vectors(self, signal_ids: Iterable[str], *, model: str, task: str, dimensions: int) -> dict[str, list[float]]:
        ids = list(signal_ids)
        found: dict[str, list[float]] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            for row in self.rows(
                f"SELECT signal_id, vector_json FROM vectors WHERE model = ? AND task = ? AND dimensions = ? AND signal_id IN ({marks})",
                (model, task, dimensions, *chunk),
            ):
                found[row["signal_id"]] = row["vector"]
        return found

    # Assessments ----------------------------------------------------------------------------------------------

    def save_assessment(self, row: Mapping[str, Any]) -> None:
        self.upsert("assessments", {"assessed_at": utc_now(), **row}, ["signal_id", "run_id", "pack"])

    def assessments(self, run_id: str, *, kept_only: bool = False) -> list[dict[str, Any]]:
        where = "AND a.decision = 'KEEP'" if kept_only else ""
        return self.rows(
            f"""SELECT a.*, s.title, s.url, s.publisher, s.published_at, s.text, s.lane, s.doc_type
                FROM assessments a JOIN signals s ON s.signal_id = a.signal_id
                WHERE a.run_id = ? {where}
                ORDER BY COALESCE(a.tier, 9), a.red DESC, s.published_at DESC, a.signal_id""",
            (run_id,),
        )

    def weekly_assessments(self, run_id: str, *, include_excluded: bool = False) -> list[dict[str, Any]]:
        """Effective editorial selection; the deterministic rows are never overwritten."""
        run = self.run(run_id) or {}
        if (run.get("config") or {}).get("weekly_mode") != "reasoned":
            return self.assessments(run_id, kept_only=True)
        state = self.one("SELECT * FROM weekly_runs WHERE run_id = ?", (run_id,))
        if state is None:
            raise ValueError(f"{run_id}: weekly editorial selection has not run.")
        selected = self.rows(
            """SELECT w.* FROM weekly_selections w
               WHERE w.run_id = ? AND w.version = ? AND w.selection_id = (
                 SELECT MAX(s.selection_id) FROM weekly_selections s
                 WHERE s.run_id = w.run_id AND s.version = w.version AND s.signal_id = w.signal_id)""",
            (run_id, state["version"]),
        )
        by_id = {row["signal_id"]: row["selection"] for row in selected}
        baseline = self.assessments(run_id)
        if {row["signal_id"] for row in self.signals_for_run(run_id)} - {row["signal_id"] for row in baseline}:
            raise ValueError(f"{run_id}: candidates are missing deterministic assessments.")
        if set(by_id) != {row["signal_id"] for row in baseline}:
            raise ValueError(f"{run_id}: incomplete weekly editorial coverage.")
        return [
            {**row, "baseline_decision": row["decision"], "editorial": by_id[row["signal_id"]]}
            for row in baseline
            if include_excluded or by_id[row["signal_id"]]["decision"] in {"INCLUDE", "WATCH"}
        ]

    # Layer 1 and Layer 5 --------------------------------------------------------------------------------------

    def save_condition(self, row: Mapping[str, Any]) -> None:
        self.upsert("condition_assessments", row, ["run_id", "condition_id"])

    def conditions(self, run_id: str) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM condition_assessments WHERE run_id = ? ORDER BY condition_id", (run_id,))

    def save_exposure_check(self, row: Mapping[str, Any]) -> None:
        self.upsert("exposure_checks", row, ["run_id", "item_type", "item_id"])

    def exposure_checks(self, run_id: str) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM exposure_checks WHERE run_id = ? ORDER BY item_type, item_id", (run_id,))

    def add_qa(self, run_id: str, *, item: str, correction: str, prior: str | None = None, source_ids: Sequence[str] = (), origin: str = "CRITIC") -> None:
        self.insert("qa_log", {"run_id": run_id, "item": item, "prior": prior, "correction": correction, "source_ids_json": list(source_ids), "origin": origin})

    def qa_log(self, run_id: str) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM qa_log WHERE run_id = ? ORDER BY qa_id", (run_id,))

    # Watchpoints ----------------------------------------------------------------------------------------------

    def watchpoints(self, pack: str, *, open_only: bool = True) -> list[dict[str, Any]]:
        where = "AND status != 'CLOSED'" if open_only else ""
        return self.rows(f"SELECT * FROM watchpoints WHERE pack = ? {where} ORDER BY opened_on, watchpoint_id", (pack,))

    def watchpoint_updates(self, run_id: str) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM watchpoint_updates WHERE run_id = ?", (run_id,))

    # Findings -------------------------------------------------------------------------------------------------

    def save_finding(self, run_id: str, card: Mapping[str, Any], signal_id: str) -> None:
        self.upsert(
            "findings",
            {
                "finding_id": card["finding_id"], "run_id": run_id, "signal_id": signal_id,
                "confidence": ((card.get("q6") or {}).get("level")), "status": card.get("status") or "DRAFT_FOR_REVIEW", "card_json": dict(card),
            },
            ["run_id", "finding_id"],
        )

    def findings(self, run_id: str) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM findings WHERE run_id = ? ORDER BY finding_id", (run_id,))

    # Feedback and requests ------------------------------------------------------------------------------------

    def add_feedback(self, profile_id: str, signal_id: str, decision: str, rationale: str | None = None) -> int:
        if decision not in {"APPROVE", "REJECT", "WATCH"}:
            raise ValueError(f"Unknown feedback decision: {decision}")
        return self.insert("feedback", {"profile_id": profile_id, "signal_id": signal_id, "decision": decision, "rationale": rationale, "submitted_at": utc_now()})

    def feedback(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        if profile_id:
            return self.rows("SELECT * FROM feedback WHERE profile_id = ? ORDER BY feedback_id", (profile_id,))
        return self.rows("SELECT * FROM feedback ORDER BY feedback_id")

    def latest_decisions(self, profile_id: str | None = None) -> dict[tuple[str, str], str]:
        """The latest decision per reader and signal. A later decision replaces an earlier one."""
        latest: dict[tuple[str, str], str] = {}
        for row in self.feedback(profile_id):
            latest[(row["profile_id"], row["signal_id"])] = row["decision"]
        return latest

    def latest_assessments(self, signal_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """The most recent assessment of each signal, across runs. Memory needs the features of signals from earlier weeks."""
        found: dict[str, dict[str, Any]] = {}
        ids = list(dict.fromkeys(signal_ids))
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            for row in self.rows(
                f"""SELECT a.*, s.title, s.url, s.publisher, s.published_at FROM assessments a JOIN signals s ON s.signal_id = a.signal_id
                    WHERE a.signal_id IN ({marks}) ORDER BY a.assessed_at""",
                tuple(chunk),
            ):
                found[row["signal_id"]] = row
        return found

    def management_requests(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE status = 'ACTIVE'" if active_only else ""
        return self.rows(f"SELECT * FROM management_requests {where} ORDER BY request_date, request_id")

    # Workflow ledger ------------------------------------------------------------------------------------------

    def append_workflow_row(self, row: Any) -> None:
        """The Supervisor's ledger sink. The table refuses UPDATE and DELETE, so a row once written stays."""
        record = asdict(row) if is_dataclass(row) else dict(row)
        self.insert(
            "workflow_states",
            {
                "signal_id": record["signal_id"], "execution_id": record["execution_id"], "sequence": record["sequence"], "state": record["state"],
                "previous_state": record.get("previous_state"), "transition_type": record["transition_type"], "actor_id": record["actor_id"],
                "recorded_at": record["accepted_at"], "row_json": record,
            },
        )

    def workflow_rows(self, signal_id: str | None = None) -> list[dict[str, Any]]:
        if signal_id:
            return [row["row"] for row in self.rows("SELECT row_json FROM workflow_states WHERE signal_id = ? ORDER BY row_id", (signal_id,))]
        return [row["row"] for row in self.rows("SELECT row_json FROM workflow_states ORDER BY row_id")]

    def current_states(self) -> dict[tuple[str, str], str]:
        """The latest workflow state of each signal in each execution."""
        states: dict[tuple[str, str], str] = {}
        for row in self.rows("SELECT signal_id, execution_id, state FROM workflow_states ORDER BY row_id"):
            states[(row["signal_id"], row["execution_id"])] = row["state"]
        return states

    # Logs -----------------------------------------------------------------------------------------------------

    def log(self, *, actor_type: str, actor: str, status: str, run_id: str | None = None, version: str | None = None, model: str | None = None,
            subject: str | None = None, input_hash: str | None = None, output_hash: str | None = None, detail: Mapping[str, Any] | None = None) -> int:
        return self.insert(
            "execution_log",
            {
                "run_id": run_id, "actor_type": actor_type, "actor": actor, "version": version, "model": model, "subject": subject,
                "input_hash": input_hash, "output_hash": output_hash, "status": status, "detail_json": dict(detail or {}), "logged_at": utc_now(),
            },
        )

    def execution_log(self, run_id: str) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM execution_log WHERE run_id = ? ORDER BY log_id", (run_id,))

    def counts(self) -> dict[str, int]:
        tables = [row["name"] for row in self.rows("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {table: int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}
