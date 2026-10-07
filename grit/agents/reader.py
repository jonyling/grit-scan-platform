"""The reader-scan agents: Assessor, Critic, and Reporter. They propose text. Checks decide what is valid.

Each function makes at most one retry with the list of violations. A draft that still fails is withheld, never patched.
Every call returns a log entry with the prompt version and the response hash, which the pipeline writes to the pool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from grit.acquisition.brave_plan import without_navigation
from grit.agents.checks import haystack, marked_ids, quote_problems, source_problems, strip_marks, text_problems
from grit.agents.loop.llm import LoopError, call_json
from grit.agents.reader_prompts import (
    ASSESSOR_SYSTEM,
    ASSESSOR_VERSION,
    CRITIC_SYSTEM,
    CRITIC_VERSION,
    REPORTER_BOTTOM_LINE_SYSTEM,
    REPORTER_ITEMS_SYSTEM,
    REPORTER_VERSION,
)
from grit.core.canonical import canonical_hash

MOVEMENTS = frozenset({"ESCALATING", "EASING", "NO_MATERIAL_CHANGE"})
NOTHING_FOUND = "No new development was found this week in the checked sources."
MAX_SOURCE_CHARS = 1500


@dataclass
class CallLog:
    actor: str
    version: str
    model: str
    subject: str
    input_hash: str
    output_hash: str | None = None
    status: str = "STARTED"
    attempts: int = 0
    violations: list[str] = field(default_factory=list)

    def as_record(self) -> dict[str, Any]:
        return {
            "actor": self.actor, "version": self.version, "model": self.model, "subject": self.subject, "input_hash": self.input_hash,
            "output_hash": self.output_hash, "status": self.status, "detail": {"attempts": self.attempts, "violations": self.violations[:20]},
        }


def _date(value: str | None) -> str | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%dT%H:%M:%SZ").strftime("%d %b %Y")
    except ValueError:
        return None


def source_table(records: Sequence[Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """S1..Sn for the model, and a map back to signal ids. The model never sees a URL."""
    table: dict[str, dict[str, Any]] = {}
    back: dict[str, str] = {}
    for number, record in enumerate(records, start=1):
        key = f"S{number}"
        table[key] = {
            "title": str(record.get("title") or ""),
            "outlet": str(record.get("publisher") or ""),
            "date": _date(record.get("published_at")) or "date not stated",
            "text": without_navigation(str(record.get("text") or ""))[:MAX_SOURCE_CHARS],
        }
        back[key] = str(record["signal_id"])
    return table, back


def _call(client: Any, model: str, system: str, payload: dict[str, Any], log: CallLog) -> dict[str, Any] | None:
    log.attempts += 1
    try:
        draft = call_json(client, model=model, system=system, payload=payload, retries=1, pause=2.0)
    except LoopError as exc:
        log.status = "LLM_ERROR"
        log.violations.append(f"LLM_ERROR: {exc}")
        return None
    log.output_hash = canonical_hash(draft)
    return draft


def check_condition(draft: Any, sources: Mapping[str, Mapping[str, Any]]) -> list[str]:
    if not isinstance(draft, Mapping):
        return ["BAD_SHAPE: the draft is not an object"]
    found: list[str] = []
    movement = draft.get("movement")
    if movement not in MOVEMENTS:
        found.append(f"BAD_VALUE: movement must be one of {sorted(MOVEMENTS)}")
    summary = draft.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return found + ["BAD_SHAPE: summary is empty"]
    ids = draft.get("source_ids") if isinstance(draft.get("source_ids"), list) else []
    found += source_problems(draft.get("source_ids"), sources, "source_ids")
    for mark in marked_ids(summary):
        if mark not in ids:
            found.append(f"UNLISTED_SOURCE: summary cites {mark}, which is not in source_ids.")
    found += quote_problems(draft.get("quotes", []), sources, "quotes", ids)
    text = f"{summary} {draft.get('change_from_prior') or ''}"
    found += text_problems(text, haystack(sources, ids), "summary")
    if movement in {"ESCALATING", "EASING"} and (not ids or not draft.get("quotes")):
        found.append("NO_EVIDENCE: a movement needs at least one cited source and one verbatim quote.")
    return found


def assess_condition(
    client: Any,
    model: str,
    *,
    condition: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    sources: Mapping[str, Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]] = (),
    previous: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, movement, summary, source_ids, change_from_prior, violations}. status is VALIDATED, NO_EVIDENCE, or WITHHELD."""
    payload: dict[str, Any] = {
        "item": {key: condition.get(key) for key in ("id", "name", "kind", "domain", "currentAssessment", "transmission", "watchIndicators") if condition.get(key) is not None},
        "last_week": dict(prior) if prior else "No prior weekly assessment is recorded.",
        "sources": sources,
    }
    if issues:
        payload |= {"previous_draft": dict(previous or {}), "critic_issues": [dict(item) for item in issues]}
    log = CallLog("Assessor", ASSESSOR_VERSION, model, str(condition.get("id")), canonical_hash(payload))
    if not sources and not issues:
        log.status = "SKIPPED_NO_SOURCES"
        return {"status": "NO_EVIDENCE", "movement": "NO_MATERIAL_CHANGE", "summary": NOTHING_FOUND, "source_ids": [], "change_from_prior": None, "violations": []}, log
    draft: dict[str, Any] | None = None
    violations: list[str] = []
    for _attempt in range(2):
        draft = _call(client, model, ASSESSOR_SYSTEM, payload, log)
        if draft is None:
            break
        violations = check_condition(draft, sources)
        if not violations:
            log.status = "VALIDATED"
            ids = list(draft.get("source_ids") or [])
            status = "VALIDATED" if ids else "NO_EVIDENCE"
            return {
                "status": status, "movement": draft["movement"], "summary": draft["summary"].strip(), "source_ids": ids,
                "change_from_prior": draft.get("change_from_prior"), "quotes": draft.get("quotes") or [], "violations": [],
            }, log
        log.violations.extend(violations)
        payload = payload | {"previous_draft": draft, "violations": violations}
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "movement": None, "summary": None, "source_ids": [], "change_from_prior": None, "violations": violations or log.violations}, log


