"""The reader-scan agents: Assessor, Critic, Reporter, and Proposer. They propose text. Checks decide what is valid.

Each function makes at most one retry with the list of violations. A draft that still fails is withheld, never patched.
Every call returns a log entry with the prompt version and the response hash, which the pipeline writes to the pool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from grit.acquisition.brave_plan import without_navigation
from grit.agents.checks import MIN_QUOTE, haystack, marked_ids, normalise, quote_problems, source_problems, strip_marks, text_problems
from grit.agents.loop.llm import LoopError, call_json
from grit.agents.reader_prompts import (
    ASSESSOR_SYSTEM,
    ASSESSOR_VERSION,
    BASELINER_SYSTEM,
    BASELINER_VERSION,
    CRITIC_SYSTEM,
    CRITIC_VERSION,
    PROPOSER_SYSTEM,
    PROPOSER_VERSION,
    REPORTER_BOTTOM_LINE_SYSTEM,
    REPORTER_ITEMS_SYSTEM,
    REPORTER_VERSION,
    STRATEGIST_SYSTEM,
    STRATEGIST_VERSION,
)
from grit.core.canonical import canonical_hash

MOVEMENTS = frozenset({"ESCALATING", "EASING", "NO_MATERIAL_CHANGE"})
NOTHING_FOUND = "No new development was found this week in the checked sources."
MAX_SOURCE_CHARS = 1500
KINDS = frozenset({"STRENGTHEN_CONTROL", "ACCELERATE_ACTION_PLAN", "ADDRESS_GAP", "MONITOR"})
_MODAL = re.compile(r"\b(could|may|might)\b", re.I)
_CERTAIN = re.compile(r"\b(will|certainly|definitely|inevitabl\w*|guarantee\w*|undoubtedly)\b", re.I)
_MUST = re.compile(r"\bmust\b", re.I)


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
    reasoning: str | None = None  # the Proposer's chain of thought: kept in the execution log, never in the brief

    def as_record(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"attempts": self.attempts, "violations": self.violations[:20]}
        if self.reasoning:
            detail["reasoning"] = self.reasoning
        return {
            "actor": self.actor, "version": self.version, "model": self.model, "subject": self.subject, "input_hash": self.input_hash,
            "output_hash": self.output_hash, "status": self.status, "detail": detail,
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


def anchor_hits(text: str, anchors: Sequence[str]) -> list[str]:
    """The business-area names a text uses: lines of business, groupings, entities, customers, and suppliers."""
    folded = normalise(text)
    return [anchor for anchor in anchors if len(anchor) >= 3 and re.search(rf"(?<![\w&]){re.escape(normalise(anchor))}(?![\w&])", folded)]


def check_implication(
    text: Any, cited_ids: Sequence[str], sources: Mapping[str, Mapping[str, Any]], anchors: Sequence[str], field_name: str = "implication"
) -> list[str]:
    """An implication (or an opportunity) names the business area's own anchors, reads as a possibility, and rests on the
    assessment's sources."""
    label = field_name.upper()
    if not isinstance(text, str) or not text.strip():
        return [f"{label}_MISSING: {field_name}: write one when the assessment cites a source."]
    found: list[str] = []
    marks = marked_ids(text)
    if not marks:
        found.append(f"{label}_UNCITED: {field_name}: end each sentence with [S#] ids from source_ids.")
    found += [f"UNLISTED_SOURCE: {field_name} cites {mark}, which is not in source_ids." for mark in marks if mark not in cited_ids]
    if not anchor_hits(strip_marks(text), anchors):
        found.append(f"NO_ANCHOR: {field_name}: name a line of business, entity, customer or supplier exactly as business_area lists it.")
    if not _MODAL.search(text):
        found.append(f"NOT_CONDITIONAL: {field_name}: use could, may or might.")
    certain = _CERTAIN.search(text)
    if certain:
        found.append(f"CERTAINTY_CLAIM: {field_name}: '{certain.group(0)}'. It is a possibility, not a forecast.")
    found += text_problems(text, haystack(sources, [mark for mark in marks if mark in cited_ids] or list(cited_ids)), field_name)
    return found


