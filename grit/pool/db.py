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
    "assessments": {"source_band": "TEXT", "outlets_json": "TEXT NOT NULL DEFAULT '[]'"},
    "red_decisions": {"trigger_id": "TEXT"},
}
_JSON_SUFFIX = "_json"


def reviewed_links(row: dict[str, Any]) -> dict[str, Any]:
    """Apply the reviewed risk_ids. A link the agents removed leaves condition_ids, together with the cascade entries
    and strong theme that trace only to it. Every other entry stands, so a pack whose conditions are not themes keeps
    its cascade. A link the agents added from the register joins condition_ids. Without risk_ids the deterministic links
    stand. The keyword links stay in keyword_condition_ids."""
    kept = (row.get("editorial") or {}).get("risk_ids")
    if not isinstance(kept, list):
        return row
    keep = {str(item) for item in kept}
    links = [str(item) for item in row.get("condition_ids") or []]
    removed = {item for item in links if item not in keep}
    added = [str(item) for item in kept if str(item) not in links]
    if not removed:
        return {**row, "keyword_condition_ids": links, "condition_ids": [*links, *added]}

    def traces(hit: Mapping[str, Any], ids: set[str]) -> bool:
        risk_id, matched = str(hit.get("risk_id") or ""), str(hit.get("matched") or "")
        return any(item == risk_id or (risk_id and item.endswith("-" + risk_id)) or matched.startswith(item + " ") for item in ids)

    themes = [{**item, "strong": False, "reviewed_out": True}
              if isinstance(item, Mapping) and item.get("strong") and str(item.get("theme_id")) in removed else item
              for item in row.get("themes") or []]
    return {**row, "keyword_condition_ids": links, "condition_ids": [*(item for item in links if item in keep), *added],
            "risk_cascade": [hit for hit in row.get("risk_cascade") or [] if traces(hit, keep) or not traces(hit, removed)],
            "themes": themes}


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
        """Insert a page or refresh its last-seen time. The first text seen is kept, so a signal's facts do not drift.
        Different text with a later stated publication day is kept as a revision; a snippet that only varies by query is not."""
        signal_id = signal_id_for(url)
        now = seen_at or utc_now()
        existing = self.one("SELECT signal_id, text, published_at FROM signals WHERE signal_id = ?", (signal_id,))
        with self.transaction() as conn:
            if existing:
                conn.execute("UPDATE signals SET last_seen_at = ? WHERE signal_id = ?", (now, signal_id))
                latest = conn.execute("SELECT MAX(published_at) FROM signal_revisions WHERE signal_id = ?", (signal_id,)).fetchone()[0]
                newer = str(published_at or "")[:10] > str(latest or existing["published_at"] or "")[:10]
                if newer and existing["published_at"] and (text or "").strip() and text != existing["text"]:
                    conn.execute("INSERT OR IGNORE INTO signal_revisions (signal_id, content_hash, title, text, published_at, seen_at) VALUES (?,?,?,?,?,?)",
                                 (signal_id, content_hash, title, text, published_at, now))
            else:
                conn.execute(
                    "INSERT INTO signals (signal_id, url, title, publisher, published_at, lane, text, content_hash, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (signal_id, url, title, publisher, published_at, lane, text or "", content_hash, now, now),
                )
        return signal_id

    def latest_revision(self, signal_id: str, *, by: str | None = None) -> dict[str, Any] | None:
        """The newest revision of a page seen on or before the date `by` (YYYY-MM-DD), or ever when `by` is None."""
        return self.one("SELECT * FROM signal_revisions WHERE signal_id = ? AND (? IS NULL OR substr(seen_at, 1, 10) <= ?) "
                        "ORDER BY seen_at DESC, content_hash LIMIT 1", (signal_id, by, by))

    def add_sighting(self, signal_id: str, run_id: str, *, query_id: str, query: str, lane: str | None, target_id: str | None, rank: int | None) -> None:
        self.insert(
            "sightings",
            {"signal_id": signal_id, "run_id": run_id, "query_id": query_id, "query": query, "lane": lane, "target_id": target_id, "rank": rank},
            replace=True,
        )

    def record_search(self, run_id: str, *, query_id: str, engine: str, query: str, lane: str | None, target_id: str | None,
                      canonical_lob: str | None, results: int, fetched_at: str) -> None:
        """An answered planned search, including one with no results. A failed search is not recorded."""
        self.insert("searches", {"run_id": run_id, "query_id": query_id, "engine": engine, "query": query, "lane": lane, "target_id": target_id,
                                 "canonical_lob": canonical_lob, "results": int(results), "fetched_at": fetched_at}, replace=True)

    def answered_searches(self, run_id: str) -> list[dict[str, Any]]:
        """One row per answered planned query: the search ledger, plus sightings from runs recorded before the ledger.
        A history lookup, an approved internal record or a covered pack's signal is not a search, so it never counts."""
        rows = {row["query_id"]: row for row in self.rows(
            "SELECT query_id, query, lane, target_id, canonical_lob, SUM(results) AS results FROM searches WHERE run_id = ? GROUP BY query_id", (run_id,))}
        for row in self.rows("SELECT query_id, MIN(query) AS query, MIN(lane) AS lane, MIN(target_id) AS target_id, COUNT(*) AS results "
                             "FROM sightings WHERE run_id = ? AND COALESCE(lane, '') NOT IN ('HISTORICAL_EVIDENCE', 'HUMAN_APPROVED', 'COVERED_BY_CHILD') "
                             "GROUP BY query_id", (run_id,)):
            rows.setdefault(row["query_id"], {**row, "canonical_lob": None})
        return list(rows.values())

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
            reviewed_links({**row, "baseline_decision": row["decision"], "editorial": by_id[row["signal_id"]]})
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

    def record_red_decision(self, *, pack: str, signal_id: str, decision: str, impact: str, decided_by: str,
                            trigger_id: str | None = None) -> dict[str, Any]:
        """trigger_id: the RED trigger that happened (URS methodology, section 14). The CLI requires it to confirm."""
        if decision not in {"CONFIRMED", "NOT_CONFIRMED"}:
            raise ValueError("A RED decision is CONFIRMED or NOT_CONFIRMED.")
        if not impact.strip() or not decided_by.strip():
            raise ValueError("A RED decision needs the impact and the name of the person deciding.")
        row = {"pack": pack, "signal_id": signal_id, "decision": decision, "impact": impact.strip(), "decided_by": decided_by.strip(),
               "decided_at": utc_now(), "trigger_id": trigger_id}
        row["decision_id"] = self.insert("red_decisions", row)
        return row

    def red_decisions(self, pack: str) -> dict[str, dict[str, Any]]:
        """The standing R&A decision on each RED candidate of a pack, keyed by signal id."""
        found: dict[str, dict[str, Any]] = {}
        for row in self.rows("SELECT * FROM red_decisions WHERE pack = ? ORDER BY decision_id", (pack,)):
            found[row["signal_id"]] = row
        return found

    def record_correction(self, *, pack: str, earlier: str, why: str, corrected: str, source: str, recorded_by: str,
                          update_old: str | None = None, phrase: str | None = None) -> dict[str, Any]:
        """Add a correction to a pack's corrections ledger (URS methodology, section 22)."""
        values = {"earlier": earlier, "why": why, "corrected": corrected, "source": source, "recorded_by": recorded_by}
        missing = [key for key, value in values.items() if not (value or "").strip()]
        if missing:
            raise ValueError("A correction needs " + ", ".join(missing) + ".")
        count = (self.one("SELECT COUNT(*) AS n FROM corrections WHERE pack = ? AND action = 'RECORDED'", (pack,)) or {}).get("n") or 0
        row = {"pack": pack, "correction_id": f"COR-{pack}-{count + 1:03d}", "action": "RECORDED",
               **{key: value.strip() for key, value in values.items()},
               "update_old": (update_old or "").strip() or None, "phrase": (phrase or "").strip() or None, "recorded_at": utc_now()}
        row["entry_id"] = self.insert("corrections", row)
        return row

    def close_correction(self, *, pack: str, correction_id: str, closed_by: str, reason: str) -> dict[str, Any]:
        """Stop carrying a correction forward. The correction itself stays in the ledger."""
        if not closed_by.strip() or not reason.strip():
            raise ValueError("Closing a correction needs your name and the reason.")
        if not any(row["correction_id"] == correction_id for row in self.corrections(pack)):
            raise ValueError(f"{correction_id} is not an open correction of {pack}.")
        row = {"pack": pack, "correction_id": correction_id, "action": "CLOSED", "recorded_by": closed_by.strip(),
               "recorded_at": utc_now(), "note": reason.strip()}
        row["entry_id"] = self.insert("corrections", row)
        return row

    def corrections(self, pack: str, *, include_closed: bool = False) -> list[dict[str, Any]]:
        """A pack's corrections, oldest first. Each closed one carries closed_by, closed_at and close_reason."""
        found: dict[str, dict[str, Any]] = {}
        for row in self.rows("SELECT * FROM corrections WHERE pack = ? ORDER BY entry_id", (pack,)):
            if row["action"] == "RECORDED":
                found[row["correction_id"]] = {**row, "closed_by": None, "closed_at": None, "close_reason": None}
            elif row["correction_id"] in found:
                found[row["correction_id"]].update(closed_by=row["recorded_by"], closed_at=row["recorded_at"], close_reason=row["note"])
        return [row for row in found.values() if include_closed or not row["closed_at"]]

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