def check_issue(issue: Any, item_ids: set[str], sources: Mapping[str, Mapping[str, Any]]) -> list[str]:
    if not isinstance(issue, Mapping):
        return ["BAD_SHAPE: issue is not an object"]
    found: list[str] = []
    if issue.get("item_id") not in item_ids:
        found.append(f"UNKNOWN_ITEM: {issue.get('item_id')}")
    if issue.get("kind") not in {"STALE", "UNSUPPORTED", "CONTRADICTION"}:
        found.append("BAD_VALUE: kind")
    source_id = issue.get("source_id")
    found += quote_problems([{"source_id": source_id, "quote": issue.get("quote")}], sources, "issue quote")
    correction = str(issue.get("correction") or "")
    if not correction.strip():
        found.append("BAD_SHAPE: correction is empty")
    else:
        found += text_problems(correction, haystack(sources, [source_id] if isinstance(source_id, str) else []), "correction")
    return found


def critique(client: Any, model: str, *, drafts: Sequence[Mapping[str, Any]], priors: Mapping[str, str], sources: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], CallLog]:
    """Returns (accepted issues, rejected issues with reasons, log). An issue without a verbatim proving quote is rejected."""
    payload = {"drafts": [dict(item) for item in drafts], "last_week": dict(priors), "sources": sources}
    log = CallLog("Critic", CRITIC_VERSION, model, "reader-drafts", canonical_hash(payload))
    raw = _call(client, model, CRITIC_SYSTEM, payload, log)
    if raw is None:
        return [], [], log
    item_ids = {str(item.get("item_id")) for item in drafts}
    accepted, rejected = [], []
    for issue in (raw.get("issues") if isinstance(raw.get("issues"), list) else [])[:12]:
        problems = check_issue(issue, item_ids, sources)
        (rejected if problems else accepted).append(dict(issue) | ({"rejected_because": problems} if problems else {}))
    log.status = "VALIDATED"
    log.violations = [f"REJECTED_ISSUE: {item.get('item_id')}: {item['rejected_because'][0]}" for item in rejected]
    return accepted, rejected, log


