"""Opt-in weekly editorial reasoning, separate from scoring and human release."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Callable, Mapping, Sequence

from grit.agents.checks import text_problems
from grit.agents.loop.llm import LoopError, call_json
from grit.agents.weekly import (
    PROMPTS, VERSION, ContractError, check_critique, check_selections,
    no_controlled_fields, object_response, source_ids, time_scope,
)
from grit.core.canonical import canonical_hash
from grit.pool import Pool, utc_now
from grit.report.brief import CITE, citations, plain

RoleCall = Callable[..., dict[str, Any]]

# Model input is bounded. Validation still checks quotes and citations against the full stored text.
FULL_TEXT_CHARS = 4000
INDEX_TEXT_CHARS = 240
RELATED_LIMIT = 30
MAX_PAYLOAD_CHARS = 2_000_000
INDEX_FIELDS = ("signal_id", "title", "publisher", "published_at", "time_scope", "decision", "drop_reason",
                "tier", "condition_ids", "red", "assessment_missing")
# Fast review (opt-in): dropped signals get a compact Critic screen; kept and escalated signals get full review.
FULL_BATCH, FULL_BATCH_MAX = 16, 24
SCREEN_BATCH, SCREEN_BATCH_MAX = 80, 120
SCREEN_TEXT_CHARS = 400
DEFAULT_WORKERS = 6


def stable_batches(keys: Sequence[str], target: int, limit: int) -> list[list[str]]:
    """Batch boundaries depend only on signal IDs, so a new signal changes only the batch it lands in."""
    batches: list[list[str]] = []
    current: list[str] = []
    for key in sorted(keys):
        current.append(key)
        if len(current) >= limit or int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % target == 0:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


def full_view(row: Mapping[str, Any], limit: int = FULL_TEXT_CHARS) -> dict[str, Any]:
    text = str(row.get("text") or "")
    value = {key: item for key, item in row.items() if key not in {"evidence_records", "harm", "link"}}
    value["text"] = text if len(text) <= limit else text[:limit] + " [truncated]"
    harm = row.get("harm") or {}
    if harm:
        value["harm"] = {"status": harm.get("status")}
    link = {key: item for key, item in (row.get("link") or {}).items() if isinstance(item, (str, int, float, bool)) and item is not None}
    if link:
        value["link"] = link
    return value


def index_view(row: Mapping[str, Any], chars: int = INDEX_TEXT_CHARS) -> dict[str, Any]:
    value = {key: row.get(key) for key in INDEX_FIELDS if row.get(key) not in (None, "", [], False)}
    value["title"] = str(row.get("title") or "")[:200]
    text = " ".join(str(row.get("text") or "").split())
    value["excerpt"] = text[:chars]
    if row.get("url") and chars > INDEX_TEXT_CHARS:
        value["url"] = row["url"]
    duplicate = (row.get("link") or {}).get("duplicate_of")
    if duplicate:
        value["duplicate_of"] = duplicate
    return value


def evidence_snapshot(pool: Pool, run_id: str) -> list[dict[str, Any]]:
    assessed = {row["signal_id"]: row for row in pool.assessments(run_id)}
    for signal in pool.signals_for_run(run_id):
        if signal["signal_id"] not in assessed:
            assessed[signal["signal_id"]] = {**signal, "assessment_missing": True}
    for evidence in pool.rows("SELECT * FROM weekly_evidence WHERE run_id = ? ORDER BY evidence_id", (run_id,)):
        if evidence["signal_id"] in assessed:
            assessed[evidence["signal_id"]].setdefault("evidence_records", []).append(evidence["provenance"])
    return [{key: value for key, value in row.items() if key not in {"assessed_at", "first_seen_at", "last_seen_at"}}
            for row in sorted(assessed.values(), key=lambda row: row["signal_id"])]


def analysis_hash(pool: Pool, run_id: str) -> str:
    config = (pool.run(run_id) or {}).get("config") or {}
    value = {"evidence": evidence_snapshot(pool, run_id),
             "authority": config.get("weekly_context"), "window_days": config.get("window_days")}
    # Switching to fast review starts a new version; full-review hashes are unchanged.
    if config.get("weekly_review") == "fast":
        value["review"] = "fast"
    return canonical_hash(value)


SELECTION_FIELDS = ("signal_id", "title", "publisher", "published_at", "url", "baseline_decision", "drop_reason", "tier")


def selection_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{**{key: row.get(key) for key in SELECTION_FIELDS}, "editorial": row["editorial"]} for row in rows]


def model_brief(brief: Mapping[str, Any]) -> dict[str, Any]:
    """The report as the model sees it: the full selection audit is replaced by counts and baseline changes."""
    value = copy.deepcopy(dict(brief))
    weekly = (value.get("method") or {}).get("weekly")
    if isinstance(weekly, dict) and isinstance(weekly.get("selections"), list):
        rows = weekly["selections"]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["editorial"]["decision"]] = counts.get(row["editorial"]["decision"], 0) + 1
        changed = [row for row in rows if (row.get("baseline_decision") == "KEEP") != (row["editorial"]["decision"] in {"INCLUDE", "WATCH"})]
        weekly["selections"] = {"counts": counts, "changed_from_baseline": changed}
    return value


class WeeklyStore:
    def __init__(self, pool: Pool, run_id: str, *, search_limit: int, refresh: bool = True) -> None:
        if type(search_limit) is not int or search_limit < 0:
            raise ValueError("An explicit nonnegative countersearch limit is required; use 0 to disable.")
        if pool.run(run_id) is None:
            raise KeyError(run_id)
        self.pool, self.run_id = pool, run_id
        snapshot_hash = analysis_hash(pool, run_id)
        existing = self.state
        if existing and existing["search_limit"] != search_limit:
            raise ValueError("A resumed run must keep its recorded countersearch limit.")
        if not existing:
            pool.insert("weekly_runs", {
                "run_id": run_id, "version": 1, "evidence_hash": snapshot_hash,
                "search_limit": search_limit, "status": "STARTED", "updated_at": utc_now(),
            })
        elif refresh and existing["evidence_hash"] != snapshot_hash:
            with pool.transaction() as conn:
                conn.execute("UPDATE weekly_runs SET version = version + 1, evidence_hash = ?, status = 'STARTED', updated_at = ? WHERE run_id = ?",
                             (snapshot_hash, utc_now(), run_id))
                conn.execute("UPDATE runs SET status = 'STARTED', finished_at = NULL WHERE run_id = ?", (run_id,))
        pool.update_run_config(run_id, weekly_mode="reasoned", weekly_contract=VERSION)

    @property
    def state(self) -> dict[str, Any]:
        return self.pool.one("SELECT * FROM weekly_runs WHERE run_id = ?", (self.run_id,)) or {}

    def reserve(self, kind: str) -> bool:
        column, ceiling = ("searches_used", "search_limit") if kind == "search" else ("revisions_used", "2")
        if kind not in {"search", "revision"}:
            raise ValueError(kind)
        with self.pool.transaction() as conn:
            result = conn.execute(f"UPDATE weekly_runs SET {column} = {column} + 1, updated_at = ? WHERE run_id = ? AND {column} < {ceiling}", (utc_now(), self.run_id))
        return result.rowcount == 1

    def select(self, call_id: int, selections: Sequence[Mapping[str, Any]]) -> None:
        call = self.pool.one("SELECT * FROM weekly_calls WHERE call_id = ?", (call_id,))
        if not call or call["status"] != "VALIDATED" or call["role"] not in {"Assessment", "Critic"} or call["run_id"] != self.run_id or call["version"] != self.state["version"]:
            raise ContractError("Selection requires a genuine validated role invocation for this revision.")
        if list(selections) != call["output"].get("selections"):
            raise ContractError("Selection does not match the recorded role output.")
        run = self.pool.run(self.run_id) or {}
        config = run.get("config") or {}
        sources = {row["signal_id"]: row for row in self.pool.assessments(self.run_id)}
        for selection in selections:
            signal = sources[str(selection["signal_id"])]
            value = dict(selection)
            duplicate = (signal.get("link") or {}).get("duplicate_of")
            if duplicate and value["decision"] in {"INCLUDE", "WATCH"}:
                value |= {"decision": "EXCLUDE", "duplicate_of": duplicate,
                          "reason": f"Source attached to representative {duplicate}. Proposed decision: {selection['reason']}"}
            value["time_scope"] = time_scope(signal.get("published_at"), date.fromisoformat(run["run_date"]), int(config.get("window_days") or 7))
            for entry in self.pool.rows("SELECT * FROM weekly_evidence WHERE run_id = ? AND signal_id = ?", (self.run_id, signal["signal_id"])):
                provenance = entry["provenance"]
                if provenance.get("status") == "SUCCEEDED" and max(provenance.get("retrieved_at", ""), provenance.get("approved_at", ""))[:10] > run["run_date"]:
                    value["time_scope"] = "HINDSIGHT"
            self.pool.insert("weekly_selections", {
                "run_id": self.run_id, "version": self.state["version"], "signal_id": selection["signal_id"],
                "call_id": call_id, "selection_json": value, "created_at": utc_now(),
            })

    def issues(self) -> dict[str, dict[str, Any]]:
        return {row["issue_id"]: row for row in self.pool.rows(
            """SELECT w.* FROM weekly_issues w WHERE w.run_id = ? AND w.version = ?
               AND w.revision_id = (SELECT MAX(i.revision_id) FROM weekly_issues i
                 WHERE i.run_id = w.run_id AND i.version = w.version AND i.issue_id = w.issue_id)
               AND w.status = 'OPEN'""", (self.run_id, self.state["version"]))}

    def record_critique(self, call_id: int, response: Mapping[str, Any]) -> None:
        opened = self.issues()
        for key in response["resolved_issue_ids"]:
            if key not in opened:
                continue  # already resolved by another batch reviewed in the same parallel wave
            previous = opened[key]["issue"]
            if previous["target"] not in response["reviewed_ids"]:
                raise ContractError("Cannot resolve an issue outside the reviewed scope.")
            resolution = next(item for item in response["resolutions"] if item["issue_id"] == key)
            self._issue(call_id, key, "RESOLVED", {**previous, "resolution": resolution})
        targets: dict[str, set[str]] = {}
        for row in self.pool.rows("SELECT issue_id, issue_json FROM weekly_issues WHERE run_id = ? AND version = ?",
                                  (self.run_id, self.state["version"])):
            targets.setdefault(row["issue_id"], set()).add(str(row["issue"]["target"]))
        recorded = {row["issue_id"] for row in self.pool.rows(
            "SELECT issue_id FROM weekly_issues WHERE call_id = ? AND status = 'OPEN'", (call_id,))}
        for issue in response["issues"]:
            key = issue["issue_id"]
            # Batches cannot see each other's IDs, so a reused ID on another target is renamed, not merged.
            if targets.get(key, {issue["target"]}) != {issue["target"]}:
                key = f"{key}@{issue['target']}"
            if key in recorded:
                continue
            self._issue(call_id, key, "OPEN", {**issue, "issue_id": key})
            self.pool.add_qa(self.run_id, item=str(issue["target"]), correction=issue["problem"],
                             source_ids=issue["source_ids"], origin="WEEKLY_CRITIC")

    def _issue(self, call_id: int, key: str, status: str, issue: Mapping[str, Any]) -> None:
        self.pool.insert("weekly_issues", {
            "run_id": self.run_id, "version": self.state["version"], "issue_id": key,
            "call_id": call_id, "status": status, "issue_json": dict(issue), "created_at": utc_now(),
        })

    def current(self) -> bool:
        return self.state["evidence_hash"] == analysis_hash(self.pool, self.run_id)

    def finish(self, status: str) -> None:
        with self.pool.transaction() as conn:
            conn.execute("UPDATE weekly_runs SET status = ?, updated_at = ? WHERE run_id = ?", (status, utc_now(), self.run_id))


class WeeklyCoordinator:
    def __init__(self, store: WeeklyStore, *, client: Any, model: str, call: RoleCall = call_json,
                 fast: bool = False, workers: int = 1, progress: Callable[[str], Any] | None = None) -> None:
        self.store, self.pool, self.run_id = store, store.pool, store.run_id
        self.client, self.model, self.call = client, model, call
        self.fast, self.workers = fast, max(1, int(workers))
        self.retries = 2 if fast else 1
        self.progress = progress or (lambda _message: None)

    @property
    def sources(self) -> dict[str, dict[str, Any]]:
        run = self.pool.run(self.run_id)
        sources = {}
        for row in evidence_snapshot(self.pool, self.run_id):
            verified = [entry for entry in row.get("evidence_records", []) if entry.get("status") == "SUCCEEDED" and entry.get("scope") == "EXTRACTED_PAGE"]
            sources[row["signal_id"]] = {**row, "text": row["text"] + "".join("\nExtracted source page:\n" + entry["text"] for entry in verified),
                                        "time_scope": time_scope(row.get("published_at"), date.fromisoformat(run["run_date"]), int(run["config"].get("window_days") or 7))}
            if any(max(entry.get("retrieved_at", ""), entry.get("approved_at", ""))[:10] > run["run_date"]
                   for entry in row.get("evidence_records", []) if entry.get("status") == "SUCCEEDED"):
                sources[row["signal_id"]]["time_scope"] = "HINDSIGHT"
        return sources

    def included_ids(self, sources: Mapping[str, Mapping[str, Any]], before_round: int | None = None) -> list[str]:
        """Latest editorial INCLUDE/WATCH selections; deterministic keeps before any selection exists.
        before_round ignores that round's own full-review selections, so its inputs survive a resume."""
        exclude = "" if before_round is None else f"pool-{before_round}-%"
        latest = {row["signal_id"]: row["selection"] for row in self.pool.rows(
            """SELECT w.* FROM weekly_selections w WHERE w.run_id = ? AND w.version = ? AND w.selection_id = (
                 SELECT MAX(s.selection_id) FROM weekly_selections s JOIN weekly_calls c ON c.call_id = s.call_id
                 WHERE s.run_id = w.run_id AND s.version = w.version AND s.signal_id = w.signal_id AND c.task NOT LIKE ?)""",
            (self.run_id, self.store.state["version"], exclude))}
        return sorted(key for key, row in sources.items()
                      if (latest[key]["decision"] in {"INCLUDE", "WATCH"} if key in latest else row.get("decision") == "KEEP"))

    def related(self, batch: Sequence[str], sources: Mapping[str, Mapping[str, Any]],
                included: Sequence[str] | None = None) -> dict[str, Any]:
        """Index entries for linked duplicates and included items sharing a register condition with the batch."""
        members = set(batch)
        linked = {(sources[key].get("link") or {}).get("duplicate_of") for key in batch} - {None}
        linked |= {key for key, row in sources.items() if (row.get("link") or {}).get("duplicate_of") in members}
        conditions = {item for key in batch for item in (sources[key].get("condition_ids") or [])}
        sharing = [key for key in (self.included_ids(sources) if included is None else included)
                   if key not in members and conditions & set(sources[key].get("condition_ids") or [])]
        sharing.sort(key=lambda key: (sources[key].get("tier") or 9, key))
        chosen = [key for key in sorted(linked) if key in sources and key not in members] + sharing
        return {key: index_view(sources[key]) for key in dict.fromkeys(chosen[:RELATED_LIMIT])}

    def batch_issues(self, batch: Sequence[str], sources: Mapping[str, Any]) -> dict[str, Any]:
        members = set(batch)
        return {key: row for key, row in self.store.issues().items()
                if str(row["issue"].get("target")) in members or str(row["issue"].get("target")) not in sources}

    def report_sources(self, brief: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        cited = {key for key in set(brief.get("evidence_ids") or []) | set(brief.get("membership") or []) if key in sources}
        view = {key: index_view(sources[key]) for key in self.included_ids(sources) if key not in cited}
        return view | {key: index_view(sources[key], 800) for key in sorted(cited)}

    def invoke(self, role: str, task: str, payload: dict[str, Any], validate: Callable[[Any], Any],
               prompt: str | None = None) -> tuple[dict[str, Any], int]:
        job = self._prepare(role, task, payload, validate, prompt)
        if "done" in job:
            return job["done"]
        return self._complete(job, self._execute(job, validate))

    def invoke_many(self, jobs: Sequence[tuple[str, str, dict[str, Any], Callable[[Any], Any], str | None]],
                    label: str | None = None) -> list[tuple[dict[str, Any], int]]:
        """Independent calls run on worker threads; recording happens here, in job order, so replay is deterministic."""
        prepared = [self._prepare(*job) for job in jobs]
        pending = [index for index, job in enumerate(prepared) if "done" not in job]
        finished = len(jobs) - len(pending)
        def report() -> None:
            if label:
                self.progress(f"  {label}: {finished}/{len(jobs)} calls done ({100 * finished // max(1, len(jobs))}%)")
        report()
        outcomes: dict[int, Any] = {}
        if self.workers > 1 and len(pending) > 1:
            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {executor.submit(self._execute, prepared[index], jobs[index][3]): index for index in pending}
                for future in as_completed(futures):
                    outcomes[futures[future]] = future.result()
                    finished += 1
                    if finished % self.workers == 0 or finished == len(jobs):
                        report()
        else:
            for index in pending:
                outcomes[index] = self._execute(prepared[index], jobs[index][3])
                finished += 1
            if pending:
                report()
        results: list[Any] = []
        error: Exception | None = None
        for index, job in enumerate(prepared):
            if "done" in job:
                results.append(job["done"])
                continue
            try:
                results.append(self._complete(job, outcomes[index]))
            except (LoopError, ContractError) as exc:
                error = error or exc
                results.append(None)
        if error is not None:
            raise error
        return results

    def _prepare(self, role: str, task: str, payload: dict[str, Any], validate: Callable[[Any], Any],
                 prompt: str | None = None) -> dict[str, Any]:
        system = PROMPTS[prompt or role]
        run = self.pool.run(self.run_id)
        envelope = {"contract": VERSION, "run": run["run_date"], "pack": run["pack"],
                    "authority": run["config"].get("weekly_context", {}), "task": task, **payload}
        digest = canonical_hash({"model": self.model, "system": system, "payload": envelope})
        version = self.store.state["version"]
        previous = self.pool.one(
            """SELECT * FROM weekly_calls WHERE run_id = ? AND version = ? AND role = ? AND task = ?
               AND input_hash = ? AND status = 'VALIDATED' ORDER BY call_id DESC LIMIT 1""",
            (self.run_id, version, role, task, digest))
        if previous:
            validate(previous["output"])
            return {"done": (previous["output"], previous["call_id"])}
        if self.fast:
            # Identical input in an earlier version: reuse the answer and record it under this version.
            earlier = self.pool.one(
                """SELECT * FROM weekly_calls WHERE run_id = ? AND role = ? AND task = ? AND input_hash = ?
                   AND status = 'VALIDATED' ORDER BY call_id DESC LIMIT 1""", (self.run_id, role, task, digest))
            if earlier:
                try:
                    validate(earlier["output"])
                except ContractError:
                    earlier = None
            if earlier:
                record = self.pool.insert("weekly_calls", {
                    "run_id": self.run_id, "version": version, "role": role, "task": task,
                    "prompt_version": VERSION, "model": self.model, "input_hash": digest, "input_json": envelope,
                    "output_json": earlier["output"], "output_hash": canonical_hash(earlier["output"]),
                    "status": "VALIDATED", "created_at": utc_now(),
                })
                self.pool.log(actor_type="AGENT", actor=role, run_id=self.run_id, version=VERSION,
                              model=self.model, subject=task, input_hash=digest, output_hash=canonical_hash(earlier["output"]),
                              status="VALIDATED", detail={"call_id": record, "reused_from": earlier["call_id"]})
                return {"done": (earlier["output"], record)}
        size = len(json.dumps(envelope, ensure_ascii=False, default=str))
        if size > MAX_PAYLOAD_CHARS:
            self.store.finish("INCOMPLETE")
            self.pool.finish_run(self.run_id, "INCOMPLETE", notes="Weekly reasoning: INCOMPLETE")
            raise ContractError(f"{role} {task}: input of {size} characters exceeds the {MAX_PAYLOAD_CHARS}-character model budget.")
        record = self.pool.insert("weekly_calls", {
            "run_id": self.run_id, "version": version, "role": role, "task": task,
            "prompt_version": VERSION, "model": self.model, "input_hash": digest, "input_json": envelope,
            "output_json": {}, "status": "STARTED", "created_at": utc_now(),
        })
        return {"role": role, "task": task, "system": system, "envelope": envelope, "digest": digest, "record": record}

    def _execute(self, job: Mapping[str, Any], validate: Callable[[Any], Any]) -> tuple[Any, list[dict[str, Any]], Exception | None]:
        """The model call and contract check only; no database access, so it is safe on a worker thread."""
        raw: Any = {}
        rejected: list[dict[str, Any]] = []
        try:
            repairs = 3 if job["role"] == "Reporting" else 1
            for attempt in range(repairs + 1):
                payload = job["envelope"]
                if rejected:
                    errors = "; ".join(item["error"] for item in rejected)
                    payload = {**job["envelope"], "previous_output": raw,
                               "contract_feedback": f"Your previous output was rejected: {errors} Return a corrected complete response."}
                    size = len(json.dumps(payload, ensure_ascii=False, default=str))
                    if size > MAX_PAYLOAD_CHARS:
                        raise ContractError(f"{job['role']} {job['task']}: repair input of {size} characters exceeds the {MAX_PAYLOAD_CHARS}-character model budget.")
                raw = self.call(self.client, model=self.model, system=job["system"], payload=payload, retries=self.retries)
                try:
                    validate(raw)
                    break
                except ContractError as exc:
                    if attempt == repairs:
                        raise
                    rejected.append({"error": str(exc), "output": raw})
        except (LoopError, ContractError) as exc:
            return raw, rejected, exc
        return raw, rejected, None

    def _complete(self, job: Mapping[str, Any], outcome: tuple[Any, list[dict[str, Any]], Exception | None]) -> tuple[dict[str, Any], int]:
        raw, rejected, error = outcome
        role, task, digest, record = job["role"], job["task"], job["digest"], job["record"]
        for item in rejected:
            self.pool.log(actor_type="AGENT", actor=role, run_id=self.run_id, version=VERSION,
                          model=self.model, subject=task, input_hash=digest, status="REJECTED",
                          detail={"call_id": record, **item})
        if error is not None:
            with self.pool.transaction() as conn:
                conn.execute("UPDATE weekly_calls SET status = 'FAILED', output_json = ?, error = ? WHERE call_id = ?",
                             (json.dumps(raw), str(error), record))
            self.store.finish("INCOMPLETE")
            self.pool.finish_run(self.run_id, "INCOMPLETE", notes="Weekly reasoning: INCOMPLETE")
            self.pool.log(actor_type="AGENT", actor=role, run_id=self.run_id, version=VERSION,
                          model=self.model, subject=task, input_hash=digest, status="FAILED",
                          detail={"call_id": record, "error": str(error)})
            raise error
        with self.pool.transaction() as conn:
            conn.execute("UPDATE weekly_calls SET status = 'VALIDATED', output_json = ?, output_hash = ? WHERE call_id = ?",
                         (json.dumps(raw), canonical_hash(raw), record))
        self.pool.log(actor_type="AGENT", actor=role, run_id=self.run_id, version=VERSION,
                      model=self.model, subject=task, input_hash=digest, output_hash=canonical_hash(raw),
                      status="VALIDATED", detail={"call_id": record})
        return raw, record

    def supervise(self, actions: Sequence[str], context: Mapping[str, Any]) -> str:
        def validate(raw: Any) -> None:
            raw = object_response(raw)
            no_controlled_fields(raw)
            if raw.get("next_action") not in actions or not isinstance(raw.get("reason"), str) or not raw["reason"].strip():
                raise ContractError("Supervisor must choose a permitted next action with a reason.")
        response, _ = self.invoke("Supervisor", "next-action", {"allowed_actions": list(actions), "context": dict(context), "budget": self.store.state}, validate)
        return response["next_action"]

    def research(self, task: str, search: Callable[[str], Mapping[str, Any]] | None, reassess: Callable[[], Any]) -> bool:
        def validate(raw: Any) -> None:
            raw = object_response(raw)
            no_controlled_fields(raw)
            if not isinstance(raw.get("queries"), list) or not isinstance(raw.get("missing_checks"), list) or not all(isinstance(item, str) for item in raw["missing_checks"]):
                raise ContractError("Researcher needs queries and missing_checks lists.")
            for query in raw["queries"]:
                if not isinstance(query, Mapping) or not isinstance(query.get("query"), str) or not query["query"].strip() or not isinstance(query.get("reason"), str):
                    raise ContractError("A search needs query text and a rationale.")
                source_ids(query.get("signal_ids"), self.sources)
            source_ids(raw.get("verify_source_ids", []), self.sources)
            history = raw.get("history_queries", [])
            if not isinstance(history, list) or not all(isinstance(item, str) and 3 <= len(item.strip()) <= 500 for item in history):
                raise ContractError("History queries must be specific text between 3 and 500 characters.")
        sources = self.sources
        included = self.included_ids(sources)
        dropped: dict[str, int] = {}
        for key, row in sources.items():
            if key not in included:
                reason = str(row.get("drop_reason") or ("ASSESSMENT_MISSING" if row.get("assessment_missing") else "EXCLUDED"))
                dropped[reason] = dropped.get(reason, 0) + 1
        response, call_id = self.invoke("Researcher", task, {
            "sources": {key: index_view(sources[key]) for key in included},
            "excluded_counts": dropped, "open_issues": self.store.issues(),
            "search_budget": self.store.state,
            "evidence_scope": "index of currently included items with short excerpts; excluded items summarized by reason",
        }, validate)
        added = False
        for request in response["queries"]:
            query = request["query"].strip()
            key = canonical_hash({"query": query})
            previous = self.pool.one("SELECT * FROM execution_log WHERE run_id = ? AND actor = 'WeeklyCountersearch' AND input_hash = ? AND status = 'SUCCEEDED'", (self.run_id, key))
            if previous:
                continue
            status, result = "NOT_SEARCHED", {"reason": "Search is disabled or the recorded run budget is exhausted."}
            if search is not None and self.store.reserve("search"):
                result = dict(search(query))
                if not isinstance(result.get("candidates"), list) or result.get("status") != "SUCCEEDED":
                    status = "FAILED"
                else:
                    status = "SUCCEEDED"
                    for rank, candidate in enumerate(result["candidates"], 1):
                        signal_id = self.pool.add_signal(
                            url=candidate["url"], title=candidate["title"], text=candidate["text"],
                            publisher=candidate.get("publisher"), published_at=candidate.get("published_at"),
                            lane=candidate.get("lane") or "COUNTERSEARCH",
                            content_hash=canonical_hash({"text": candidate["text"]}))
                        self.pool.add_sighting(signal_id, self.run_id, query_id=f"counter-{key[:16]}",
                                               query=query, lane="COUNTEREVIDENCE", target_id=None, rank=rank)
                        added = True
                    issue_id = f"SEARCH-{key[:16]}"
                    if issue_id in self.store.issues():
                        self.store._issue(call_id, issue_id, "RESOLVED", self.store.issues()[issue_id]["issue"])
            self.pool.log(actor_type="FUNCTION", actor="WeeklyCountersearch", run_id=self.run_id, version=VERSION,
                          subject=str(call_id), input_hash=key, output_hash=canonical_hash(result), status=status,
                          detail={"request": request, "result": result})
            if status != "SUCCEEDED":
                self.store._issue(call_id, f"SEARCH-{key[:16]}", "OPEN", {
                    "issue_id": f"SEARCH-{key[:16]}", "target": "RESEARCH", "kind": "COVERAGE",
                    "problem": f"Required countercheck not completed: {query}", "material": True,
                    "source_ids": request["signal_ids"], "missing_evidence": status,
                })
        for signal_id in response.get("verify_source_ids", []):
            previous = self.pool.one("SELECT * FROM weekly_evidence WHERE run_id = ? AND signal_id = ? AND kind = 'PAGE_VERIFICATION' ORDER BY evidence_id DESC LIMIT 1", (self.run_id, signal_id))
            if previous and previous["provenance"].get("status") == "SUCCEEDED":
                continue
            result = {"status": "NOT_CHECKED", "reason": "The explicit network budget is exhausted or disabled."}
            if self.store.reserve("search"):
                from grit.agents.weekly_evidence import verify_page
                result = verify_page(self.sources[signal_id]["url"])
            self.pool.insert("weekly_evidence", {"run_id": self.run_id, "signal_id": signal_id,
                "kind": "PAGE_VERIFICATION", "provenance_json": {**result, "invocation_id": call_id, "retrieved_at": utc_now()}, "created_at": utc_now()})
            added = True
            self.pool.log(actor_type="FUNCTION", actor="WeeklyPageVerification", run_id=self.run_id, version=VERSION,
                          subject=signal_id, status=result["status"], output_hash=canonical_hash(result), detail=result)
            if result["status"] != "SUCCEEDED":
                self.store._issue(call_id, f"PAGE-{signal_id}", "OPEN", {
                    "issue_id": f"PAGE-{signal_id}", "target": signal_id, "kind": "COVERAGE", "material": True,
                    "problem": "Requested source-page verification was not completed.", "source_ids": [signal_id],
                    "missing_evidence": result["reason"],
                })
            elif f"PAGE-{signal_id}" in self.store.issues():
                self.store._issue(call_id, f"PAGE-{signal_id}", "RESOLVED", self.store.issues()[f"PAGE-{signal_id}"]["issue"])
        pack = self.pool.run(self.run_id)["pack"]
        for terms in response.get("history_queries", []):
            escaped = terms.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            history = self.pool.rows(
                """SELECT DISTINCT s.* FROM signals s JOIN assessments a ON a.signal_id = s.signal_id
                   WHERE a.pack = ? AND a.run_id != ? AND LOWER(s.title || ' ' || s.text) LIKE ? ESCAPE '\\'
                   ORDER BY s.published_at DESC, s.signal_id LIMIT 21""", (pack, self.run_id, "%" + escaped.casefold() + "%"))
            self.pool.log(actor_type="FUNCTION", actor="WeeklyHistoryLookup", run_id=self.run_id, version=VERSION,
                          subject=str(call_id), status="SUCCEEDED", detail={"terms": terms, "signal_ids": [row["signal_id"] for row in history[:20]], "truncated": len(history) > 20})
            for record in history[:20]:
                self.pool.add_sighting(record["signal_id"], self.run_id, query_id="history-" + canonical_hash({"terms": terms})[:16],
                                       query=terms, lane="HISTORICAL_EVIDENCE", target_id=None, rank=None)
                added = True
        for check in response["missing_checks"]:
            self.pool.add_qa(self.run_id, item="Research coverage", correction=check, origin="WEEKLY_RESEARCHER")
        if added:
            reassess()
            with self.pool.transaction() as conn:
                conn.execute("UPDATE weekly_runs SET evidence_hash = ?, status = 'STARTED' WHERE run_id = ?",
                             (analysis_hash(self.pool, self.run_id), self.run_id))
        return added

    def assess_and_critique(self, round_number: int, only: set[str] | None = None) -> None:
        sources = self.sources
        keys = sorted(sources) if only is None else sorted(set(sources) & only)
        for offset in range(0, len(keys), 16):
            batch_keys = keys[offset:offset + 16]
            batch = {key: full_view(sources[key]) for key in batch_keys}
            related = self.related(batch_keys, sources)
            selections, assessment_id = self.invoke("Assessment", f"pool-{round_number}-{offset}", {
                "candidates": batch, "sources": related, "open_issues": self.batch_issues(batch_keys, sources), "admitted_corrections": memory_context(self.pool, self.run_id),
            }, lambda raw: check_selections(raw, sources, set(batch)))
            self.store.select(assessment_id, selections["selections"])
            critique, critic_id = self.invoke("Critic", f"pool-{round_number}-{offset}", {
                "candidates": batch, "sources": related, "assessment": selections,
                "open_issues": self.batch_issues(batch_keys, sources), "admitted_corrections": memory_context(self.pool, self.run_id),
            }, lambda raw: (check_critique(raw, set(batch), sources, set(self.store.issues())),
                            check_selections(raw, sources, set(batch))))
            self.store.select(critic_id, critique["selections"])
            self.store.record_critique(critic_id, critique)

    def review(self, round_number: int, only: set[str] | None = None) -> None:
        if not self.fast:
            self.assess_and_critique(round_number, only)
            return
        sources = self.sources
        keys = set(sources) if only is None else set(sources) & set(only)
        full = keys
        if only is None:
            dropped = {key for key in keys if sources[key].get("decision") != "KEEP" and not sources[key].get("assessment_missing")}
            full = (keys - dropped) | self.screen(round_number, sorted(dropped), sources)
        self.full_review(round_number, sorted(full), sources)

    def scoped(self, members: set[str], sources: Mapping[str, Any], open_issues: Mapping[str, Any],
               selections: bool = True) -> Callable[[Any], Any]:
        """A thread-safe critique check against a snapshot of the open issues."""
        def validate(raw: Any) -> None:
            check_critique(raw, members, sources, set(open_issues))
            outside = [key for key in raw["resolved_issue_ids"] if str(open_issues[key]["issue"].get("target")) not in members]
            if outside and isinstance(raw, dict):
                # Only a batch that reviewed an issue's target may resolve it; the attempt is kept for audit.
                raw["resolved_issue_ids"] = [key for key in raw["resolved_issue_ids"] if key not in outside]
                raw["resolutions"] = [item for item in raw.get("resolutions", []) if item.get("issue_id") not in outside]
                raw["ignored_resolutions"] = outside
            if selections:
                check_selections(raw, sources, members)
        return validate

    def screen(self, round_number: int, keys: Sequence[str], sources: Mapping[str, Any]) -> set[str]:
        """The Critic checks deterministic drops from compact records; any decision except EXCLUDE escalates."""
        open_issues = self.store.issues()
        corrections = memory_context(self.pool, self.run_id)
        jobs = []
        for batch in stable_batches(keys, SCREEN_BATCH, SCREEN_BATCH_MAX):
            members = set(batch)
            jobs.append(("Critic", f"screen-{round_number}-{batch[0]}", {
                "candidates": {key: index_view(sources[key], SCREEN_TEXT_CHARS) for key in batch},
                "open_issues": {key: row for key, row in open_issues.items() if str(row["issue"].get("target")) in members},
                "admitted_corrections": corrections,
            }, self.scoped(members, sources, open_issues), "Screen"))
        escalated: set[str] = set()
        for critique, call_id in self.invoke_many(jobs, f"Critic screen of {len(keys)} dropped signals"):
            self.store.select(call_id, critique["selections"])
            self.store.record_critique(call_id, critique)
            escalated |= {row["signal_id"] for row in critique["selections"] if row["decision"] != "EXCLUDE"}
        self.progress(f"  Critic screen escalated {len(escalated)} dropped signals to full review.")
        return escalated

    def full_review(self, round_number: int, keys: Sequence[str], sources: Mapping[str, Any]) -> None:
        """Every batch's input comes from the state before this round, so all batches run in one parallel queue."""
        batches = stable_batches(keys, FULL_BATCH, FULL_BATCH_MAX)
        if not batches:
            return
        included = self.included_ids(sources, before_round=round_number)
        open_issues = self.store.issues()
        corrections = memory_context(self.pool, self.run_id)
        inputs = [{"candidates": {key: full_view(sources[key]) for key in batch},
                   "sources": self.related(batch, sources, included), "open_issues": self.batch_issues(batch, sources),
                   "admitted_corrections": corrections} for batch in batches]
        assessments = self.invoke_many([
            ("Assessment", f"pool-{round_number}-{batch[0]}", payload,
             (lambda members: lambda raw: check_selections(raw, sources, members))(set(batch)), None)
            for batch, payload in zip(batches, inputs)], f"Assessor full review of {len(keys)} signals")
        for selections, call_id in assessments:
            self.store.select(call_id, selections["selections"])
        critiques = self.invoke_many([
            ("Critic", f"pool-{round_number}-{batch[0]}", {**payload, "assessment": selections},
             self.scoped(set(batch), sources, open_issues), None)
            for batch, payload, (selections, _) in zip(batches, inputs, assessments)], f"Critic full review of {len(keys)} signals")
        for critique, call_id in critiques:
            self.store.select(call_id, critique["selections"])
            self.store.record_critique(call_id, critique)

    def digests(self) -> dict[str, str]:
        return {row["signal_id"]: canonical_hash(row) for row in evidence_snapshot(self.pool, self.run_id)}

    def changed(self, before: Mapping[str, str]) -> set[str]:
        return {key for key, value in self.digests().items() if before.get(key) != value}

    def reconcile_pool(self, *, search: Callable[[str], Mapping[str, Any]] | None, reassess: Callable[[], Any]) -> None:
        action = self.supervise(["RESEARCH", "ASSESS"], {"stage": "initial coverage"})
        if action == "ASSESS":
            self.review(0)
        before = self.digests() if self.fast else {}
        added = self.research("initial-counterevidence", search, reassess)
        if action != "ASSESS":
            self.review(1)
        elif added:
            # Fast review re-examines only signals whose evidence changed; full review repeats the pass.
            self.review(1, self.changed(before) if self.fast else None)
        while any(row["issue"]["material"] for row in self.store.issues().values()) and self.store.reserve("revision"):
            round_number = self.store.state["revisions_used"] + 1
            action = self.supervise(["RESEARCH", "REASSESS"], {"stage": "pool reconciliation", "issues": self.store.issues()})
            before = self.digests() if self.fast else {}
            added = action == "RESEARCH" and self.research(f"counterevidence-{round_number}", search, reassess)
            # Revision rounds revisit only challenged candidates and any newly acquired evidence.
            sources = self.sources
            targets = {str(row["issue"].get("target")) for row in self.store.issues().values() if row["issue"].get("material")}
            unselected = set(sources) - {row["signal_id"] for row in self.pool.rows(
                "SELECT DISTINCT signal_id FROM weekly_selections WHERE run_id = ? AND version = ?", (self.run_id, self.store.state["version"]))}
            if added and not self.fast:
                self.assess_and_critique(round_number)
                continue
            scope = (targets | unselected | (self.changed(before) if added else set())) & set(sources)
            if scope:
                # Fast round 1 is the post-research pass, so its revision rounds are numbered from 2.
                self.review(round_number + 1 if self.fast else round_number, scope)

    def report_payload(self, profile_id: str, brief: Mapping[str, Any],
                       sources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "report": model_brief(brief), "editable_fields": narrative_fields(brief),
            "sources": self.report_sources(brief, sources), "open_issues": self.store.issues(), "budget": self.store.state,
            "reader_memory": memory_context(self.pool, self.run_id, profile_id),
            "human_feedback": [row["membership"].get("review") for row in self.pool.rows(
                "SELECT * FROM packages WHERE run_id = ? AND profile_id = ? AND status IN ('RETURNED_FOR_REWORK', 'REJECTED') ORDER BY version",
                (self.run_id, profile_id))],
        }

    def report(self, profile: Any, draft: Mapping[str, Any], routed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        brief = copy.deepcopy(dict(draft))
        sources = self.sources
        task = f"report-{profile.profile_id}"
        target = f"REPORT:{profile.profile_id}"
        while True:
            payload = self.report_payload(profile.profile_id, brief, sources)
            editable = payload["editable_fields"]
            def validate_edits(raw: Any) -> None:
                raw = object_response(raw)
                no_controlled_fields(raw)
                edits = raw.get("edits")
                if not isinstance(edits, list) or not all(isinstance(item, Mapping) for item in edits):
                    raise ContractError("Reporting needs an edits list.")
                paths = [edit.get("path") for edit in edits]
                if not all(isinstance(path, str) for path in paths) or len(paths) != len(set(paths)) or not {"bottom_line.text", "bottom_line.escalation"}.issubset(paths):
                    raise ContractError("Reporting must synthesize both bottom-line fields once.")
                for edit in edits:
                    if edit.get("path") not in editable or not isinstance(edit.get("text"), str) or not edit["text"].strip():
                        raise ContractError("Reporting attempted an unknown narrative edit.")
                    refs = source_ids(edit.get("source_ids"), sources)
                    marks = {ref.strip() for group in CITE.findall(edit["text"]) for ref in group.split(",")}
                    if set(refs) != marks or (sources and not refs):
                        raise ContractError("Every narrative edit needs matching evidence citations.")
                    if refs:
                        last = 0
                        for mark in CITE.finditer(edit["text"]):
                            claim = edit["text"][last:mark.start()].strip(" .!?")
                            if re.search(r"[.!?]\s+[A-Z]", claim):
                                raise ContractError("Cite each factual sentence, not an entire paragraph.")
                            last = mark.end()
                        if edit["text"][last:].strip(" .!?\n"):
                            raise ContractError("Uncited narrative after the final reference.")
                    problems = text_problems(CITE.sub("", edit["text"]), " ".join(sources[ref]["text"] for ref in refs), "report edit")
                    if problems:
                        raise ContractError("; ".join(problems))
            response, _ = self.invoke("Reporting", task, payload, validate_edits)
            for edit in response["edits"]:
                apply_edit(brief, edit, sources)
            brief["evidence_ids"] = sorted(set(brief.get("evidence_ids", [])) | set(brief["membership"]))
            selected = {row["signal_id"] for row in self.pool.weekly_assessments(self.run_id)}
            brief["membership"] = sorted(set(brief["membership"]) & selected)
            label_date_exceptions(brief, sources)
            brief["bottom_line"]["drafted_by"] = "Reporting; independently critiqued before readiness"
            brief["method"]["weekly"] = {"contract": VERSION, "budget": self.store.state,
                "issues": list(self.store.issues().values()), "selections": selection_summary(self.pool.weekly_assessments(self.run_id, include_excluded=True)),
                "evidence_scope": "stored excerpts; full-page and internal checks are not implied"}
            brief["content_hash"] = report_hash(brief)
            open_issues = self.store.issues()
            critique, critic_id = self.invoke("Critic", f"final-{profile.profile_id}", {
                "report": model_brief(brief), "report_hash": brief["content_hash"], "targets": [target],
                "sources": self.report_sources(brief, sources), "open_issues": open_issues,
            }, self.scoped({target}, sources, open_issues, selections=False))
            self.store.record_critique(critic_id, critique)
            material = any(row["issue"]["material"] for row in self.store.issues().values())
            if not material or not self.store.reserve("revision"):
                break
            self.supervise(["REVISE_REPORT"], {"profile": profile.profile_id, "issues": self.store.issues()})
        status = "INCOMPLETE" if material or not sources or not self.store.current() or not set(brief["evidence_ids"]).issubset(sources) or any(row.get("assessment_missing") for row in sources.values()) or any(
            row["editorial"]["decision"] == "DEFER" for row in self.pool.weekly_assessments(self.run_id, include_excluded=True)) else "READY"
        self.pool.insert("weekly_reports", {
            "run_id": self.run_id, "version": self.store.state["version"], "profile_id": profile.profile_id,
            "content_hash": brief["content_hash"], "selection_hash": selection_hash(self.pool, self.run_id),
            "critique_call_id": critic_id, "status": status,
            "brief_json": brief, "routed_json": list(routed), "created_at": utc_now(),
        })
        return {"profile_id": profile.profile_id, "status": status, "content_hash": brief["content_hash"]}


def report_hash(brief: Mapping[str, Any]) -> str:
    return canonical_hash({key: value for key, value in brief.items() if key != "content_hash"})


def selection_hash(pool: Pool, run_id: str) -> str:
    return canonical_hash({"selections": sorted(
        [{"signal_id": row["signal_id"], "editorial": row["editorial"]} for row in pool.weekly_assessments(run_id, include_excluded=True)],
        key=lambda row: row["signal_id"])})


def narrative_fields(brief: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {"bottom_line.text": brief["bottom_line"]["text"], "bottom_line.escalation": brief["bottom_line"]["escalation"]}
    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            if {"runs", "plain", "sources"}.issubset(value):
                fields[path] = value["plain"]
            else:
                for key, item in value.items():
                    if key not in {"method", "meta", "order_book", "red", "membership", "row", "qa", "scenarios"}:
                        visit(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}.{index}")
    visit(brief, "")
    for index, row in enumerate(brief.get("findings") or []):
        for key in ("what_happened", "hypothesis"):
            if isinstance((row.get("card") or {}).get(key), str):
                fields[f"findings.{index}.card.{key}"] = row["card"][key]
    for index, row in enumerate((brief.get("v2") or {}).get("escalating") or []):
        if isinstance((row.get("response") or {}).get("action"), str):
            fields[f"v2.escalating.{index}.response.action"] = row["response"]["action"]
    return fields


def apply_edit(brief: dict[str, Any], edit: Mapping[str, Any], sources: Mapping[str, Any]) -> None:
    parts = edit["path"].split(".")
    current: Any = brief
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    key = parts[-1]
    runs, refs = citations(edit["text"], sources)
    if isinstance(current[key], Mapping):
        current[key] = {"runs": runs, "plain": plain(runs), "sources": refs}
    else:
        current[key] = plain(runs)
    brief["membership"] = sorted(set(brief["membership"]) | set(edit["source_ids"]))
    if parts[0] == "conditions" and key == "basis":
        from grit.report.brief import text_only
        current["development"] = text_only(runs)


def label_date_exceptions(value: Any, sources: Mapping[str, Any]) -> None:
    if isinstance(value, dict):
        source = sources.get(value.get("signal_id")) if isinstance(value.get("signal_id"), str) else None
        if source:
            scope = source.get("time_scope")
            label = {"HINDSIGHT": "hindsight", "EARLIER_CONTEXT": "earlier context"}.get(scope)
            if label:
                for key in ("date", "when"):
                    if value.get(key) and label not in value[key]:
                        value[key] += f" ({label})"
        for key, item in value.items():
            if key != "method":
                label_date_exceptions(item, sources)
    elif isinstance(value, list):
        for item in value:
            label_date_exceptions(item, sources)


def frozen_report(pool: Pool, run_id: str, profile_id: str, *, ready: bool = True) -> dict[str, Any]:
    state = pool.one("SELECT * FROM weekly_runs WHERE run_id = ?", (run_id,))
    if state is None or state["evidence_hash"] != analysis_hash(pool, run_id):
        raise ValueError("Weekly evidence changed or has not been reviewed.")
    row = pool.one("SELECT * FROM weekly_reports WHERE run_id = ? AND version = ? AND profile_id = ? ORDER BY report_id DESC LIMIT 1", (run_id, state["version"], profile_id))
    if row is None or (ready and row["status"] != "READY"):
        raise ValueError("The exact weekly report is not ready for human review.")
    if ready and state["status"] == "INCOMPLETE":
        raise ValueError("The current weekly analysis is incomplete.")
    if row["selection_hash"] != selection_hash(pool, run_id):
        raise ValueError("Weekly editorial selection changed after report critique.")
    if report_hash(row["brief"]) != row["content_hash"]:
        raise ValueError("Weekly report snapshot hash mismatch.")
    call = pool.one("SELECT * FROM weekly_calls WHERE call_id = ?", (row["critique_call_id"],))
    if call is None or call["status"] != "VALIDATED" or call["role"] != "Critic" or call["input"].get("report_hash") != row["content_hash"]:
        raise ValueError("Missing genuine critique for the exact report snapshot.")
    if ready:
        store = WeeklyStore(pool, run_id, search_limit=state["search_limit"], refresh=False)
        if any(issue["issue"]["material"] for issue in store.issues().values()):
            raise ValueError("Unresolved material objections prevent review readiness.")
    return row


def run_weekly(pool: Pool, run: Any, *, client: Any, model: str, search_limit: int,
               profiles: Sequence[Any], call: RoleCall = call_json,
               search: Callable[[str], Mapping[str, Any]] | None = None,
               reassess: Callable[[], Any] | None = None, draft_sections: Callable[[], Any] | None = None,
               progress: Callable[[str], Any] | None = None) -> dict[str, Any]:
    store = WeeklyStore(pool, run.run_id, search_limit=search_limit)
    if reassess is None or draft_sections is None:
        raise ValueError("Weekly reasoning requires deterministic reassessment and section-drafting functions.")
    store.finish("STARTED")
    with pool.transaction() as conn:
        conn.execute("UPDATE runs SET status = 'STARTED', finished_at = NULL WHERE run_id = ?", (run.run_id,))
    config = (pool.run(run.run_id) or {}).get("config") or {}
    fast = config.get("weekly_review") == "fast"
    coordinator = WeeklyCoordinator(store, client=client, model=model, call=call, fast=fast,
                                    workers=int(config.get("agent_workers") or DEFAULT_WORKERS) if fast else 1, progress=progress)
    coordinator.reconcile_pool(search=search, reassess=reassess)
    coordinator.supervise(["DRAFT_REPORT"], {"stage": "pool reconciled", "issues": store.issues()})
    draft_sections()
    return _finish_weekly_reports(pool, run, profiles, coordinator)


def _finish_weekly_reports(pool: Pool, run: Any, profiles: Sequence[Any], coordinator: WeeklyCoordinator) -> dict[str, Any]:
    from grit.report.brief import build_brief, routed_signals
    results = [coordinator.report(profile, build_brief(pool, run.run_id, profile), routed_signals(pool, run.run_id, profile))
               for profile in profiles if profile.brief_for(run.pack)]
    ready = bool(results) and all(result["status"] == "READY" for result in results)
    coordinator.store.finish("READY" if ready else "INCOMPLETE")
    pool.finish_run(run.run_id, "COMPLETE" if ready else "INCOMPLETE", notes="Weekly reasoning: " + coordinator.store.state["status"])
    return {"status": coordinator.store.state["status"], "reports": results, "budget": coordinator.store.state}


def resume_weekly_reporting(pool: Pool, run: Any, *, client: Any, model: str | None,
                            profiles: Sequence[Any], call: RoleCall = call_json,
                            progress: Callable[[str], Any] | None = None) -> dict[str, Any]:
    state = pool.one("SELECT * FROM weekly_runs WHERE run_id = ?", (run.run_id,))
    saved_run = pool.run(run.run_id)
    if not state or not saved_run or (saved_run.get("config") or {}).get("weekly_mode") != "reasoned":
        raise ValueError("No saved reasoned weekly analysis exists for this run.")
    if state["status"] != "INCOMPLETE":
        raise ValueError("Report recovery requires an incomplete weekly analysis.")
    if state["evidence_hash"] != analysis_hash(pool, run.run_id):
        raise ValueError("Weekly evidence changed after the saved review; report recovery cannot skip reassessment.")
    failed = pool.one("SELECT * FROM weekly_calls WHERE run_id = ? AND version = ? ORDER BY call_id DESC LIMIT 1",
                      (run.run_id, state["version"]))
    if not failed or failed["role"] != "Reporting" or failed["status"] != "FAILED" or not failed["task"].startswith("report-"):
        raise ValueError("Report recovery requires the latest weekly call to be a failed Reporting call.")
    supervisors = pool.rows(
        "SELECT * FROM weekly_calls WHERE run_id = ? AND version = ? AND role = 'Supervisor' AND status = 'VALIDATED' AND call_id < ? ORDER BY call_id DESC",
        (run.run_id, state["version"], failed["call_id"]))
    checkpoint = next((row for row in supervisors if row["input"].get("context", {}).get("stage") == "pool reconciled"
                       and row["output"].get("next_action") == "DRAFT_REPORT"), None)
    if not checkpoint:
        raise ValueError("The saved analysis has no validated pool-reconciliation checkpoint.")
    selected = pool.weekly_assessments(run.run_id, include_excluded=True)
    latest = pool.rows(
        """SELECT s.signal_id, c.role, c.status, c.task, c.call_id FROM weekly_selections s
           JOIN weekly_calls c ON c.call_id = s.call_id
           WHERE s.run_id = ? AND s.version = ? AND s.selection_id = (
             SELECT MAX(t.selection_id) FROM weekly_selections t
             WHERE t.run_id = s.run_id AND t.version = s.version AND t.signal_id = s.signal_id)""",
        (run.run_id, state["version"]))
    if not selected or {row["signal_id"] for row in latest} != {row["signal_id"] for row in selected} or any(
        row["role"] != "Critic" or row["status"] != "VALIDATED" or not row["task"].startswith(("pool-", "screen-"))
        or row["call_id"] >= checkpoint["call_id"] for row in latest
    ):
        raise ValueError("The latest selection for every candidate must have a validated Critic before the report checkpoint.")
    profile_id = failed["task"][len("report-"):]
    anchor = next((profile for profile in profiles if profile.profile_id == profile_id and profile.brief_for(run.pack)), None)
    if anchor is None:
        raise ValueError("Report recovery requires the profile whose Reporting call failed.")
    store = WeeklyStore(pool, run.run_id, search_limit=state["search_limit"], refresh=False)
    config = saved_run.get("config") or {}
    fast = config.get("weekly_review") == "fast"
    coordinator = WeeklyCoordinator(store, client=client, model=model or failed["model"], call=call, fast=fast,
                                    workers=int(config.get("agent_workers") or DEFAULT_WORKERS) if fast else 1, progress=progress)
    from grit.report.brief import build_brief
    draft = build_brief(pool, run.run_id, anchor)
    current = coordinator.report_payload(profile_id, draft, coordinator.sources)
    previous = failed["input"]
    def report_core(value: Mapping[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if key not in {"method", "content_hash"}}
    if not isinstance(previous.get("report"), Mapping) or canonical_hash(report_core(current["report"])) != canonical_hash(report_core(previous["report"])):
        raise ValueError("The saved report draft changed after the failed call; report recovery cannot reuse it.")
    for key in ("editable_fields", "sources", "open_issues", "reader_memory", "human_feedback"):
        if key not in previous or canonical_hash(current[key]) != canonical_hash(previous[key]):
            raise ValueError(f"The saved report input changed: {key}.")
    old_budget = previous.get("budget") or {}
    if any(old_budget.get(key) != state[key] for key in ("version", "evidence_hash", "search_limit", "searches_used", "revisions_used")):
        raise ValueError("The saved analysis budget changed after the failed call.")
    coordinator.progress("Resuming the report from saved critique; no search or signal review will run.")
    store.finish("STARTED")
    with pool.transaction() as conn:
        conn.execute("UPDATE runs SET status = 'STARTED', finished_at = NULL WHERE run_id = ?", (run.run_id,))
    return _finish_weekly_reports(pool, run, profiles, coordinator)


def run_memory(pool: Pool, *, package_id: str, version: int, client: Any, model: str, call: RoleCall = call_json) -> dict[str, Any]:
    package = pool.one("SELECT * FROM packages WHERE package_id = ? AND version = ?", (package_id, version))
    if not package or package["status"] == "PENDING_REVIEW" or not package.get("decided_by") or not package.get("decided_at"):
        raise ValueError("Memory requires a recorded named human decision on the exact package.")
    report_id = package["membership"].get("weekly_report_id")
    snapshot = pool.one("SELECT * FROM weekly_reports WHERE report_id = ?", (report_id,))
    if not snapshot or snapshot["content_hash"] != package["membership"]["brief_hash"]:
        raise ValueError("Memory requires the decided weekly report snapshot.")
    state = pool.one("SELECT * FROM weekly_runs WHERE run_id = ?", (package["run_id"],))
    store = WeeklyStore(pool, package["run_id"], search_limit=state["search_limit"], refresh=False)
    coordinator = WeeklyCoordinator(store, client=client, model=model, call=call)
    sources = {row["signal_id"]: row for row in snapshot["brief"]["method"]["weekly"]["selections"]}
    def validate(raw: Any) -> None:
        raw = object_response(raw)
        no_controlled_fields(raw)
        if not isinstance(raw.get("proposals"), list):
            raise ContractError("Memory needs a proposals list.")
        for proposal in raw["proposals"]:
            if not isinstance(proposal, Mapping) or not isinstance(proposal.get("kind"), str) or proposal["kind"] not in {"CORRECTION", "READER_PREFERENCE"} or not isinstance(proposal.get("text"), str) or not proposal["text"].strip():
                raise ContractError("Invalid typed memory proposal.")
            if not source_ids(proposal.get("source_ids"), sources):
                raise ContractError("Memory proposals must identify evidence.")
            if proposal["kind"] == "CORRECTION" and proposal.get("profile_id") is not None:
                raise ContractError("A shared correction cannot be a reader preference.")
            if proposal["kind"] == "READER_PREFERENCE" and proposal.get("profile_id") != package["profile_id"]:
                raise ContractError("A preference cannot affect another reader.")
    response, invocation = coordinator.invoke("Memory", f"{package_id}-v{version}", {
        "decision": {"package": package_id, "version": version, "hash": package["content_hash"],
                     "status": package["status"], "by": package["decided_by"], "at": package["decided_at"],
                     "feedback": package["membership"].get("review")},
        "report": model_brief(snapshot["brief"]),
        "sources": {key: row for key, row in sources.items()
                    if key in set(snapshot["brief"].get("evidence_ids") or []) | set(snapshot["brief"].get("membership") or [])
                    or row["editorial"]["decision"] in {"INCLUDE", "WATCH"}},
        "policy": "Proposals are retained for admission, never applied to shared facts or profile appetite automatically.",
    }, validate)
    for proposal in response["proposals"]:
        if not pool.one("SELECT 1 FROM weekly_memory WHERE package_id = ? AND package_version = ? AND call_id = ? AND proposal_json = ?",
                        (package_id, version, invocation, json.dumps(proposal, ensure_ascii=False, sort_keys=True))):
            pool.insert("weekly_memory", {"package_id": package_id, "package_version": version, "call_id": invocation,
                                         "proposal_json": proposal, "status": "PROPOSED", "created_at": utc_now()})
    return {"package_id": package_id, "version": version, "memory": "PROPOSED", "proposals": len(response["proposals"])}


def memory_context(pool: Pool, run_id: str, profile_id: str | None = None) -> list[dict[str, Any]]:
    run = pool.run(run_id)
    records = pool.rows(
        """SELECT m.* FROM weekly_memory m JOIN weekly_memory_admissions a ON a.memory_id = m.memory_id
           JOIN packages p ON p.package_id = m.package_id AND p.version = m.package_version
           JOIN runs r ON r.run_id = p.run_id WHERE a.intent = 'ADMIT' AND r.pack = ?""", (run["pack"],))
    return [row["proposal"] for row in records if row["proposal"]["kind"] == "CORRECTION" or (
        profile_id is not None and row["proposal"].get("profile_id") == profile_id)]


def admit_memory(pool: Pool, memory_id: int, reviewer: Any, intent: str) -> dict[str, Any]:
    row = pool.one("SELECT * FROM weekly_memory WHERE memory_id = ?", (memory_id,))
    if row is None or intent not in {"ADMIT", "REJECT"}:
        raise ValueError("A known memory proposal and ADMIT or REJECT are required.")
    proposal = row["proposal"]
    call = pool.one("SELECT * FROM weekly_calls WHERE call_id = ?", (row["call_id"],))
    package = pool.one("SELECT * FROM packages WHERE package_id = ? AND version = ?", (row["package_id"], row["package_version"]))
    if not call or call["role"] != "Memory" or call["status"] != "VALIDATED" or proposal not in call["output"].get("proposals", []) or not package or not package.get("decided_by"):
        raise ValueError("A memory admission requires a genuine post-decision Memory proposal.")
    if proposal["kind"] == "CORRECTION":
        if reviewer.review_role not in {"REVIEWER", "BA_HEAD"}:
            raise PermissionError("Only a named reviewer can admit shared corrections.")
    elif proposal.get("profile_id") != reviewer.profile_id:
        raise PermissionError("Only the named reader can admit their preference.")
    existing = pool.one("SELECT * FROM weekly_memory_admissions WHERE memory_id = ?", (memory_id,))
    if existing:
        if existing["intent"] == intent and existing["reviewer_id"] == reviewer.profile_id:
            return existing
        raise ValueError("A memory admission is immutable; propose a new correction instead.")
    record = {"memory_id": memory_id, "reviewer_id": reviewer.profile_id, "intent": intent, "created_at": utc_now()}
    pool.insert("weekly_memory_admissions", record)
    pool.log(actor_type="FUNCTION", actor="MemoryAdmissionGate", subject=str(memory_id), status=intent, detail=record)
    return record


def add_internal_evidence(pool: Pool, run_id: str, records: Any, approver: Any) -> list[str]:
    if approver.review_role not in {"REVIEWER", "BA_HEAD"}:
        raise PermissionError("Internal evidence needs a named reviewer or BA head.")
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise ValueError("Internal evidence must be a JSON list of records.")
    for row in records:
        if not all(isinstance(row.get(key), str) and row[key].strip() for key in ("title", "text", "reference")):
            raise ValueError("Each internal record needs title, text, and a document reference.")
        if row.get("published_at") is not None:
            date.fromisoformat(row["published_at"][:10])
    ids = []
    for row in records:
        digest = canonical_hash(row)
        signal_id = pool.add_signal(url="urn:grit:approved-evidence:" + digest, title=row["title"], text=row["text"],
                                    publisher="Approved internal evidence", published_at=row.get("published_at"),
                                    lane="HUMAN_APPROVED", content_hash=digest)
        pool.add_sighting(signal_id, run_id, query_id="human-" + digest[:16], query=row["reference"],
                          lane="HUMAN_APPROVED", target_id=None, rank=None)
        if not pool.one("SELECT 1 FROM weekly_evidence WHERE run_id = ? AND signal_id = ? AND kind = 'HUMAN_APPROVED'", (run_id, signal_id)):
            pool.insert("weekly_evidence", {"run_id": run_id, "signal_id": signal_id, "kind": "HUMAN_APPROVED",
                "provenance_json": {"status": "SUCCEEDED", "scope": "APPROVED_INTERNAL", "reference": row["reference"],
                                    "approved_by": approver.profile_id, "approved_at": utc_now()}, "created_at": utc_now()})
        ids.append(signal_id)
    return ids


def countersearch(query: str) -> dict[str, Any]:
    """One budgeted Brave call; unlike the legacy helper, preserve failures and empty results separately."""
    from types import SimpleNamespace
    from grit.acquisition.brave import BraveFetchError
    from grit.acquisition.brave_plan import collect_plan
    try:
        result = collect_plan([SimpleNamespace(query_id=canonical_hash({"query": query})[:16], query=query, exact=False)],
                              fetched_at=utc_now(), freshness=None, country="ALL")
    except (BraveFetchError, ValueError) as exc:
        return {"status": "FAILED", "candidates": [], "reason": str(exc)}
    return {"status": "FAILED" if result.stopped else "SUCCEEDED",
            "candidates": [candidate.as_record() for candidate in result.candidates], "stopped": result.stopped}