def check_counter_indicator(text: Any, cited_ids: Sequence[str], sources: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """What would show the assessment is wrong. It looks forward, so it needs no citation, but it adds no number and no score word."""
    if not isinstance(text, str) or not text.strip():
        return ["COUNTER_MISSING: counter_indicator: name the development that would show this assessment is wrong."]
    found: list[str] = []
    if len(re.findall(r"[.!?](?:\s|$)", text.strip())) > 2:
        found.append("TOO_LONG: counter_indicator: one or two sentences.")
    return found + text_problems(strip_marks(text), haystack(sources, cited_ids), "counter_indicator")


def check_extras(
    draft: Any, cited_ids: Sequence[str], sources: Mapping[str, Mapping[str, Any]], anchors: Sequence[str], group: bool
) -> tuple[dict[str, str | None], dict[str, list[str]]]:
    """The fields that ride on a validated assessment, each checked on its own: the implication, and for a Group condition the
    opportunity and the counter-indicator (spec 37). Returns the fields that passed and the problems of those that failed."""
    accepted: dict[str, str | None] = {}
    failing: dict[str, list[str]] = {}
    if not anchors or not cited_ids:
        return accepted, failing
    draft = draft if isinstance(draft, Mapping) else {}
    checks: dict[str, list[str]] = {"implication": check_implication(draft.get("implication"), cited_ids, sources, anchors)}
    if group:
        opportunity = draft.get("opportunity")
        checks["opportunity"] = [] if opportunity in (None, "") else check_implication(opportunity, cited_ids, sources, anchors, "opportunity")
        checks["counter_indicator"] = check_counter_indicator(draft.get("counter_indicator"), cited_ids, sources)
    for name, problems in checks.items():
        if problems:
            failing[name] = problems
        else:
            value = draft.get(name)
            if name == "counter_indicator" and isinstance(value, str):
                value = strip_marks(value)  # it needs no citation, and a local [S#] would mean nothing in the brief
            accepted[name] = value.strip() if isinstance(value, str) and value.strip() else None
    return accepted, failing


def _flat(failing: Mapping[str, Sequence[str]]) -> list[str]:
    return [problem for problems in failing.values() for problem in problems]


def assess_condition(
    client: Any,
    model: str,
    *,
    condition: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    sources: Mapping[str, Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]] = (),
    previous: Mapping[str, Any] | None = None,
    business_area: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, movement, summary, source_ids, change_from_prior, implication, violations}. status is VALIDATED,
    NO_EVIDENCE, or WITHHELD. With business_area (a key risk), the draft also carries an implication. An implication that
    fails its checks is dropped and its violations returned; the assessment stands (spec 36)."""
    payload: dict[str, Any] = {
        "item": {key: condition.get(key) for key in ("id", "name", "kind", "domain", "tier", "currentAssessment", "transmission", "watchIndicators") if condition.get(key) is not None},
        "last_week": dict(prior) if prior else "No prior weekly assessment is recorded.",
        "sources": sources,
    }
    anchors = [str(item) for item in (business_area or {}).get("anchors") or []]
    if business_area:
        payload["business_area"] = {key: value for key, value in business_area.items() if key != "anchors"}
    if issues:
        payload |= {"previous_draft": dict(previous or {}), "critic_issues": [dict(item) for item in issues]}
    log = CallLog("Assessor", ASSESSOR_VERSION, model, str(condition.get("id")), canonical_hash(payload))
    if not sources and not issues:
        log.status = "SKIPPED_NO_SOURCES"
        return {"status": "NO_EVIDENCE", "movement": "NO_MATERIAL_CHANGE", "summary": NOTHING_FOUND, "source_ids": [], "change_from_prior": None, "implication": None, "violations": []}, log
    group = bool((business_area or {}).get("group"))
    violations: list[str] = []
    accepted: dict[str, str | None] = {}
    failing: dict[str, list[str]] = {}
    passed: dict[str, Any] | None = None  # the first draft whose assessment passed. A retry after it may change only the failing extra fields.
    for _attempt in range(2):
        draft = _call(client, model, ASSESSOR_SYSTEM, payload, log)
        if draft is None:
            break
        if passed is not None:
            retried, still = check_extras(draft, list(passed.get("source_ids") or []), sources, anchors, group)
            for name in list(failing):
                if name in retried:
                    accepted[name] = retried[name]
                    del failing[name]
                else:
                    failing[name] = still.get(name, failing[name])
            log.violations.extend(_flat(failing))
            break
        violations = check_condition(draft, sources)
        if violations:
            log.violations.extend(violations)
            payload = payload | {"previous_draft": draft, "violations": violations}
            continue
        passed = dict(draft)
        accepted, failing = check_extras(draft, list(draft.get("source_ids") or []), sources, anchors, group)
        if not failing:
            break
        log.violations.extend(_flat(failing))
        payload = payload | {
            "previous_draft": draft, "violations": _flat(failing),
            "keep_assessment": f"Keep movement, summary, source_ids and quotes unchanged. Fix only: {', '.join(failing)}.",
        }
    if passed is not None:
        draft = passed
        log.status = "VALIDATED"
        ids = list(draft.get("source_ids") or [])
        return {
            "status": "VALIDATED" if ids else "NO_EVIDENCE", "movement": draft["movement"], "summary": draft["summary"].strip(), "source_ids": ids,
            "change_from_prior": draft.get("change_from_prior"), "quotes": draft.get("quotes") or [],
            "implication": accepted.get("implication"), "opportunity": accepted.get("opportunity"), "counter_indicator": accepted.get("counter_indicator"),
            "implication_violations": _flat(failing), "violations": [],
        }, log
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "movement": None, "summary": None, "source_ids": [], "change_from_prior": None, "implication": None, "violations": violations or log.violations}, log


def check_options(
    draft: Any, sources: Mapping[str, Mapping[str, Any]], theme: Mapping[str, Any], owners: Sequence[str]
) -> list[str]:
    """The Proposer's options against the theme's own controls and action plans, the owner roles, and the sources (spec 36)."""
    if not isinstance(draft, Mapping):
        return ["BAD_SHAPE: the draft is not an object"]
    found: list[str] = []
    if not isinstance(draft.get("reasoning"), str) or not draft["reasoning"].strip():
        found.append("NO_REASONING: reasoning: think first, in plain sentences.")
    options = draft.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        return found + ["BAD_COUNT: options: two to four options."]
    controls, plans = str(theme.get("transmission") or ""), str(theme.get("actionPlans") or "")
    theme_text = " ".join(str(theme.get(key) or "") for key in ("name", "currentAssessment", "transmission", "actionPlans"))
    monitors = 0
    for number, option in enumerate(options, start=1):
        where = f"option {number}"
        if not isinstance(option, Mapping):
            found.append(f"BAD_SHAPE: {where}: each option is an object")
            continue
        kind, text, owner = option.get("kind"), option.get("option"), option.get("owner")
        builds_on, trigger = str(option.get("builds_on") or "").strip(), str(option.get("trigger") or "").strip()
        if kind not in KINDS:
            found.append(f"BAD_VALUE: {where}: kind must be one of {sorted(KINDS)}")
            continue
        if not isinstance(text, str) or not strip_marks(text).startswith("Consider"):
            found.append(f"NOT_FOR_CONSIDERATION: {where}: start the option with 'Consider'.")
            continue
        if _MUST.search(text):
            found.append(f"DIRECTIVE: {where}: do not write must.")
        if owner not in owners:
            found.append(f"UNKNOWN_OWNER: {where}: '{str(owner)[:40]}' is not one of owner_roles.")
        ids = option.get("source_ids")
        found += source_problems(ids, sources, where)
        ids = [item for item in ids if item in sources] if isinstance(ids, list) else []
        if kind == "MONITOR":
            monitors += 1
            if not trigger:
                found.append(f"NO_TRIGGER: {where}: a MONITOR option names the development that would change the response.")
        elif not ids:
            found.append(f"UNCITED: {where}: cite at least one source id.")
        if kind in {"STRENGTHEN_CONTROL", "ACCELERATE_ACTION_PLAN"}:
            register = controls if kind == "STRENGTHEN_CONTROL" else plans
            label = "key_controls" if kind == "STRENGTHEN_CONTROL" else "action_plans"
            if not register.strip():
                found.append(f"NO_REGISTER_TEXT: {where}: the theme has no {label}. Use another kind.")
            elif len(builds_on) < MIN_QUOTE or normalise(builds_on) not in normalise(register):
                found.append(f"NOT_VERBATIM: {where}: builds_on must be a verbatim span of {MIN_QUOTE} or more characters from {label}.")
        found += text_problems(f"{text} {trigger}", f"{haystack(sources, ids)} {theme_text}", where)
    if monitors != 1:
        found.append("MONITOR_COUNT: options: exactly one MONITOR option.")
    return found


def propose_options(
    client: Any,
    model: str,
    *,
    theme: Mapping[str, Any],
    assessment: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    owners: Sequence[str],
) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, options, violations}. status is VALIDATED or WITHHELD. The reasoning goes to the log entry only."""
    payload: dict[str, Any] = {
        "theme": {
            "id": theme.get("id"), "name": theme.get("name"), "drivers": theme.get("currentAssessment"),
            "key_controls": theme.get("transmission"), "action_plans": theme.get("actionPlans") or "None recorded.",
        },
        "assessment": {key: assessment.get(key) for key in ("movement", "summary", "implication")},
        "sources": sources,
        "owner_roles": list(owners),
    }
    log = CallLog("Proposer", PROPOSER_VERSION, model, str(theme.get("id")), canonical_hash(payload))
    violations: list[str] = []
    for _attempt in range(2):
        draft = _call(client, model, PROPOSER_SYSTEM, payload, log)
        if draft is None:
            break
        if isinstance(draft, Mapping) and isinstance(draft.get("reasoning"), str):
            log.reasoning = draft["reasoning"].strip()
        violations = check_options(draft, sources, theme, owners)
        if not violations:
            log.status = "VALIDATED"
            options = [
                {
                    "kind": item["kind"], "option": strip_marks(item["option"]), "owner": item["owner"], "builds_on": str(item.get("builds_on") or "").strip() or None,
                    "trigger": str(item.get("trigger") or "").strip() or None, "source_ids": [ref for ref in item.get("source_ids") or [] if ref in sources],
                }
                for item in draft["options"]
            ]
            return {"status": "VALIDATED", "options": options, "violations": []}, log
        log.violations.extend(violations)
        payload = payload | {"previous_draft": {key: value for key, value in dict(draft).items() if key != "reasoning"} if isinstance(draft, Mapping) else draft, "violations": violations}
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "options": [], "violations": violations or log.violations}, log


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