def report_items(client: Any, model: str, *, items: Sequence[Mapping[str, Any]], sources: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], CallLog]:
    """items: [{item_id, item_type, name, source_ids}]. Returns item_id -> {status, summary, source_ids, violations}."""
    payload = {"items": [dict(item) for item in items], "sources": {key: sources[key] for key in sorted({s for item in items for s in item["source_ids"]}, key=lambda k: int(k[1:]))}}
    log = CallLog("Reporter", REPORTER_VERSION, model, "reader-items", canonical_hash(payload))
    results: dict[str, dict[str, Any]] = {}
    if not items:
        log.status = "SKIPPED_NO_ITEMS"
        return results, log
    pending = {str(item["item_id"]): item for item in items}
    for _attempt in range(2):
        raw = _call(client, model, REPORTER_ITEMS_SYSTEM, payload, log)
        if raw is None:
            break
        retry: list[dict[str, Any]] = []
        for entry in raw.get("items") if isinstance(raw.get("items"), list) else []:
            if not isinstance(entry, Mapping) or str(entry.get("item_id")) not in pending:
                continue
            item = pending[str(entry["item_id"])]
            allowed = set(item["source_ids"])
            ids = entry.get("source_ids") if isinstance(entry.get("source_ids"), list) else []
            problems = source_problems(entry.get("source_ids"), {key: sources[key] for key in allowed}, "source_ids")
            summary = str(entry.get("summary") or "")
            if not summary.strip():
                problems.append("BAD_SHAPE: summary is empty")
            problems += [f"UNLISTED_SOURCE: {mark}" for mark in marked_ids(summary) if mark not in ids]
            problems += quote_problems(entry.get("quotes", []), sources, "quotes", ids)
            problems += text_problems(summary, haystack(sources, ids), "summary")
            if problems:
                retry.append({"item_id": item["item_id"], "violations": problems})
                results[str(item["item_id"])] = {"status": "WITHHELD", "summary": None, "source_ids": [], "violations": problems}
            else:
                results[str(item["item_id"])] = {"status": "VALIDATED", "summary": summary.strip(), "source_ids": ids, "violations": []}
                pending.pop(str(item["item_id"]))
        if not pending:
            break
        log.violations.extend(f"{entry['item_id']}: {entry['violations'][0]}" for entry in retry)
        payload = payload | {"items": [dict(pending[key]) for key in pending], "violations": retry}
    for key in pending:
        results.setdefault(key, {"status": "WITHHELD", "summary": None, "source_ids": [], "violations": ["NO_DRAFT: the Reporter returned no entry for this item."]})
    log.status = "VALIDATED" if not pending else "PARTIAL"
    return results, log


def bottom_line(client: Any, model: str, *, facts: Mapping[str, Any]) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, bottom_line, escalation}. Numbers must come from the supplied facts."""
    payload = {"facts": dict(facts)}
    log = CallLog("Reporter", REPORTER_VERSION, model, "bottom-line", canonical_hash(payload))
    grounding = canonical_text(facts)
    for _attempt in range(2):
        raw = _call(client, model, REPORTER_BOTTOM_LINE_SYSTEM, payload, log)
        if raw is None:
            break
        text = f"{raw.get('bottom_line') or ''} {raw.get('escalation') or ''}"
        problems = text_problems(strip_marks(text), grounding, "bottom line") if str(raw.get("bottom_line") or "").strip() else ["BAD_SHAPE: bottom_line is empty"]
        if not problems:
            log.status = "VALIDATED"
            return {"status": "VALIDATED", "bottom_line": strip_marks(str(raw["bottom_line"])), "escalation": strip_marks(str(raw.get("escalation") or ""))}, log
        log.violations.extend(problems)
        payload = payload | {"previous_draft": raw, "violations": problems}
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "bottom_line": None, "escalation": None}, log


def canonical_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(f"{key} {canonical_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(canonical_text(item) for item in value)
    return str(value if value is not None else "")
