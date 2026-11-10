"""Opt-in weekly editorial reasoning, separate from scoring and human release."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Callable, Mapping, Sequence

from grit.agents.checks import sentence_cite_problems, text_problems
from grit.agents.loop.llm import LoopError, call_json
from grit.agents.weekly import (
    significance_band,
    EXPOSURE_STATUSES, EXPOSURE_TYPES,
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
INTERNAL_LIMIT = 20
MAX_PAYLOAD_CHARS = 2_000_000
INDEX_FIELDS = ("signal_id", "title", "publisher", "source_band", "published_at", "event_dates", "time_scope", "decision", "drop_reason",
                "tier", "condition_ids", "red", "assessment_missing", "internal_exposure", "hindsight_checks")
EVENT_LEAD_CHARS, EVENT_DATES_MAX = 600, 3
# The included findings that must each have a recorded contradiction search before release.
TOP_FINDINGS = 5
# Rewrites each report gets after a material final critique, separate from the pool's revision rounds.
REPORT_REWRITES = 1
QUERY_PURPOSES = frozenset({"CONTRADICT", "COVERAGE", "CORROBORATE"})
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


SAME_EVENT_DAYS, SAME_EVENT_TERMS, SAME_EVENT_MAX = 7, 2, 6
# Transmission that may enter DeepSeek ranking without a named register anchor.
RANK_RUNGS = frozenset({"DIRECT", "COUNTERPARTY", "SECTOR"})
_ASK_CONFIRM = re.compile(r"\b(confirm|reconfirm|check)\b", re.I)
_ASK_TIME = re.compile(r"\b(before|by|next cycle|the next cycle)\b", re.I)
_ASK_MANY = re.compile(r"\b(and also|secondly|additionally, consider)\b", re.I)


def escalation_problems(text: str, *, owners: Sequence[str], forum: str = "", forum_date: str = "",
                        triggers: Sequence[str] = ()) -> list[str]:
    """Reject an escalation that does not name who, a confirmation verb, and a time, unless a forum ask applies."""
    if not isinstance(text, str) or not text.strip():
        return ["bottom_line.escalation is empty."]
    found: list[str] = []
    if forum and forum_date:
        if forum.casefold() not in text.casefold() or forum_date.casefold() not in text.casefold():
            found.append(f"bottom_line.escalation must name the meeting {forum} on {forum_date}.")
        if triggers and not any(str(trigger).casefold() in text.casefold() for trigger in triggers):
            found.append("bottom_line.escalation must name one RED trigger from the pack patterns.")
        if _ASK_MANY.search(text) or text.count("Consider ") > 1:
            found.append("bottom_line.escalation must state one ask, not several.")
        return found
    roles = [role for role in owners if isinstance(role, str) and role.strip()]
    if roles and not any(role.casefold() in text.casefold() for role in roles):
        found.append("bottom_line.escalation must name an owner role from the pack.")
    if not _ASK_CONFIRM.search(text):
        found.append("bottom_line.escalation must ask to confirm, reconfirm, or check something.")
    if not _ASK_TIME.search(text):
        found.append("bottom_line.escalation must name a time (before, by, or the next cycle).")
    if _ASK_MANY.search(text) or text.count("Consider ") > 1:
        found.append("bottom_line.escalation must state one ask, not several.")
    return found


def rank_noise(editorial: Mapping[str, Any], source: Mapping[str, Any], names: Sequence[str]) -> bool:
    """True when a kept signal should leave the ranking: no register anchor and no material transmission."""
    from grit.agents.weekly import _register_named

    if not names:
        return False
    fact = " ".join(str(editorial.get(field) or "") for field in ("fact", "exposure", "transmission", "impact"))
    haystack = f"{fact} {source.get('title') or ''}"
    has_anchor = bool(editorial.get("risk_ids")) or bool(source.get("condition_ids")) or _register_named(haystack, names)
    rung = str(source.get("rung") or (source.get("link") or {}).get("rung") or "").upper()
    return not has_anchor and rung not in RANK_RUNGS


def event_terms(row: Mapping[str, Any]) -> frozenset[str]:
    """Distinctive headline words (grit.core.headlines)."""
    from grit.core.headlines import distinctive_terms

    return distinctive_terms(str(row.get("title") or ""))


def band_rank(row: Mapping[str, Any]) -> int:
    from grit.core.source_bands import BANDS

    band = str(row.get("source_band") or "UNRATED")
    return BANDS.index(band) if band in BANDS else len(BANDS)


def same_event(key: str, sources: Mapping[str, Mapping[str, Any]], terms_of: Mapping[str, frozenset[str]] | None = None) -> list[str]:
    """Other candidates whose headlines share at least two distinctive words with this one, published within a week.
    terms_of: precomputed event_terms per candidate, so a batch does not recompute them."""
    terms_of = terms_of if terms_of is not None else {other: event_terms(candidate) for other, candidate in sources.items()}
    row = sources[key]
    terms = terms_of.get(key) or frozenset()
    if len(terms) < SAME_EVENT_TERMS:
        return []
    day = str(row.get("published_at") or "")[:10]
    found = []
    for other, candidate in sources.items():
        if other == key or len(terms & (terms_of.get(other) or frozenset())) < SAME_EVENT_TERMS:
            continue
        when = str(candidate.get("published_at") or "")[:10]
        try:
            if day and when and abs((date.fromisoformat(day) - date.fromisoformat(when)).days) > SAME_EVENT_DAYS:
                continue
        except ValueError:
            pass
        found.append(other)
    return sorted(found, key=lambda other: (band_rank(sources[other]), other))[:SAME_EVENT_MAX]


def full_view(row: Mapping[str, Any], limit: int = FULL_TEXT_CHARS) -> dict[str, Any]:
    text = str(row.get("text") or "")
    value = {key: item for key, item in row.items() if key not in {"evidence_records", "harm", "link", "revision"}}
    value["text"] = text if len(text) <= limit else text[:limit] + " [truncated]"
    harm = row.get("harm") or {}
    if harm:
        value["harm"] = {"status": harm.get("status")}
    link = {key: item for key, item in (row.get("link") or {}).items() if isinstance(item, (str, int, float, bool)) and item is not None}
    if link:
        value["link"] = link
    return value


def late_evidence(entry: Mapping[str, Any], run_date: str | None) -> bool:
    """A check retrieved or approved after the run date is hindsight. The source it checks keeps its own date."""
    return bool(run_date) and entry.get("status") == "SUCCEEDED" and max(
        str(entry.get("retrieved_at") or ""), str(entry.get("approved_at") or ""))[:10] > str(run_date)


def evidence_text(row: Mapping[str, Any], run_date: str | None = None) -> str:
    """The text a role reads: a verified source page first, then the latest revision seen by the run date, then the
    text first stored. Labels appear only when there is more than the stored text, so excerpts lead with the freshest."""
    verified = [(entry["text"], late_evidence(entry, run_date)) for entry in row.get("evidence_records", [])
                if entry.get("status") == "SUCCEEDED" and entry.get("scope") == "EXTRACTED_PAGE" and entry.get("text")]
    revision = row.get("revision") or {}
    if not verified and not revision.get("text"):
        return str(row.get("text") or "")
    parts = [f"Extracted source page (verified{', retrieved after the run date: hindsight' if late else ''}):\n{text}"
             for text, late in verified]
    if revision.get("text"):
        parts.append(f"Updated page text (published {str(revision.get('published_at') or '')[:10]}, seen {str(revision.get('seen_at') or '')[:10]}):\n{revision['text']}")
    parts.append(f"Text first stored:\n{row.get('text') or ''}")
    return "\n\n".join(parts)


def event_dates(row: Mapping[str, Any], run_date: date, *, lead_chars: int = EVENT_LEAD_CHARS) -> list[str]:
    """Dates (day and month) stated in the title or lead, within a year of the run date. These are the dates an event is
    said to happen; published_at is only when it was reported."""
    from grit.report.lookahead import dates_in

    lead = (row.get("revision") or {}).get("text") or row.get("text") or ""
    text = f"{row.get('title') or ''}. {' '.join(str(lead).split())[:lead_chars]}"
    found = [day for day in dates_in(text, run_date) if abs((day - run_date).days) <= 366]
    return [day.isoformat() for day in found[:EVENT_DATES_MAX]]


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


SCREEN_LEAD_CHARS = 240


def matched_terms(row: Mapping[str, Any]) -> list[str]:
    """The pack terms the deterministic reader matched on this page."""
    link = row.get("link") or {}
    terms = [hit.get("matched") for key in ("involves", "reader_hits") for hit in link.get(key) or [] if isinstance(hit, Mapping)]
    terms += [term if isinstance(term, str) else (term or {}).get("matched") for key in ("lob_keywords", "hazards") for term in link.get(key) or []]
    return list(dict.fromkeys(str(term) for term in terms if term))


def screen_view(row: Mapping[str, Any], chars: int = SCREEN_TEXT_CHARS) -> dict[str, Any]:
    """The lead, then the sentences naming a matched pack term, so a screen sees why the page was linked or dropped."""
    value = index_view(row, chars)
    text = " ".join(str(row.get("text") or "").split())
    terms = [term.lower() for term in matched_terms(row)]
    if len(text) <= chars or not terms:
        return value
    excerpt = text[:SCREEN_LEAD_CHARS]
    for sentence in re.split(r"(?<=[.!?])\s+", text[SCREEN_LEAD_CHARS:]):
        if any(term in sentence.lower() for term in terms):
            room = chars - len(excerpt) - 5
            if room <= 40:
                break
            excerpt += " … " + sentence[:room]
    value["excerpt"] = excerpt
    if terms:
        value["matched_terms"] = matched_terms(row)[:6]
    return value


def evidence_snapshot(pool: Pool, run_id: str) -> list[dict[str, Any]]:
    assessed = {row["signal_id"]: row for row in pool.assessments(run_id)}
    for signal in pool.signals_for_run(run_id):
        if signal["signal_id"] not in assessed:
            assessed[signal["signal_id"]] = {**signal, "assessment_missing": True}
    for evidence in pool.rows("SELECT * FROM weekly_evidence WHERE run_id = ? ORDER BY evidence_id", (run_id,)):
        if evidence["signal_id"] in assessed:
            assessed[evidence["signal_id"]].setdefault("evidence_records", []).append(evidence["provenance"])
    run_date = str((pool.run(run_id) or {}).get("run_date") or "") or None
    for row in pool.rows("SELECT DISTINCT signal_id FROM signal_revisions"):
        if row["signal_id"] in assessed:
            revision = pool.latest_revision(row["signal_id"], by=run_date)
            if revision:
                assessed[row["signal_id"]]["revision"] = {key: revision[key] for key in ("content_hash", "title", "text", "published_at", "seen_at")}
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


def scenario_history(pool: Pool, run_id: str, brief: Mapping[str, Any]) -> set[str]:
    """Signals the scenario outlook shows as signposts seen earlier in its window, from an earlier run of the pack. They
    were kept in that run, are shown by rule with their own dates, and are not cited by the narrative, so this run's review
    need not cover them."""
    shown = {signal_id for shift in (brief.get("scenarios") or {}).get("shifts") or [] for case in shift.get("cases") or []
             for item in [*(case.get("signposts_seen") or []), *(case.get("counter_indicators_seen") or [])]
             for signal_id in item.get("signal_ids") or []}
    if not shown:
        return set()
    marks = ",".join("?" * len(shown))
    here = {row["signal_id"] for row in pool.rows(f"SELECT signal_id FROM assessments WHERE run_id = ? AND signal_id IN ({marks})", (run_id, *shown))}
    return shown - here


def model_brief(brief: Mapping[str, Any]) -> dict[str, Any]:
    """The report as the model sees it: the full selection audit is replaced by counts and baseline changes, and the
    QA log, which records earlier objections and checks, by its size, so an old objection is not read as current text."""
    value = copy.deepcopy(dict(brief))
    if isinstance(value.get("qa"), list):
        value["qa"] = {"rows": len(value["qa"]), "note": "Audit history of earlier objections and checks, not report content."}
    weekly = (value.get("method") or {}).get("weekly")
    if isinstance(weekly, dict) and isinstance(weekly.get("selections"), list):
        rows = weekly["selections"]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["editorial"]["decision"]] = counts.get(row["editorial"]["decision"], 0) + 1
        changed = [row for row in rows if (row.get("baseline_decision") == "KEEP") != (row["editorial"]["decision"] in {"INCLUDE", "WATCH"})]
        weekly["selections"] = {"counts": counts, "changed_from_baseline": changed}
    return value


FIXED_VALUES_RULE = (
    "These values are set by rule before Reporting and no rewrite can change them. Status follows the agreed policy: "
    "ELEVATED needs identifiable possible or confirmed exposure, credible evidence and near-term timing, or a credible "
    "immediate threat; it does not need confirmed impact. RED needs impact confirmed by the Risk & Assurance operator. "
    "A condition trend comes from that condition's own assessment, which a Critic has already challenged. "
    "Do not raise a material issue against these values. "
    "Text that states a value and its basis accurately is correct. If you think a value is wrong, raise a non-material "
    "issue so the Risk & Assurance operator sees it; challenge overstated wording, not the value. An open issue that "
    "objects only to one of these values may be resolved with source_ids [] and a response saying so.")


def fixed_values(brief: Mapping[str, Any]) -> dict[str, Any]:
    """The rule-set values in a report that the final Critic reviews but Reporting cannot edit."""
    basis = ((brief.get("v2") or {}).get("status_basis") or {}) if isinstance(brief.get("v2"), Mapping) else {}
    return {"rule": FIXED_VALUES_RULE, "status": (brief.get("v2") or {}).get("status"), "status_basis": basis.get("basis"),
            "condition_trends": {str(row.get("condition_id")): row.get("trend") for row in brief.get("conditions") or []
                                 if isinstance(row, Mapping) and row.get("condition_id")}}


class WeeklyStore:
    def __init__(self, pool: Pool, run_id: str, *, search_limit: int, refresh: bool = True) -> None:
        if type(search_limit) is not int or search_limit < 0:
            raise ValueError("An explicit nonnegative countersearch limit is required. Use 0 to disable.")
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
        """kind "contradiction": a contradiction search for a top finding that has none can go up to TOP_FINDINGS past
        an enabled search limit for each evidence version. Review can change the top findings after the budget is spent,
        and every top finding needs one before release (URS methodology, section 18). The allowance is per version
        because the search count carries across versions. Without it, a later version would inherit an allowance that an
        earlier one spent."""
        column, ceiling = {"search": ("searches_used", "search_limit"), "revision": ("revisions_used", "2"),
                           "contradiction": ("searches_used", f"search_limit + {TOP_FINDINGS} * version")}.get(kind, (None, None))
        if column is None:
            raise ValueError(kind)
        if kind == "contradiction" and int(self.state.get("search_limit") or 0) <= 0:
            return False
        with self.pool.transaction() as conn:
            result = conn.execute(f"UPDATE weekly_runs SET {column} = {column} + 1, updated_at = ? WHERE run_id = ? AND {column} < {ceiling}", (utc_now(), self.run_id))
        return result.rowcount == 1

    def can_search_to_contradict(self) -> bool:
        """The run could still pay for a contradiction search: an enabled limit with its per-version allowance left."""
        state = self.state
        limit = int(state.get("search_limit") or 0)
        return limit > 0 and int(state.get("searches_used") or 0) < limit + TOP_FINDINGS * int(state.get("version") or 1)

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
            if any(late_evidence(entry["provenance"], run["run_date"]) for entry in self.pool.rows(
                    "SELECT * FROM weekly_evidence WHERE run_id = ? AND signal_id = ?", (self.run_id, signal["signal_id"]))):
                value["hindsight_checks"] = True
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
                continue  # only a batch that reviewed the target may resolve it; the attempt stays in the call output
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
                 fast: bool = False, workers: int = 1, progress: Callable[[str], Any] | None = None,
                 reasoner: str | None = None, harness: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None) -> None:
        self.store, self.pool, self.run_id = store, store.pool, store.run_id
        self.client, self.model, self.call = client, model, call
        self.fast, self.workers = fast, max(1, int(workers))
        self.retries = 2 if fast else 1
        self.progress = progress or (lambda _message: None)
        self.reasoner = reasoner if reasoner == "deepseek" else None
        self.harness = harness

    @property
    def sources(self) -> dict[str, dict[str, Any]]:
        run = self.pool.run(self.run_id)
        sources = {}
        run_date = date.fromisoformat(run["run_date"])
        for row in evidence_snapshot(self.pool, self.run_id):
            sources[row["signal_id"]] = {**row, "text": evidence_text(row, run["run_date"]),
                                        "time_scope": time_scope(row.get("published_at"), run_date, int(run["config"].get("window_days") or 7))}
            stated = event_dates(row, run_date)
            if stated:
                sources[row["signal_id"]]["event_dates"] = stated
            exposure = [entry["exposure"] for entry in row.get("evidence_records", [])
                        if entry.get("status") == "SUCCEEDED" and entry.get("scope") == "APPROVED_INTERNAL" and entry.get("exposure")]
            if exposure:
                sources[row["signal_id"]]["internal_exposure"] = exposure
            if any(late_evidence(entry, run["run_date"]) for entry in row.get("evidence_records", [])):
                sources[row["signal_id"]]["hindsight_checks"] = True
        return sources

    def included_ids(self, sources: Mapping[str, Mapping[str, Any]], before_round: int | None = None) -> list[str]:
        """Latest editorial INCLUDE/WATCH selections. Before any selection exists, it returns the deterministic keeps.
        before_round ignores that round's own full-review selections, so its inputs survive a resume."""
        exclude = "" if before_round is None else f"pool-{before_round}-%"
        latest = {row["signal_id"]: row["selection"] for row in self.pool.rows(
            """SELECT w.* FROM weekly_selections w WHERE w.run_id = ? AND w.version = ? AND w.selection_id = (
                 SELECT MAX(s.selection_id) FROM weekly_selections s JOIN weekly_calls c ON c.call_id = s.call_id
                 WHERE s.run_id = w.run_id AND s.version = w.version AND s.signal_id = w.signal_id AND c.task NOT LIKE ?)""",
            (self.run_id, self.store.state["version"], exclude))}
        return sorted(key for key, row in sources.items()
                      if (latest[key]["decision"] in {"INCLUDE", "WATCH"} if key in latest else row.get("decision") == "KEEP"))

    def _weekly_context(self) -> Mapping[str, Any]:
        return ((self.pool.run(self.run_id) or {}).get("config") or {}).get("weekly_context") or {}

    def _register_names(self) -> list[str]:
        """Names a cascade hop may end on: standing items and the business's own entities and counterparties."""
        context = self._weekly_context()
        names = [str(item.get("name")) for item in context.get("standing_items") or [] if isinstance(item, Mapping) and item.get("name")]
        business = context.get("business") or {}
        if isinstance(business, Mapping):
            for key in ("entities", "lines_of_business", "customers", "suppliers", "anchors"):
                names += [str(item) for item in business.get(key) or [] if isinstance(item, str)]
        return list(dict.fromkeys(name for name in names if len(name.strip()) >= 4))

    def _register_descriptions(self) -> dict[str, Any]:
        context = self._weekly_context()
        items = [{"id": item.get("id"), "name": item.get("name"), "transmission": item.get("transmission")}
                 for item in (context.get("standing_items") or [])[:80] if isinstance(item, Mapping)]
        business = context.get("business") or {}
        listed = business if isinstance(business, Mapping) else {}
        return {"standing_items": items, "entities": list(listed.get("entities") or [])[:40],
                "lines_of_business": list(listed.get("lines_of_business") or [])[:40]}

    def _note_skip(self, task: str, exc: BaseException) -> None:
        notes = list(((self.pool.run(self.run_id) or {}).get("config") or {}).get("deepseek_notes") or [])
        notes.append({"task": task, "skipped": str(exc)[:200]})
        self.pool.update_run_config(self.run_id, deepseek_notes=notes[-20:])
        self.progress(f"  DeepSeek {task} skipped: {exc}")

    def _harness_invoke(self, role: str, task: str, payload: Mapping[str, Any], validate: Callable[[Any], Any]) -> tuple[dict[str, Any], int] | None:
        """One harness proposal. A contract failure is retried once with the error text, then skipped. Gemini stands."""
        if self.harness is None:
            return None
        from grit.agents.harness import ground_report_edits, ground_selection_ids, normalize_report_edits, prepare_output

        run = self.pool.run(self.run_id)
        base = {"contract": VERSION, "run": run["run_date"], "pack": run["pack"], "task": task,
                "authority": run["config"].get("weekly_context", {}), "accepted_misses": memory_context(self.pool, self.run_id),
                **dict(payload)}
        last_error: BaseException | None = None
        raw: Any = {}
        for attempt in range(2):
            envelope = dict(base)
            if last_error is not None:
                envelope["repair"] = {"error": str(last_error)[:400], "attempt": attempt + 1}
            digest = canonical_hash({"model": self.model, "system": PROMPTS[role], "payload": envelope, "reasoner": "deepseek"})
            record = self.pool.insert("weekly_calls", {
                "run_id": self.run_id, "version": self.store.state["version"], "role": role, "task": task,
                "prompt_version": VERSION, "model": self.model, "input_hash": digest, "input_json": envelope,
                "output_json": {}, "status": "STARTED", "created_at": utc_now(),
            })
            raw = {}
            try:
                raw = prepare_output(self.harness(task, envelope))
                if role == "Reporting":
                    raw = normalize_report_edits(raw, payload.get("editable_fields") or {})
                    raw = ground_report_edits(raw, self.sources)
                elif role == "Assessment" and isinstance(raw, dict):
                    raw = ground_selection_ids(raw, self.sources, payload)
                validate(raw)
            except Exception as exc:
                with self.pool.transaction() as conn:
                    conn.execute("UPDATE weekly_calls SET status = 'FAILED', output_json = ?, error = ? WHERE call_id = ?",
                                 (json.dumps(raw if isinstance(raw, dict) else {}), str(exc)[:500], record))
                last_error = exc
                if attempt == 0:
                    self.progress(f"  DeepSeek {task} retrying after: {exc}")
                    continue
                self._note_skip(task, exc)
                return None
            with self.pool.transaction() as conn:
                conn.execute("UPDATE weekly_calls SET status = 'VALIDATED', output_json = ?, output_hash = ? WHERE call_id = ?",
                             (json.dumps(raw), canonical_hash(raw), record))
            return raw, record
        return None

    def _save_harness_selections(self, task: str, payload: Mapping[str, Any], members: set[str], *, chain: bool,
                                 names: Sequence[str]) -> dict[str, Any] | None:
        def validate(raw: Any) -> None:
            check_selections(raw, self.sources, members, chain=chain, register=self.register, register_names=names)
        found = self._harness_invoke("Assessment", task, payload, validate)
        if found is None:
            return None
        raw, call_id = found
        self.store.select(call_id, raw["selections"])
        return raw

    def _drop_rank_noise(self, chosen: Sequence[str], rows: Mapping[str, Mapping[str, Any]],
                         sources: Mapping[str, Mapping[str, Any]], names: Sequence[str]) -> list[str]:
        """Leave hobby and stock notes out of DeepSeek ranking when they lack a register anchor and a material rung."""
        noise = [key for key in chosen if rank_noise(rows[key].get("editorial") or {}, sources.get(key) or {}, names)]
        keep = [key for key in chosen if key not in set(noise)]
        if noise:
            self.progress(f"  Dropping {len(noise)} signals with no register anchor before ranking")
            self._record_noise_excludes(noise)
        return keep

    def _record_noise_excludes(self, keys: Sequence[str]) -> None:
        """Record EXCLUDE selections for noise so they stay out of the opening summary."""
        selections = [{
            "signal_id": key, "decision": "EXCLUDE",
            "reason": "No register anchor and no direct, counterparty, or sector transmission.",
            "source_ids": [key],
        } for key in keys]
        task = f"noise-{keys[0]}"
        envelope = {"task": task, "selections": selections}
        digest = canonical_hash({"model": self.model, "role": "Assessment", "payload": envelope, "reason": "noise-filter"})
        record = self.pool.insert("weekly_calls", {
            "run_id": self.run_id, "version": self.store.state["version"], "role": "Assessment", "task": task,
            "prompt_version": VERSION, "model": self.model, "input_hash": digest, "input_json": envelope,
            "output_json": {"selections": selections}, "output_hash": canonical_hash({"selections": selections}),
            "status": "VALIDATED", "created_at": utc_now(),
        })
        self.store.select(record, selections)

    def reason_with_harness(self) -> None:
        """Meaning review, cascade, then a ranking of the kept signals. Gemini selections stand when a task fails."""
        if self.reasoner != "deepseek" or self.harness is None:
            return
        sources, names = self.sources, self._register_names()
        dropped = sorted(key for key, row in sources.items() if row.get("drop_reason") == "NOT_RELEVANT")
        rescued: list[str] = []
        offered = 0
        for offset in range(0, len(dropped), 40):
            if len(rescued) >= 12:
                break
            batch = dropped[offset:offset + 40]
            if not batch:
                break
            offered += len(batch)
            self.progress(f"  DeepSeek is reviewing {len(batch)} keyword drops by meaning"
                          + (f" (batch {offset // 40 + 1})" if offset else ""))
            result = self._save_harness_selections(
                f"meaning-{batch[0]}",
                {"candidates": {key: screen_view(sources[key]) for key in batch}, "register_descriptions": self._register_descriptions()},
                set(batch), chain=False, names=names)
            if result:
                for row in result["selections"]:
                    if row["decision"] != "EXCLUDE" and row["signal_id"] not in rescued:
                        rescued.append(row["signal_id"])
                        if len(rescued) >= 12:
                            break
        unseen = max(0, len(dropped) - offered)
        if unseen:
            self.pool.update_run_config(self.run_id, deepseek_unreviewed_drops=unseen)
        if rescued:
            self.progress(f"  DeepSeek sent {len(rescued)} drops to full review")
            self.full_review(90, rescued, self.sources)
            self._save_harness_selections(
                f"cascade-{rescued[0]}",
                {"candidates": {key: index_view(self.sources[key], 800) for key in rescued}, "register_names": names,
                 "register_descriptions": self._register_descriptions()},
                set(rescued), chain=True, names=names)
        rows = {row["signal_id"]: row for row in self.pool.weekly_assessments(self.run_id)}
        chosen = [key for key in self.included_ids(self.sources) if key in rows]
        chosen.sort(key=lambda key: (-significance_score(rows[key].get("editorial") or {}), key))
        chosen = chosen[:80]
        chosen = self._drop_rank_noise(chosen, rows, self.sources, names)
        if not chosen:
            return
        self.progress(f"  DeepSeek is ranking {len(chosen)} included signals")
        ranked = self._save_harness_selections(
            f"rank-{chosen[0]}",
            {"candidates": {key: index_view(self.sources[key], 800) for key in chosen},
             "current_selections": {key: rows[key]["editorial"] for key in chosen}, "register_names": names},
            set(chosen), chain=True, names=names)
        if ranked and isinstance(ranked.get("selections"), list):
            order = [row["signal_id"] for row in ranked["selections"]
                     if isinstance(row, Mapping) and row.get("decision") in {"INCLUDE", "WATCH"} and row.get("signal_id")]
            self.pool.update_run_config(self.run_id, deepseek_rank=order[:12])

    @property
    def register(self) -> frozenset[str]:
        """The standing-item IDs a selection may link to: the pack's register, as given to every role."""
        context = ((self.pool.run(self.run_id) or {}).get("config") or {}).get("weekly_context") or {}
        return frozenset(str(item.get("id")) for item in context.get("standing_items") or [] if isinstance(item, Mapping) and item.get("id"))

    def related(self, batch: Sequence[str], sources: Mapping[str, Mapping[str, Any]],
                included: Sequence[str] | None = None) -> dict[str, Any]:
        """Index entries for linked duplicates, other reports of the same event (best band first), and included items
        sharing a register condition with the batch. Same-event reports let a selection cite its corroboration."""
        members = set(batch)
        linked = {(sources[key].get("link") or {}).get("duplicate_of") for key in batch} - {None}
        linked |= {key for key, row in sources.items() if (row.get("link") or {}).get("duplicate_of") in members}
        terms_of = {key: event_terms(row) for key, row in sources.items()}
        same = sorted({key for member in batch for key in same_event(member, sources, terms_of) if key not in members},
                      key=lambda key: (band_rank(sources[key]), key))
        conditions = {item for key in batch for item in (sources[key].get("condition_ids") or [])}
        sharing = [key for key in (self.included_ids(sources) if included is None else included)
                   if key not in members and conditions & set(sources[key].get("condition_ids") or [])]
        sharing.sort(key=lambda key: (sources[key].get("tier") or 9, key))
        # Approved internal records (the exposure snapshot) go to every batch, so a selection can cite confirmed exposure.
        internal = sorted(key for key, row in sources.items() if row.get("internal_exposure") and key not in members)[:INTERNAL_LIMIT]
        chosen = internal + [key for key in sorted(linked) if key in sources and key not in members] + same + sharing
        return {key: index_view(sources[key]) for key in dict.fromkeys(chosen[:RELATED_LIMIT + len(internal)])}

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
        """Independent calls run on worker threads. Recording happens here, in job order, so replay is deterministic."""
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
        """The model call and contract check only. It has no database access, so it is safe on a worker thread."""
        raw: Any = {}
        rejected: list[dict[str, Any]] = []
        try:
            repairs = 3 if job["role"] == "Reporting" else 2
            for attempt in range(repairs + 1):
                payload = job["envelope"]
                if rejected:
                    errors = "; ".join(item["error"] for item in rejected)
                    feedback = f"Your previous output was rejected: {errors} Return a corrected complete response."
                    if "JSON" in errors:
                        feedback += " Return one JSON object with the fields the instructions name."
                    payload = {**job["envelope"], "contract_feedback": feedback}
                    if isinstance(raw, dict) and raw:
                        payload["previous_output"] = raw
                    size = len(json.dumps(payload, ensure_ascii=False, default=str))
                    if size > MAX_PAYLOAD_CHARS:
                        raise ContractError(f"{job['role']} {job['task']}: repair input of {size} characters exceeds the {MAX_PAYLOAD_CHARS}-character model budget.")
                try:
                    raw = self.call(self.client, model=self.model, system=job["system"], payload=payload, retries=self.retries)
                except LoopError as exc:
                    raw = {}
                    if attempt == repairs:
                        raise
                    rejected.append({"error": str(exc), "output": {}})
                    continue
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
                if query.get("purpose") is not None and query["purpose"] not in QUERY_PURPOSES:
                    raise ContractError("A search purpose must be CONTRADICT, COVERAGE or CORROBORATE.")
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
        top = self.top_findings(sources, included)
        response, call_id = self.invoke("Researcher", task, {
            "sources": {key: index_view(sources[key]) for key in included},
            "excluded_counts": dropped, "open_issues": self.store.issues(),
            "search_budget": self.store.state, "top_findings": top, "contradiction_searched": sorted(self.contradicted()),
            "evidence_scope": "index of currently included items with short excerpts; excluded items summarized by reason",
        }, validate)
        added = False
        # Contradiction searches for top findings spend the budget first.
        # A contradiction search that only repeats completed ones is skipped, so the budget reaches the rest.
        challenged = self.contradicted()
        ordered = sorted(response["queries"], key=lambda request: not (
            request.get("purpose") == "CONTRADICT" and (set(request["signal_ids"]) & set(top)) - challenged))
        for request in ordered:
            query = request["query"].strip()
            key = canonical_hash({"query": query})
            previous = self.pool.one("SELECT * FROM execution_log WHERE run_id = ? AND actor = 'WeeklyCountersearch' AND input_hash = ? AND status = 'SUCCEEDED'", (self.run_id, key))
            if previous or (request.get("purpose") == "CONTRADICT" and set(request["signal_ids"]) <= challenged):
                continue
            status, result = "NOT_SEARCHED", {"reason": "Search is disabled or the recorded run budget is exhausted."}
            needed = request.get("purpose") == "CONTRADICT" and bool((set(request["signal_ids"]) & set(top)) - challenged)
            if search is not None and (self.store.reserve("search") or (needed and self.store.reserve("contradiction"))):
                result = dict(search(query))
                if not isinstance(result.get("candidates"), list) or result.get("status") != "SUCCEEDED":
                    status = "FAILED"
                else:
                    status = "SUCCEEDED"
                    if request.get("purpose") == "CONTRADICT":
                        challenged |= set(request["signal_ids"])
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
                # Only a failed search is material. One the budget could not run is disclosed: the contradiction rule
                # (CONTRA issues) already decides which searches a READY report needs.
                self.store._issue(call_id, f"SEARCH-{key[:16]}", "OPEN", {
                    "issue_id": f"SEARCH-{key[:16]}", "target": "RESEARCH", "kind": "COVERAGE",
                    "problem": (f"Required countercheck not completed: {query}" if status == "FAILED"
                                else f"Countercheck not run because the search budget was spent or disabled: {query}"),
                    "material": status == "FAILED", "source_ids": request["signal_ids"], "missing_evidence": status,
                })
        for signal_id in response.get("verify_source_ids", []):
            previous = self.pool.one("SELECT * FROM weekly_evidence WHERE run_id = ? AND signal_id = ? AND kind = 'PAGE_VERIFICATION' ORDER BY evidence_id DESC LIMIT 1", (self.run_id, signal_id))
            # The source's own answer is final for this run; fetching it again would only spend the budget.
            if previous and (previous["provenance"].get("status") == "SUCCEEDED" or previous["provenance"].get("attempted")):
                continue
            result = {"status": "NOT_CHECKED", "reason": "The explicit network budget is exhausted or disabled."}
            if self.store.reserve("search"):
                from grit.agents.weekly_evidence import verify_page
                result = {**verify_page(self.sources[signal_id]["url"]), "attempted": True}
            self.pool.insert("weekly_evidence", {"run_id": self.run_id, "signal_id": signal_id,
                "kind": "PAGE_VERIFICATION", "provenance_json": {**result, "invocation_id": call_id, "retrieved_at": utc_now()}, "created_at": utc_now()})
            added = True
            self.pool.log(actor_type="FUNCTION", actor="WeeklyPageVerification", run_id=self.run_id, version=VERSION,
                          subject=signal_id, status=result["status"], output_hash=canonical_hash(result), detail=result)
            if result["status"] != "SUCCEEDED":
                # A page the source refused, or one the spent or disabled budget could not check, is disclosed, not a
                # blocker: like an unrun countersearch, the report relies on the retrieved text and says so.
                attempted = bool(result.get("attempted"))
                self.store._issue(call_id, f"PAGE-{signal_id}", "OPEN", {
                    "issue_id": f"PAGE-{signal_id}", "target": signal_id, "kind": "COVERAGE", "material": False,
                    "problem": ("The source page could not be verified, so the report relies on the retrieved text."
                                if attempted else "Source-page verification was not run because the search budget was spent or disabled, "
                                                  "so the report relies on the retrieved text."),
                    "source_ids": [signal_id], "missing_evidence": result["reason"],
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
        self.require_contradiction(call_id, top)
        if added:
            reassess()
            with self.pool.transaction() as conn:
                conn.execute("UPDATE weekly_runs SET evidence_hash = ?, status = 'STARTED' WHERE run_id = ?",
                             (analysis_hash(self.pool, self.run_id), self.run_id))
        return added

    def top_findings(self, sources: Mapping[str, Mapping[str, Any]], included: Sequence[str]) -> list[str]:
        """The included findings a reader is most likely to act on: RED first, then by source tier and corroboration."""
        latest = {row["signal_id"]: row["selection"] for row in self.pool.rows(
            "SELECT signal_id, selection_json FROM weekly_selections WHERE run_id = ? AND version = ? ORDER BY selection_id",
            (self.run_id, self.store.state["version"]))}
        if not latest:
            # Before review there are no scores, so there is no stable top finding to contradict yet.
            return []
        chosen = [key for key in included if (latest.get(key) or {}).get("decision", "INCLUDE") == "INCLUDE"]
        chosen.sort(key=lambda key: (-significance_score(latest.get(key) or {}), not sources[key].get("red"),
                                     sources[key].get("tier") or 9, -(sources[key].get("corroboration") or 1), key))
        # Top findings are distinct stories: a second report of a story already chosen adds no new claim to challenge.
        top: list[str] = []
        for key in chosen:
            if len(top) >= TOP_FINDINGS:
                break
            if not any(self.same_story(key, other, sources) for other in top):
                top.append(key)
        return top

    def same_story(self, first: str, second: str, sources: Mapping[str, Mapping[str, Any]]) -> bool:
        """Two reports of one story: the same event cluster, or headlines that share STORY_TERMS distinctive words."""
        from grit.core.headlines import distinctive_terms
        from grit.pipeline.events import STORY_TERMS

        if first == second:
            return True
        events = {row["signal_id"]: row["event_id"] for row in self.pool.rows(
            "SELECT signal_id, event_id FROM event_members WHERE signal_id IN (?, ?)", (first, second))}
        if len(events) == 2 and events[first] == events[second]:
            return True
        words = [distinctive_terms(str((sources.get(key) or {}).get("title") or "")) for key in (first, second)]
        return len(words[0] & words[1]) >= STORY_TERMS

    def front_page_findings(self, sources: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Up to three in-window ranked findings with a material rung or a register id, for the status line and bottom line."""
        sources = sources if sources is not None else self.sources
        config = (self.pool.run(self.run_id) or {}).get("config") or {}
        order = [str(key) for key in (config.get("deepseek_rank") or []) if isinstance(key, str) and key in sources]
        if not order:
            return []
        editorials = {row["signal_id"]: row.get("editorial") or {}
                      for row in self.pool.weekly_assessments(self.run_id, include_excluded=True)}
        kept: list[dict[str, Any]] = []
        for key in order:
            row, editorial = sources[key], editorials.get(key) or {}
            if str(row.get("time_scope") or "IN_WINDOW") not in {"IN_WINDOW", ""}:
                continue
            rung = str(row.get("rung") or (row.get("link") or {}).get("rung") or "").upper()
            has_register = bool(row.get("condition_ids")) or bool(editorial.get("risk_ids"))
            if rung not in RANK_RUNGS and not has_register:
                continue
            from grit.report.format_v2 import clean_title

            kept.append({"signal_id": key, "title": clean_title(str(row.get("title") or key)), "rung": rung or None})
            if len(kept) >= 3:
                break
        return kept

    def status_policy(self) -> dict[str, Any]:
        """The report status from the reviewed selections (URS methodology section 14), separate from headline severity.
        RED: impact on the business confirmed by the Risk & Assurance operator. ELEVATED: identifiable exposure, credible
        evidence and near-term immediacy, a credible immediate threat, or an assessed impact awaiting R&A confirmation.
        A headline-pattern RED candidate alone raises nothing; it is listed for the R&A decision.
        WATCHPOINT: anything included or watched. NORMAL: nothing."""
        decisions = self.pool.red_decisions(str((self.pool.run(self.run_id) or {}).get("pack") or ""))
        rows = [dict(row.get("editorial") or {"decision": "INCLUDE"}) | {"signal_id": row["signal_id"], "red_candidate": bool(row.get("red")),
                 "title": (self.sources.get(row["signal_id"]) or {}).get("title")}
                for row in self.pool.weekly_assessments(self.run_id)]
        for row in rows:
            row["red_decision"] = (decisions.get(row["signal_id"]) or {}).get("decision")
        included = [row for row in rows if row["decision"] == "INCLUDE"]
        red = [row for row in rows if row["red_decision"] == "CONFIRMED"]
        awaiting = [row for row in included if row["red_decision"] is None and row.get("impact_status") == "CONFIRMED"]
        elevated = [row for row in included if row.get("immediate_threat") or (
            row.get("exposure_status") in {"CONFIRMED", "POSSIBLE"}
            and all(int((row.get("significance") or {}).get(key, 0)) >= 2 for key in ("exposure", "immediacy", "evidence")))]
        elevated += [row for row in awaiting if row not in elevated]
        candidates = sorted(row["signal_id"] for row in rows if row["red_candidate"])
        front = self.front_page_findings()
        if red:
            from grit.core.red_events import red_triggers

            labels = red_triggers()
            named = sorted({labels.get(str((decisions.get(row["signal_id"]) or {}).get("trigger_id")), "") for row in red} - {""})
            status, basis, ids = "RED", (f"The Risk & Assurance operator confirmed the impact of {len(red)} findings"
                                         + (f" ({'; '.join(named)})." if named else ".")), red
        elif front:
            titles = [str(item["title"] or item["signal_id"])[:80] for item in front]
            status, basis, ids = "ELEVATED", (
                "Lead findings: " + "; ".join(titles) + ". No impact on the business is confirmed."
            ), [{"signal_id": item["signal_id"]} for item in front]
        elif elevated:
            status, basis, ids = "ELEVATED", (f"{len(elevated)} included findings combine identifiable exposure, credible evidence and "
                                              "near-term timing, or a credible immediate threat; no impact is confirmed."), elevated
        elif rows:
            status, basis, ids = "WATCHPOINT", f"{len(rows)} findings are worth watching; none shows identifiable near-term exposure.", []
        else:
            status, basis, ids = "NORMAL", "No finding was included or watched this week.", []
        if awaiting and status != "RED":
            basis += f" {len(awaiting)} included findings with assessed impact await R&A confirmation."
        undecided = [key for key in candidates if key not in {row["signal_id"] for row in awaiting} and not decisions.get(key)]
        if undecided and status != "RED":
            basis += f" {len(undecided)} RED alert candidates matched a headline pattern and await the R&A decision; none has confirmed impact."
        rejected = [row for row in rows if row["red_decision"] == "NOT_CONFIRMED"]
        if rejected:
            basis += f" R&A did not confirm RED impact for {len(rejected)} findings."
        excluded = sorted(row["signal_id"] for row in self.pool.weekly_assessments(self.run_id, include_excluded=True)
                          if row.get("red") and (row.get("editorial") or {}).get("decision") == "EXCLUDE")
        if excluded:
            basis += f" The review excluded {len(excluded)} further RED alert candidates, so the report does not list them."
        ranked = sorted(included, key=lambda row: -significance_score(row))
        return {"status": status, "basis": basis, "signal_ids": sorted(row["signal_id"] for row in ids), "red_candidates": candidates,
                "red_awaiting_confirmation": sorted({row["signal_id"] for row in awaiting} | set(undecided)), "red_excluded": excluded,
                "red_decisions": {row["signal_id"]: row["red_decision"] for row in rows if row["red_decision"]},
                "front_page": front,
                "priorities": [{"signal_id": row["signal_id"], "score": significance_score(row),
                                "band": significance_band(significance_score(row), bool(row.get("immediate_threat")))} for row in ranked]}

    def require_decisions(self) -> None:
        """A DEFER is a question the review has not answered. It opens a material issue, so a revision round revisits the
        candidate. A candidate still deferred when the revision budget is spent keeps the reports INCOMPLETE, visibly."""
        issues = self.store.issues()
        latest = self.pool.rows(
            """SELECT s.signal_id, s.call_id, s.selection_json FROM weekly_selections s
               WHERE s.run_id = ? AND s.version = ? AND s.selection_id = (SELECT MAX(t.selection_id) FROM weekly_selections t
                 WHERE t.run_id = s.run_id AND t.version = s.version AND t.signal_id = s.signal_id)""",
            (self.run_id, self.store.state["version"]))
        for row in latest:
            issue_id, deferred = f"DEFER-{row['signal_id']}", row["selection"].get("decision") == "DEFER"
            if deferred and issue_id not in issues:
                self.store._issue(row["call_id"], issue_id, "OPEN", {
                    "issue_id": issue_id, "target": row["signal_id"], "kind": "COVERAGE", "material": True,
                    "source_ids": [row["signal_id"]], "missing_evidence": "An editorial decision",
                    "problem": f"The review deferred this candidate ({str(row['selection'].get('reason') or 'no reason given').strip()}). "
                               "Decide INCLUDE, WATCH or EXCLUDE."})
            elif not deferred and issue_id in issues:
                self.store._issue(row["call_id"], issue_id, "RESOLVED", {
                    **issues[issue_id]["issue"], "resolution": {"response": f"Decided: {row['selection'].get('decision')}."}})

    def settle_false_positives(self) -> None:
        """A FALSE_POSITIVE, UNSUPPORTED or WRONG_ATTRIBUTION objection is settled once the candidate's current selection
        excludes it, often by the same Critic call that raised it. An objection to a risk link is settled once the
        selection no longer carries any risk link the objection names."""
        selections = {row["signal_id"]: row["editorial"] for row in self.pool.weekly_assessments(self.run_id, include_excluded=True)}
        for issue_id, row in self.store.issues().items():
            issue, target = row["issue"], str(row["issue"].get("target"))
            editorial = selections.get(target)
            if editorial is None:
                continue
            named = set(RISK_ID.findall(str(issue.get("problem") or "")))
            kind = issue.get("kind")
            if kind in {"FALSE_POSITIVE", "UNSUPPORTED", "WRONG_ATTRIBUTION"} and editorial["decision"] == "EXCLUDE":
                response = "The candidate is excluded, so the challenged claim is not reported."
            elif kind in {"UNSUPPORTED", "WRONG_ATTRIBUTION"} and named and not named & set(editorial.get("risk_ids") or []):
                response = "The selection no longer carries the challenged risk link."
            else:
                continue
            self.store._issue(row["call_id"], issue_id, "RESOLVED", {**issue, "resolution": {"response": response, "source_ids": []}})

    def requalify_unrun_page_checks(self) -> None:
        """A page check the budget could not run was material before 5 November 2026; it is now a disclosed limitation."""
        for issue_id, row in self.store.issues().items():
            issue = row["issue"]
            if issue_id.startswith("PAGE-") and issue.get("material") and "budget" in str(issue.get("missing_evidence") or ""):
                self.store._issue(row["call_id"], issue_id, "OPEN", {**issue, "material": False, "problem": (
                    "Source-page verification was not run because the search budget was spent or disabled, "
                    "so the report relies on the retrieved text.")})

    def check_contradiction(self) -> None:
        """Review can change which findings rank top after the last research pass. Each current top finding still needs
        its contradiction search. An issue for a finding that left the top set is resolved. Settled false-positive
        objections are resolved first."""
        self.settle_false_positives()
        latest = self.pool.one(
            "SELECT MAX(call_id) AS call_id FROM weekly_calls WHERE run_id = ? AND version = ? AND role = 'Researcher' AND status = 'VALIDATED'",
            (self.run_id, self.store.state["version"]))
        if latest and latest.get("call_id"):
            sources = self.sources
            self.require_contradiction(latest["call_id"], self.top_findings(sources, self.included_ids(sources)))

    def ensure_top_findings_contradicted(self, *, search: Callable[[str], Mapping[str, Any]] | None,
                                         reassess: Callable[[], Any] | None) -> None:
        """After DeepSeek ranking, a newly promoted top finding still needs its contradiction search."""
        sources = self.sources
        missing = set(self.top_findings(sources, self.included_ids(sources))) - self.contradicted()
        if not missing:
            self.check_contradiction()
            return
        self.progress(f"  Ranking promoted {len(missing)} top finding(s) that still need a contradiction search")
        before = self.digests() if self.fast else {}
        if search is not None and reassess is not None and self.research("post-rank-contradiction", search, reassess):
            self.review(int(self.store.state["revisions_used"]) + 3, self.changed(before) if self.fast else None)
        self.require_decisions()
        self.check_contradiction()

    def contradicted(self) -> set[str]:
        """Findings with a completed contradiction search in this run."""
        return {str(key) for row in self.pool.rows(
            "SELECT detail_json FROM execution_log WHERE run_id = ? AND actor = 'WeeklyCountersearch' AND status = 'SUCCEEDED'", (self.run_id,))
            if (row["detail"].get("request") or {}).get("purpose") == "CONTRADICT"
            for key in (row["detail"].get("request") or {}).get("signal_ids") or []}

    def require_contradiction(self, call_id: int, top: Sequence[str]) -> None:
        """Each top finding needs a completed contradiction search. Without one, an issue stays open: material when the
        run had a search budget, a disclosed limitation when the operator set the budget to zero."""
        searched, issues = self.contradicted(), self.store.issues()
        sources = self.sources
        # A contradiction search on any report of a story covers the story.
        done = searched | {key for key in top if any(self.same_story(key, other, sources) for other in searched if other in sources)}
        enabled = int(self.store.state["search_limit"]) > 0
        # Material only while the run could still pay for the search, as for page checks and other searches: once the
        # allowance is spent, review can keep reshuffling near-tied findings, and the gap is disclosed instead.
        affordable = self.store.can_search_to_contradict()
        problem = ("No completed search tried to contradict this top finding." if affordable
                   else "The search allowance was spent before a search could try to contradict this top finding." if enabled
                   else "Countersearch is disabled for this run, so no search tried to contradict this top finding.")
        for key in top:
            issue_id = f"CONTRA-{key}"
            if key in done:
                if issue_id in issues:
                    self.store._issue(call_id, issue_id, "RESOLVED", {**issues[issue_id]["issue"], "resolution": {"response": "A contradiction search completed."}})
            elif issue_id not in issues or bool(issues[issue_id]["issue"].get("material")) != affordable:
                self.store._issue(call_id, issue_id, "OPEN", {
                    "issue_id": issue_id, "target": "RESEARCH", "kind": "COVERAGE", "material": affordable, "source_ids": [key],
                    "problem": problem, "missing_evidence": "Contradiction search"})
        for issue_id, row in issues.items():
            if issue_id.startswith("CONTRA-") and issue_id.removeprefix("CONTRA-") not in top and issue_id.removeprefix("CONTRA-") not in done:
                self.store._issue(call_id, issue_id, "RESOLVED", {**row["issue"], "resolution": {"response": "No longer a top finding."}})

    def prepare_report(self, brief: dict[str, Any], sources: Mapping[str, Mapping[str, Any]]) -> None:
        """Policy fields set before Reporting sees the draft; report recovery repeats this to compare inputs."""
        self.disclose_contradiction(brief, sources)
        self.disclose_unverified_pages(brief)
        self.disclose_unrun_searches(brief)
        policy = self.status_policy()
        if isinstance(brief.get("v2"), dict):
            brief["v2"]["status"], brief["v2"]["status_basis"] = policy["status"], policy
            front = policy.get("front_page") or []
            if front:
                from grit.pipeline.events import UP

                brief["v2"]["arrows"] = [f"{item['title'][:80]} {UP}" for item in front]
        if isinstance(brief.get("bottom_line"), dict):
            brief["bottom_line"]["label"] = policy["status"]

    def disclose_unverified_pages(self, brief: dict[str, Any]) -> None:
        """A requested page check the source refused, or the budget could not run, is a limitation of the report, not a blocker."""
        completeness = (brief.get("v2") or {}).get("completeness")
        open_pages = [row for issue_id, row in self.store.issues().items()
                      if issue_id.startswith("PAGE-") and row["status"] == "OPEN" and not row["issue"].get("material")]
        refused = sorted(row["issue"]["target"] for row in open_pages if "budget" not in row["issue"]["problem"])
        unrun = sorted(row["issue"]["target"] for row in open_pages if "budget" in row["issue"]["problem"])
        if isinstance(completeness, dict) and refused:
            completeness.setdefault("limitations", []).append(
                f"{len(refused)} requested source-page checks could not be completed because the source page could not be retrieved: "
                + ", ".join(refused) + ". These findings rely on the retrieved text.")
        if isinstance(completeness, dict) and unrun:
            completeness.setdefault("limitations", []).append(
                f"{len(unrun)} requested source-page checks were not run because the search budget was spent: "
                + ", ".join(unrun) + ". These findings rely on the retrieved text.")

    def disclose_unrun_searches(self, brief: dict[str, Any]) -> None:
        """Countersearches the budget could not run are a limitation of the report, not a blocker."""
        completeness = (brief.get("v2") or {}).get("completeness")
        unrun = sorted(row["issue"]["problem"].split(": ", 1)[-1] for issue_id, row in self.store.issues().items()
                       if issue_id.startswith("SEARCH-") and row["status"] == "OPEN" and not row["issue"].get("material"))
        if isinstance(completeness, dict) and unrun:
            completeness.setdefault("limitations", []).append(
                f"{len(unrun)} requested countersearches were not run because the search budget was spent: "
                + "; ".join(unrun) + ".")

    def disclose_contradiction(self, brief: dict[str, Any], sources: Mapping[str, Mapping[str, Any]]) -> None:
        """A gate row states how many top findings a contradiction search challenged. It is not critical: when search
        was available, the open CONTRA issue already blocks readiness."""
        gate = (brief.get("v2") or {}).get("gate")
        if not isinstance(gate, list):
            return
        top = self.top_findings(sources, self.included_ids(sources))
        searched = self.contradicted()
        checked = [key for key in top if key in searched or any(self.same_story(key, other, sources) for other in searched if other in sources)]
        answer = "Yes" if len(checked) == len(top) else "No" if not checked else "Partial"
        note = (f"{len(checked)} of {len(top)} top findings were searched for denials, corrections or reversals."
                if top else "No finding was included, so none needed a contradiction search.")
        if int(self.store.state["search_limit"]) <= 0 and len(checked) < len(top):
            note += " Countersearch was disabled for this run."
        elif len(checked) < len(top) and not self.store.can_search_to_contradict():
            note += " The search allowance was spent before the rest could be searched."
        gate.append({"question": "Were the top findings challenged by a contradiction search?", "answer": answer if top else "Yes",
                     "note": note, "critical": False})
        completeness = brief["v2"].get("completeness")
        if isinstance(completeness, dict) and answer != "Yes" and top:
            completeness.setdefault("limitations", []).append(f"{gate[-1]['question']} {answer}: {note}")

    def assess_and_critique(self, round_number: int, only: set[str] | None = None) -> None:
        sources = self.sources
        keys = sorted(sources) if only is None else sorted(set(sources) & only)
        for offset in range(0, len(keys), 16):
            batch_keys = keys[offset:offset + 16]
            batch = {key: full_view(sources[key]) for key in batch_keys}
            related = self.related(batch_keys, sources)
            selections, assessment_id = self.invoke("Assessment", f"pool-{round_number}-{offset}", {
                "candidates": batch, "sources": related, "open_issues": self.batch_issues(batch_keys, sources), "admitted_corrections": memory_context(self.pool, self.run_id),
            }, lambda raw: check_selections(raw, sources, set(batch), register=self.register))
            self.store.select(assessment_id, selections["selections"])
            critique, critic_id = self.invoke("Critic", f"pool-{round_number}-{offset}", {
                "candidates": batch, "sources": related, "assessment": selections,
                "open_issues": self.batch_issues(batch_keys, sources), "admitted_corrections": memory_context(self.pool, self.run_id),
            }, lambda raw: (check_critique(raw, set(batch), sources, set(self.store.issues())),
                            check_selections(raw, sources, set(batch), register=self.register)))
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
               selections: bool = True, chain: bool = True) -> Callable[[Any], Any]:
        """A thread-safe critique check against a snapshot of the open issues."""
        register = self.register  # read on the caller thread, because workers must not touch the pool
        def validate(raw: Any) -> None:
            if not selections and isinstance(raw, dict) and "selections" in raw:
                # Selections are not this task's output. An unrequested list is set aside instead of failing the run.
                extra = raw.pop("selections")
                raw["ignored_selections"] = len(extra) if isinstance(extra, list) else 1
            check_critique(raw, members, sources, set(open_issues))
            outside = [key for key in raw["resolved_issue_ids"] if str(open_issues[key]["issue"].get("target")) not in members]
            if outside and isinstance(raw, dict):
                # Only a batch that reviewed an issue's target can resolve it. The attempt is kept for audit.
                raw["resolved_issue_ids"] = [key for key in raw["resolved_issue_ids"] if key not in outside]
                raw["resolutions"] = [item for item in raw.get("resolutions", []) if item.get("issue_id") not in outside]
                raw["ignored_resolutions"] = outside
            if selections:
                check_selections(raw, sources, members, chain=chain, register=register)
        return validate

    def screen(self, round_number: int, keys: Sequence[str], sources: Mapping[str, Any]) -> set[str]:
        """The Critic checks deterministic drops from compact records; any decision except EXCLUDE escalates."""
        open_issues = self.store.issues()
        corrections = memory_context(self.pool, self.run_id)
        jobs = []
        for batch in stable_batches(keys, SCREEN_BATCH, SCREEN_BATCH_MAX):
            members = set(batch)
            jobs.append(("Critic", f"screen-{round_number}-{batch[0]}", {
                "candidates": {key: screen_view(sources[key]) for key in batch},
                "open_issues": {key: row for key, row in open_issues.items() if str(row["issue"].get("target")) in members},
                "admitted_corrections": corrections,
            }, self.scoped(members, sources, open_issues, chain=False), "Screen"))
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
        register = self.register  # snapshot before workers run
        inputs = [{"candidates": {key: full_view(sources[key]) for key in batch},
                   "sources": self.related(batch, sources, included), "open_issues": self.batch_issues(batch, sources),
                   "admitted_corrections": corrections} for batch in batches]
        assessments = self.invoke_many([
            ("Assessment", f"pool-{round_number}-{batch[0]}", payload,
             (lambda members: lambda raw: check_selections(raw, sources, members, register=register))(set(batch)), None)
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
            # Fast review re-examines only signals whose evidence changed. Full review repeats the pass.
            self.review(1, self.changed(before) if self.fast else None)
        sources = self.sources
        if set(self.top_findings(sources, self.included_ids(sources))) - self.contradicted():
            # Review scores can change which findings rank top. Each new top finding still needs its contradiction search.
            before = self.digests() if self.fast else {}
            if self.research("contradiction", search, reassess):
                self.review(2, self.changed(before) if self.fast else None)
        self.require_decisions()
        self.check_contradiction()
        while any(row["issue"]["material"] for row in self.store.issues().values()) and self.store.reserve("revision"):
            round_number = self.store.state["revisions_used"] + 1
            issues = self.store.issues()
            # Only research can close a research issue, such as a top finding without a contradiction search.
            research_needed = any(row["issue"]["material"] and row["issue"].get("target") == "RESEARCH" for row in issues.values())
            action = self.supervise(["RESEARCH"] if research_needed else ["RESEARCH", "REASSESS"],
                                    {"stage": "pool reconciliation", "issues": issues})
            before = self.digests() if self.fast else {}
            added = action == "RESEARCH" and self.research(f"counterevidence-{round_number}", search, reassess)
            # Revision rounds revisit only challenged candidates and any newly acquired evidence.
            sources = self.sources
            targets = {str(row["issue"].get("target")) for row in self.store.issues().values() if row["issue"].get("material")}
            unselected = set(sources) - {row["signal_id"] for row in self.pool.rows(
                "SELECT DISTINCT signal_id FROM weekly_selections WHERE run_id = ? AND version = ?", (self.run_id, self.store.state["version"]))}
            if added and not self.fast:
                self.assess_and_critique(round_number)
                self.require_decisions()
                self.check_contradiction()
                continue
            scope = (targets | unselected | (self.changed(before) if added else set())) & set(sources)
            if scope:
                # Fast round 1 is the post-research pass, so its revision rounds are numbered from 2.
                self.review(round_number + 1 if self.fast else round_number, scope)
            self.require_decisions()
            self.check_contradiction()

    def _red_triggers(self) -> list[str]:
        """RED trigger labels this pack may name in the recommended action."""
        from grit.settings import CONFIG_DIR

        run = self.pool.run(self.run_id) or {}
        pack = str(run.get("pack") or "")
        path = CONFIG_DIR / "red-patterns.json"
        if not path.is_file():
            return []
        try:
            patterns = json.loads(path.read_text(encoding="utf-8")).get("patterns") or []
        except (OSError, json.JSONDecodeError):
            return []
        return [str(row.get("label") or row.get("ruleId")) for row in patterns
                if isinstance(row, Mapping) and pack in (row.get("packs") or []) and (row.get("label") or row.get("ruleId"))]

    def report_payload(self, profile_id: str, brief: Mapping[str, Any],
                       sources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        conflicts = ((brief.get("v2") or {}).get("conflicts") or [])
        front = self.front_page_findings(sources)
        return {
            "report": model_brief(brief), "editable_fields": narrative_fields(brief),
            "sources": self.report_sources(brief, sources), "open_issues": self.store.issues(), "budget": self.store.state,
            "reader_memory": memory_context(self.pool, self.run_id, profile_id),
            "settled_conflicts": [{"item": row.get("item"), "chosen": row.get("chosen"),
                                   "rejected": list(row.get("rejected") or []), "treatment": row.get("treatment")}
                                  for row in conflicts if isinstance(row, Mapping)],
            "red_triggers": self._red_triggers(),
            "front_page": front,
            "owner_roles": list(self._weekly_context().get("owner_roles") or []),
            "human_feedback": [row["membership"].get("review") for row in self.pool.rows(
                "SELECT * FROM packages WHERE run_id = ? AND profile_id = ? AND status IN ('RETURNED_FOR_REWORK', 'REJECTED') ORDER BY version",
                (self.run_id, profile_id))],
        }

    def report(self, profile: Any, draft: Mapping[str, Any], routed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Write, critique and decide one report. Several reports are decided together, after every final Critic."""
        brief, critic_id = self.write_report(profile, draft)
        return self.decide_report(profile, brief, routed, critic_id)

    def _report_response(self, task: str, payload: Mapping[str, Any], validate: Callable[[Any], Any]) -> tuple[dict[str, Any], int]:
        """DeepSeek drafts the narrative when the flag is on. A failed draft falls back to the Gemini call."""
        if self.reasoner == "deepseek" and self.harness is not None:
            found = self._harness_invoke("Reporting", task, payload, validate)
            if found is not None:
                return found
            self.progress(f"  DeepSeek report draft skipped. Gemini will draft {task}.")
        return self.invoke("Reporting", task, dict(payload), validate)

    def _supersede_report_issues(self, target: str, call_id: int | None = None) -> None:
        """A new draft replaces earlier objections to this report; the next Critic may raise only what still stands."""
        for key, row in self.store.issues().items():
            if str(row["issue"].get("target") or "") != target:
                continue
            self.store._issue(call_id or row["call_id"], key, "RESOLVED", {**row["issue"], "resolution": {
                "response": "Superseded: a new report draft replaces this objection; the next Critic reviews the new text.",
                "source_ids": []}})

    def write_report(self, profile: Any, draft: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        brief = copy.deepcopy(dict(draft))
        sources = self.sources
        self.prepare_report(brief, sources)
        task = f"report-{profile.profile_id}"
        target = f"REPORT:{profile.profile_id}"
        rewrites = 0
        self._supersede_report_issues(target)
        self.progress(f"  Report {profile.profile_id}: Reporting is writing the narrative")
        from grit.core.corrections import retired_in

        ledger = brief.get("corrections") or []
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
                posture = (self._weekly_context().get("action_posture") or {}) if isinstance(self._weekly_context(), Mapping) else {}
                forum = str(posture.get("next_forum") or "").strip()
                forum_date = str(posture.get("next_forum_date") or "").strip()
                triggers = payload.get("red_triggers") or []
                owners = [str(role) for role in (payload.get("owner_roles") or []) if isinstance(role, str) and role.strip()]
                front_ids = {str(item["signal_id"]) for item in (payload.get("front_page") or []) if isinstance(item, Mapping)}
                for edit in edits:
                    if edit.get("path") not in editable or not isinstance(edit.get("text"), str) or not edit["text"].strip():
                        raise ContractError("Reporting attempted an unknown narrative edit.")
                    retired = retired_in(edit["text"], ledger)
                    if retired:
                        raise ContractError("; ".join(f"{edit['path']} uses \"{row['phrase']}\", which correction {row['id']} retired: "
                                                      f"write the corrected fact instead ({row['corrected']})" for row in retired) + ".")
                    refs = source_ids(edit.get("source_ids"), sources)
                    marks = {ref.strip() for group in CITE.findall(edit["text"]) for ref in group.split(",")}
                    if set(refs) != marks or (sources and not refs):
                        raise ContractError("Every narrative edit needs matching evidence citations.")
                    if refs:
                        last = 0
                        for mark in CITE.finditer(edit["text"]):
                            claim = edit["text"][last:mark.start()].strip(" .!?")
                            fragment = sentence_break(claim)
                            if fragment:
                                raise ContractError("Cite each factual sentence, not an entire paragraph: a sentence ends "
                                                    f"inside '{fragment}' before its citation. Put a citation before each "
                                                    "sentence's final punctuation.")
                            last = mark.end()
                        if edit["text"][last:].strip(" .!?\n"):
                            raise ContractError("Uncited narrative after the final reference.")
                    # A cited source's title is part of its evidence, as it is for quotes.
                    cited = " ".join(f"{sources[ref].get('title') or ''} {sources[ref].get('text') or ''}" for ref in refs)
                    problems = text_problems(CITE.sub("", edit["text"]), cited, "report edit")
                    if problems:
                        raise ContractError("; ".join(problems))
                    cite_issues = sentence_cite_problems(edit["text"], sources, edit["path"])
                    if cite_issues:
                        raise ContractError("; ".join(cite_issues))
                    if edit.get("path") == "bottom_line.text" and front_ids and set(refs) - front_ids:
                        raise ContractError("bottom_line.text must cite only the front-page findings when a DeepSeek rank is stored.")
                    if edit.get("path") == "bottom_line.escalation":
                        ask_problems = escalation_problems(edit["text"], owners=owners, forum=forum, forum_date=forum_date,
                                                           triggers=triggers)
                        if ask_problems:
                            raise ContractError("; ".join(ask_problems))
                    for conflict in payload.get("settled_conflicts") or []:
                        if not isinstance(conflict, Mapping):
                            continue
                        for rejected in conflict.get("rejected") or []:
                            if isinstance(rejected, str) and rejected and rejected.casefold() in edit["text"].casefold():
                                raise ContractError(
                                    f"{edit['path']} quotes {rejected}; use the settled figure {conflict.get('chosen')} instead.")
            response, _ = self._report_response(task, payload, validate_edits)
            for edit in response["edits"]:
                apply_edit(brief, edit, sources)
            brief["evidence_ids"] = sorted(set(brief.get("evidence_ids", [])) | set(brief["membership"]))
            selected = {row["signal_id"] for row in self.pool.weekly_assessments(self.run_id)}
            brief["membership"] = sorted(set(brief["membership"]) & selected)
            label_date_exceptions(brief, sources)
            brief["bottom_line"]["drafted_by"] = "Reporting; independently critiqued before readiness"
            brief["method"]["weekly"] = {"contract": VERSION, "budget": self.store.state,
                "issues": list(self.store.issues().values()), "selections": selection_summary(self.pool.weekly_assessments(self.run_id, include_excluded=True)),
                "evidence_scope": "stored excerpts; full-page and internal checks are not implied",
                "completeness": {"blocking": completeness_blockers(brief),
                                 "limitations": list(((brief.get("v2") or {}).get("completeness") or {}).get("limitations") or [])}}
            brief["content_hash"] = report_hash(brief)
            open_issues = self.store.issues()
            self.progress(f"  Report {profile.profile_id}: the final Critic is reviewing it")
            critique, critic_id = self.invoke("Critic", f"final-{profile.profile_id}", {
                "report": model_brief(brief), "report_hash": brief["content_hash"], "targets": [target],
                "sources": self.report_sources(brief, sources), "open_issues": open_issues,
                "fixed_values": fixed_values(brief),
                # A rewrite after a material objection must not reuse a cached Critic answer for the same prose.
                "rewrite_pass": rewrites,
            }, self.scoped({target}, sources, open_issues, selections=False))
            self.store.record_critique(critic_id, critique)
            # Each report has its own rewrite: pool revision rounds must not use up the chance to fix a report objection.
            own = any(row["issue"]["material"] and row["issue"].get("target") == target for row in self.store.issues().values())
            if not own or rewrites >= REPORT_REWRITES:
                break
            rewrites += 1
            self.progress(f"  Report {profile.profile_id}: rewriting after a material objection")
            self.supervise(["REVISE_REPORT"], {"profile": profile.profile_id, "issues": self.store.issues()})
            self._supersede_report_issues(target, critic_id)
        return brief, critic_id

    def decide_report(self, profile: Any, brief: dict[str, Any], routed: Sequence[Mapping[str, Any]], critic_id: int) -> dict[str, Any]:
        """Readiness from the issues as they stand now. Any open material issue blocks every report, so reports are
        decided after all of them are critiqued: a later final Critic can resolve an objection to an earlier report."""
        sources = self.sources
        unreviewed = sorted(set(brief["evidence_ids"]) - set(sources) - scenario_history(self.pool, self.run_id, brief))
        # Every reason is stated, so an INCOMPLETE report always says what keeps it from READY.
        blocked_by = [
            *(f"Completeness: {item}" for item in brief["method"]["weekly"]["completeness"]["blocking"]),
            *(f"Open material issue {key}: {str(row['issue'].get('problem') or '')[:200]}"
              for key, row in self.store.issues().items() if row["issue"].get("material")),
            *(["No reviewed sources."] if not sources else []),
            *(["The evidence changed after the review."] if not self.store.current() else []),
            *([f"Cites signals the review did not cover: {', '.join(unreviewed)}."] if unreviewed else []),
            *(f"Deterministic assessment missing: {key}." for key, row in sources.items() if row.get("assessment_missing")),
            *(f"Deferred candidate: {row['signal_id']}." for row in self.pool.weekly_assessments(self.run_id, include_excluded=True)
              if row["editorial"]["decision"] == "DEFER"),
        ]
        status = "INCOMPLETE" if blocked_by else "READY"
        self.progress(f"  Report {profile.profile_id}: {status}" + (f", {len(blocked_by)} blockers" if blocked_by else ""))
        self.pool.insert("weekly_reports", {
            "run_id": self.run_id, "version": self.store.state["version"], "profile_id": profile.profile_id,
            "content_hash": brief["content_hash"], "selection_hash": selection_hash(self.pool, self.run_id),
            "critique_call_id": critic_id, "status": status,
            "brief_json": brief, "routed_json": list(routed), "created_at": utc_now(),
        })
        return {"profile_id": profile.profile_id, "status": status, "content_hash": brief["content_hash"], "blocked_by": blocked_by}


# A period after an initial or one of these words ends an abbreviation, not a sentence: "U.S. State", "J. Smith", "Pte. Ltd".
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "st", "no", "nos", "vs", "approx", "inc", "ltd", "co", "corp", "pte", "bhd", "plc",
    "jr", "sr", "gen", "lt", "col", "gov", "sen", "rep", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
})


def sentence_break(text: str) -> str | None:
    """The text around the first sentence break, or None when the text is one sentence."""
    for match in re.finditer(r"[.!?]\s+[A-Z]", text):
        if text[match.start()] == ".":
            token = re.search(r"(\S*)$", text[:match.start()]).group(1).lstrip("([\"'\u2018\u201c")
            if re.fullmatch(r"(?:[A-Za-z]\.)*[A-Za-z]", token) or token.casefold() in ABBREVIATIONS:
                continue
        return text[max(0, match.start() - 40):match.end() + 20]
    return None


def report_hash(brief: Mapping[str, Any]) -> str:
    return canonical_hash({key: value for key, value in brief.items() if key != "content_hash"})


def significance_score(selection: Mapping[str, Any]) -> int:
    score = selection.get("significance")
    return sum(int(value) for value in score.values()) if isinstance(score, Mapping) else 0


def completeness_blockers(brief: Mapping[str, Any]) -> list[str]:
    """Critical completeness-gate questions answered No. Any one keeps the report from READY, whatever the Critic said."""
    v2 = brief.get("v2") or {}
    if isinstance(v2.get("completeness"), Mapping):
        return list(v2["completeness"].get("blocking") or [])
    return [str(row.get("question")) for row in v2.get("gate") or [] if row.get("critical") and row.get("answer") == "No"]


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
                    if key not in {"method", "meta", "order_book", "red", "membership", "row", "qa", "scenarios", "corrections"}:
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


def coordinator_for(store: WeeklyStore, *, client: Any, model: str, call: RoleCall = call_json,
                    fast: bool = False, workers: int = 1, progress: Callable[[str], Any] | None = None) -> WeeklyCoordinator:
    """A coordinator for this run. A stored reasoner of deepseek attaches the harness. Gemini stays the default."""
    config = (store.pool.run(store.run_id) or {}).get("config") or {}
    reasoner = config.get("reasoner") if config.get("reasoner") == "deepseek" else None
    harness = None
    if reasoner == "deepseek":
        from grit.agents.harness import deepseek_harness
        harness = deepseek_harness
    return WeeklyCoordinator(store, client=client, model=model, call=call, fast=fast, workers=workers, progress=progress,
                             reasoner=reasoner, harness=harness)


def _selection_reviewed(row: Mapping[str, Any], checkpoint_id: int) -> bool:
    """A latest selection may come from the Critic or from a DeepSeek task that ran before the report checkpoint."""
    if row["status"] != "VALIDATED" or row["call_id"] >= checkpoint_id:
        return False
    task = str(row["task"])
    if task.startswith(("pool-", "screen-")) and row["role"] == "Critic":
        return True
    return task.startswith(("meaning-", "cascade-", "rank-")) and row["role"] == "Assessment"


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
    coordinator = coordinator_for(store, client=client, model=model, call=call, fast=fast,
                                  workers=int(config.get("agent_workers") or DEFAULT_WORKERS) if fast else 1, progress=progress)
    coordinator.reconcile_pool(search=search, reassess=reassess)
    coordinator.reason_with_harness()
    # DeepSeek ranking can promote a new top finding after the contradiction budget was spent in reconcile.
    coordinator.ensure_top_findings_contradicted(search=search, reassess=reassess)
    coordinator.supervise(["DRAFT_REPORT"], {"stage": "pool reconciled", "issues": store.issues()})
    draft_sections()
    return _finish_weekly_reports(pool, run, profiles, coordinator)


def _finish_weekly_reports(pool: Pool, run: Any, profiles: Sequence[Any], coordinator: WeeklyCoordinator) -> dict[str, Any]:
    from grit.report.brief import build_brief, routed_signals
    written = [(profile, routed_signals(pool, run.run_id, profile), *coordinator.write_report(profile, build_brief(pool, run.run_id, profile)))
               for profile in profiles if profile.brief_for(run.pack)]
    results = [coordinator.decide_report(profile, brief, routed, critic_id) for profile, routed, brief, critic_id in written]
    ready = bool(results) and all(result["status"] == "READY" for result in results)
    coordinator.store.finish("READY" if ready else "INCOMPLETE")
    pool.finish_run(run.run_id, "COMPLETE" if ready else "INCOMPLETE", notes="Weekly reasoning: " + coordinator.store.state["status"])
    return {"status": coordinator.store.state["status"], "reports": results, "budget": coordinator.store.state}


RECOVERY_PASSES = 3
# A register risk or condition ID, such as STE-GC-08 or URS-TIER_2-07.
RISK_ID = re.compile(r"\b[A-Z]{2,5}-(?:[A-Z_0-9]+-)?\d{2}\b")


def resume_weekly_research(pool: Pool, run: Any, *, client: Any, model: str | None, profiles: Sequence[Any],
                           search: Callable[[str], Mapping[str, Any]], reassess: Callable[[], Any], call: RoleCall = call_json,
                           progress: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Finish an analysis that stopped INCOMPLETE only on research issues, such as a top finding without a contradiction
    search: up to RECOVERY_PASSES research passes, each reviewing only the new evidence, then every report again. The drafted sections are
    kept, so the section agents do not run again."""
    state = pool.one("SELECT * FROM weekly_runs WHERE run_id = ?", (run.run_id,))
    saved_run = pool.run(run.run_id)
    if not state or not saved_run or (saved_run.get("config") or {}).get("weekly_mode") != "reasoned":
        raise ValueError(f"No reasoned weekly analysis exists for {run.run_id} in this database.")
    if state["status"] != "INCOMPLETE":
        raise ValueError("Research recovery requires an incomplete weekly analysis.")
    if state["evidence_hash"] != analysis_hash(pool, run.run_id):
        raise ValueError("Weekly evidence changed after the saved review. Run the weekly again instead.")
    last = pool.one("SELECT * FROM weekly_calls WHERE run_id = ? AND version = ? ORDER BY call_id DESC LIMIT 1", (run.run_id, state["version"]))
    if not last or last["role"] != "Critic" or last["status"] != "VALIDATED" or not last["task"].startswith("final-"):
        raise ValueError("Research recovery requires a review whose report stage finished.")
    store = WeeklyStore(pool, run.run_id, search_limit=state["search_limit"], refresh=False)
    config = saved_run.get("config") or {}
    fast = config.get("weekly_review") == "fast"
    coordinator = coordinator_for(store, client=client, model=model or last["model"], call=call, fast=fast,
                                  workers=int(config.get("agent_workers") or DEFAULT_WORKERS) if fast else 1, progress=progress)
    # Rule-settled objections first, so only live issues decide whether research alone can finish the analysis.
    coordinator.settle_false_positives()
    coordinator.requalify_unrun_page_checks()
    material = {key: row for key, row in store.issues().items() if row["issue"].get("material")}
    other = sorted(key for key, row in material.items() if row["issue"].get("target") != "RESEARCH" and not str(row["issue"].get("target") or "").startswith("REPORT:"))
    if not any(row["issue"].get("target") == "RESEARCH" for row in material.values()) or other:
        raise ValueError("Research recovery is for an analysis blocked only by research issues"
                         + (f"; these other material issues are open: {', '.join(other)}." if other else "; none is open."))
    coordinator.progress("Completing the missing research; only new evidence is reviewed and the drafted sections are kept.")
    store.finish("STARTED")
    with pool.transaction() as conn:
        conn.execute("UPDATE runs SET status = 'STARTED', finished_at = NULL WHERE run_id = ?", (run.run_id,))
    # Reviewing new evidence can move another finding into the top set, which then needs its own search.
    for attempt in range(1, RECOVERY_PASSES + 1):
        before = coordinator.digests() if fast else {}
        if coordinator.research(f"research-recovery-{attempt}", search, reassess):
            coordinator.review(int(store.state["revisions_used"]) + 2 + attempt, coordinator.changed(before) if fast else None)
        coordinator.require_decisions()
        coordinator.check_contradiction()
        if not any(row["issue"].get("material") and row["issue"].get("target") == "RESEARCH" for row in store.issues().values()):
            break
    # The report stage resolves objections to the old drafts, as a refresh does: every report is critiqued again.
    for key, row in store.issues().items():
        if str(row["issue"].get("target") or "").startswith("REPORT:"):
            store._issue(last["call_id"], key, "RESOLVED", {**row["issue"], "resolution": {
                "response": "Superseded: the recovery rebuilds every report, and each new report gets its own final critique.", "source_ids": []}})
    coordinator.supervise(["DRAFT_REPORT"], {"stage": "pool reconciled", "issues": store.issues()})
    return _finish_weekly_reports(pool, run, profiles, coordinator)


def resume_weekly_reporting(pool: Pool, run: Any, *, client: Any, model: str | None,
                            profiles: Sequence[Any], call: RoleCall = call_json,
                            progress: Callable[[str], Any] | None = None, refresh: bool = False) -> dict[str, Any]:
    """Recover a failed Reporting call, or with refresh, rebuild every report from a finished review whose evidence has
    not changed, for example after an R&A RED decision. Neither runs search or signal review."""
    state = pool.one("SELECT * FROM weekly_runs WHERE run_id = ?", (run.run_id,))
    saved_run = pool.run(run.run_id)
    if not state or not saved_run or (saved_run.get("config") or {}).get("weekly_mode") != "reasoned":
        raise ValueError(f"No reasoned weekly analysis exists for {run.run_id} in this database. Run "
                         "`grit weekly --reasoned` for this date first; report recovery and refresh rebuild the reports of an existing one.")
    if state["status"] not in ({"INCOMPLETE", "READY"} if refresh else {"INCOMPLETE"}):
        raise ValueError("Report refresh requires a finished weekly analysis." if refresh
                         else "Report recovery requires an incomplete weekly analysis.")
    if state["evidence_hash"] != analysis_hash(pool, run.run_id):
        raise ValueError("Weekly evidence changed after the saved review. Report recovery cannot skip reassessment.")
    failed = pool.one("SELECT * FROM weekly_calls WHERE run_id = ? AND version = ? ORDER BY call_id DESC LIMIT 1",
                      (run.run_id, state["version"]))
    final_critic_failed = bool(failed) and failed["role"] == "Critic" and failed["status"] == "FAILED" and failed["task"].startswith("final-")
    if refresh:
        finished = bool(failed) and failed["role"] == "Critic" and failed["status"] == "VALIDATED" and failed["task"].startswith("final-")
        stopped = final_critic_failed or (bool(failed) and failed["role"] == "Reporting" and failed["status"] == "FAILED" and failed["task"].startswith("report-"))
        if not (finished or stopped):
            raise ValueError("Report refresh requires a review whose report stage finished, or stopped on a Reporting or final Critic call.")
    elif not failed or failed["role"] != "Reporting" or failed["status"] != "FAILED" or not failed["task"].startswith("report-"):
        raise ValueError("Report recovery requires the latest weekly call to be a failed Reporting call."
                         + (" The report stage stopped on a final Critic call: rebuild the reports with `grit resume-report --refresh`."
                            if final_critic_failed else ""))
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
            not _selection_reviewed(row, checkpoint["call_id"]) for row in latest):
        raise ValueError("The latest selection for every candidate must have a validated Critic before the report checkpoint.")
    if refresh:
        store = WeeklyStore(pool, run.run_id, search_limit=state["search_limit"], refresh=False)
        config = saved_run.get("config") or {}
        fast = config.get("weekly_review") == "fast"
        coordinator = coordinator_for(store, client=client, model=model or failed["model"], call=call, fast=fast,
                                      workers=int(config.get("agent_workers") or DEFAULT_WORKERS) if fast else 1, progress=progress)
        coordinator.progress("Refreshing every report from the saved review; no search or signal review will run.")
        # Rule-based issue settlement that the review stage now applies, so a refresh uses the current rules.
        coordinator.settle_false_positives()
        coordinator.requalify_unrun_page_checks()
        coordinator.check_contradiction()
        # An objection to an earlier draft of a report no longer applies: every report is rebuilt and critiqued again.
        for key, row in store.issues().items():
            if str(row["issue"].get("target") or "").startswith("REPORT:"):
                store._issue(failed["call_id"], key, "RESOLVED", {**row["issue"], "resolution": {
                    "response": "Superseded: the refresh rebuilds every report, and each new report gets its own final critique.", "source_ids": []}})
        store.finish("STARTED")
        with pool.transaction() as conn:
            conn.execute("UPDATE runs SET status = 'STARTED', finished_at = NULL WHERE run_id = ?", (run.run_id,))
        return _finish_weekly_reports(pool, run, profiles, coordinator)
    profile_id = failed["task"][len("report-"):]
    anchor = next((profile for profile in profiles if profile.profile_id == profile_id and profile.brief_for(run.pack)), None)
    if anchor is None:
        raise ValueError("Report recovery requires the profile whose Reporting call failed.")
    store = WeeklyStore(pool, run.run_id, search_limit=state["search_limit"], refresh=False)
    config = saved_run.get("config") or {}
    fast = config.get("weekly_review") == "fast"
    coordinator = coordinator_for(store, client=client, model=model or failed["model"], call=call, fast=fast,
                                  workers=int(config.get("agent_workers") or DEFAULT_WORKERS) if fast else 1, progress=progress)
    from grit.report.brief import build_brief
    draft = build_brief(pool, run.run_id, anchor)
    coordinator.prepare_report(draft, coordinator.sources)
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
    """Admitted Memory corrections, the corrections ledger, and accepted review misses."""
    from grit.agents.harness import as_miss
    from grit.core.corrections import as_admitted

    run = pool.run(run_id)
    records = pool.rows(
        """SELECT m.* FROM weekly_memory m JOIN weekly_memory_admissions a ON a.memory_id = m.memory_id
           JOIN packages p ON p.package_id = m.package_id AND p.version = m.package_version
           JOIN runs r ON r.run_id = p.run_id WHERE a.intent = 'ADMIT' AND r.pack = ?""", (run["pack"],))
    admitted = [row["proposal"] for row in records if row["proposal"]["kind"] == "CORRECTION" or (
        profile_id is not None and row["proposal"].get("profile_id") == profile_id)]
    return admitted + [as_admitted(row) for row in pool.corrections(run["pack"])] + [as_miss(row) for row in pool.misses(run["pack"])]


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
        raise ValueError("A memory admission is immutable. Propose a new correction instead.")
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
        exposure = row.get("exposure")
        if exposure is not None and (not isinstance(exposure, dict) or exposure.get("type") not in EXPOSURE_TYPES
                                     or exposure.get("status") not in EXPOSURE_STATUSES
                                     or not isinstance(exposure.get("entity"), str) or not exposure["entity"].strip()):
            raise ValueError("An internal exposure needs a type (" + ", ".join(sorted(EXPOSURE_TYPES)) + "), a status ("
                             + ", ".join(sorted(EXPOSURE_STATUSES)) + ") and the entity it concerns.")
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
                                    "approved_by": approver.profile_id, "approved_at": utc_now(),
                                    **({"exposure": dict(row["exposure"])} if row.get("exposure") else {})}, "created_at": utc_now()})
        ids.append(signal_id)
    return ids


def countersearch(query: str) -> dict[str, Any]:
    """One budgeted Brave call. Unlike the legacy helper, it keeps failures and empty results separate."""
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