NO_POSITION = "The sources in this run do not establish the current position."
HORIZONS = frozenset({"1-5 years", "5-10 years"})


def check_baseline(draft: Any, sources: Mapping[str, Mapping[str, Any]], indicators: Sequence[str]) -> list[str]:
    """A drafted current position: cited sentences, verbatim quotes, grounded numbers, and readings only for listed indicators."""
    if not isinstance(draft, Mapping):
        return ["BAD_SHAPE: the draft is not an object"]
    position = draft.get("position")
    if not isinstance(position, str) or not position.strip():
        return ["BAD_SHAPE: position is empty"]
    found = source_problems(draft.get("source_ids"), sources, "source_ids")
    ids = [item for item in draft.get("source_ids") or [] if item in sources] if isinstance(draft.get("source_ids"), list) else []
    readings = draft.get("indicators") if isinstance(draft.get("indicators"), list) else None
    if readings is None:
        return found + ["BAD_SHAPE: indicators must be a list"]
    texts = [position]
    for number, item in enumerate(readings, start=1):
        if not isinstance(item, Mapping) or item.get("indicator") not in indicators or not isinstance(item.get("reading"), str):
            found.append(f"UNKNOWN_INDICATOR: indicators {number}: copy one of the watch indicators exactly, with a reading.")
            continue
        texts.append(item["reading"])
        if not marked_ids(item["reading"]):
            found.append(f"UNCITED: indicators {number}: end the reading with [S#].")
    for text in texts:
        found += [f"UNLISTED_SOURCE: cites {mark}, which is not in source_ids." for mark in marked_ids(text) if mark not in ids]
    if ids:
        found += quote_problems(draft.get("quotes", []), sources, "quotes", ids)
        if not draft.get("quotes"):
            found.append("NO_EVIDENCE: a position with sources needs at least one verbatim quote.")
        if not marked_ids(position):
            found.append("UNCITED: position: end each sentence with [S#] ids.")
    found += text_problems(" ".join(texts + [str(draft.get("gaps") or "")]), haystack(sources, ids), "position")
    return found


def draft_baseline(client: Any, model: str, *, condition: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, position, indicators, gaps, source_ids, violations}. status is DRAFTED, NO_EVIDENCE, or WITHHELD."""
    indicators = [str(item) for item in condition.get("watchIndicators") or []]
    payload: dict[str, Any] = {
        "condition": {key: condition.get(key) for key in ("id", "name", "domain", "transmission", "watchIndicators")},
        "sources": sources,
    }
    log = CallLog("Baseliner", BASELINER_VERSION, model, str(condition.get("id")), canonical_hash(payload))
    if not sources:
        log.status = "SKIPPED_NO_SOURCES"
        return {"status": "NO_EVIDENCE", "position": NO_POSITION, "indicators": [], "gaps": "None of the watch indicators is covered.", "source_ids": [], "violations": []}, log
    violations: list[str] = []
    for _attempt in range(2):
        draft = _call(client, model, BASELINER_SYSTEM, payload, log)
        if draft is None:
            break
        violations = check_baseline(draft, sources, indicators)
        if not violations:
            log.status = "VALIDATED"
            ids = list(draft.get("source_ids") or [])
            return {
                "status": "DRAFTED" if ids else "NO_EVIDENCE", "position": draft["position"].strip(), "indicators": list(draft.get("indicators") or []),
                "gaps": str(draft.get("gaps") or "").strip() or None, "source_ids": ids, "violations": [],
            }, log
        log.violations.extend(violations)
        payload = payload | {"previous_draft": draft, "violations": violations}
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "position": None, "indicators": [], "gaps": None, "source_ids": [], "violations": violations or log.violations}, log


def check_scenarios(draft: Any, sources: Mapping[str, Mapping[str, Any]], anchors: Sequence[str]) -> list[str]:
    """Proposed scenarios: possibility, not forecast; observable signposts; STE sentences that name the Group's own anchors; and
    no number that the cited sources do not contain."""
    if not isinstance(draft, Mapping):
        return ["BAD_SHAPE: the draft is not an object"]
    found: list[str] = []
    if not isinstance(draft.get("reasoning"), str) or not draft["reasoning"].strip():
        found.append("NO_REASONING: reasoning: think first, in plain sentences.")
    scenarios = draft.get("scenarios")
    if not isinstance(scenarios, list) or not 2 <= len(scenarios) <= 4:
        return found + ["BAD_COUNT: scenarios: two to four scenarios."]
    for number, item in enumerate(scenarios, start=1):
        where = f"scenario {number}"
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str) or not isinstance(item.get("narrative"), str):
            found.append(f"BAD_SHAPE: {where}: needs a name and a narrative")
            continue
        if item.get("horizon") not in HORIZONS:
            found.append(f"BAD_VALUE: {where}: horizon must be one of {sorted(HORIZONS)}")
        signposts, counters = item.get("signposts"), item.get("counter_indicators")
        if not isinstance(signposts, list) or not 2 <= len([text for text in signposts if isinstance(text, str) and text.strip()]) <= 4:
            found.append(f"BAD_COUNT: {where}: two to four signposts.")
        if not isinstance(counters, list) or not 1 <= len([text for text in counters if isinstance(text, str) and text.strip()]) <= 2:
            found.append(f"BAD_COUNT: {where}: one or two counter-indicators.")
        ids = item.get("source_ids")
        found += source_problems(ids, sources, where)
        ids = [ref for ref in ids if ref in sources] if isinstance(ids, list) else []
        if not ids:
            found.append(f"UNCITED: {where}: cite in source_ids the sources the scenario starts from.")
        for field_name in ("narrative", "risk_for_ste"):
            text = item.get(field_name)
            if not isinstance(text, str) or not _MODAL.search(text):
                found.append(f"NOT_CONDITIONAL: {where}: {field_name} uses could, may or might.")
            elif _CERTAIN.search(text):
                found.append(f"CERTAINTY_CLAIM: {where}: {field_name}: '{_CERTAIN.search(text).group(0)}'.")
        for field_name in ("risk_for_ste", "opportunity_for_ste"):
            text = item.get(field_name)
            if field_name == "opportunity_for_ste" and text in (None, ""):
                continue
            if not isinstance(text, str) or not anchor_hits(text, anchors):
                found.append(f"NO_ANCHOR: {where}: {field_name} names a business area, line of business, entity or country as business_area lists it.")
        if any(marked_ids(str(item.get(key) or "")) for key in ("narrative", "risk_for_ste", "opportunity_for_ste")):
            found.append(f"MARK_IN_TEXT: {where}: put citations in source_ids, not [S#] in the text.")
        texts = [item.get("narrative"), item.get("risk_for_ste"), item.get("opportunity_for_ste"), *(signposts or []), *(counters or [])]
        found += text_problems(" ".join(str(text) for text in texts if isinstance(text, str)), haystack(sources, ids), where)
    return found


def propose_scenarios(
    client: Any, model: str, *, shift: Mapping[str, Any], business_area: Mapping[str, Any], conditions: Sequence[Mapping[str, Any]], sources: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, scenarios, violations}. status is PROPOSED or WITHHELD. The reasoning goes to the log entry only."""
    anchors = [str(item) for item in business_area.get("anchors") or []]
    payload: dict[str, Any] = {
        "shift": {key: shift.get(key) for key in ("themeId", "themeName", "keywords")},
        "business_area": {key: value for key, value in business_area.items() if key != "anchors"},
        "conditions": [dict(item) for item in conditions],
        "sources": sources,
    }
    log = CallLog("Strategist", STRATEGIST_VERSION, model, str(shift.get("themeId")), canonical_hash(payload))
    if not sources:
        log.status = "SKIPPED_NO_SOURCES"
        return {"status": "NO_EVIDENCE", "scenarios": [], "violations": []}, log
    violations: list[str] = []
    for _attempt in range(2):
        draft = _call(client, model, STRATEGIST_SYSTEM, payload, log)
        if draft is None:
            break
        if isinstance(draft, Mapping) and isinstance(draft.get("reasoning"), str):
            log.reasoning = draft["reasoning"].strip()
        violations = check_scenarios(draft, sources, anchors)
        if not violations:
            log.status = "VALIDATED"
            keep = ("name", "horizon", "narrative", "signposts", "counter_indicators", "risk_for_ste", "opportunity_for_ste", "source_ids")
            return {"status": "PROPOSED", "scenarios": [{key: item.get(key) for key in keep} for item in draft["scenarios"]], "violations": []}, log
        log.violations.extend(violations)
        payload = payload | {"previous_draft": {key: value for key, value in dict(draft).items() if key != "reasoning"} if isinstance(draft, Mapping) else draft, "violations": violations}
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "scenarios": [], "violations": violations or log.violations}, log
