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
from grit.agents.checks import (
    MIN_QUOTE, haystack, marked_ids, normalise, quote_problems, sentence_cite_problems, source_problems, strip_marks,
    text_problems,
)
from grit.agents.loop.llm import LoopError, call_json
from grit.agents.reader_prompts import (
    ASSESSOR_SYSTEM,
    ASSESSOR_VERSION,
    KEYWORDER_SYSTEM,
    KEYWORDER_VERSION,
    BASELINER_SYSTEM,
    BASELINER_VERSION,
    CRITIC_SYSTEM,
    CRITIC_VERSION,
    EVENT_ROW_SYSTEM,
    EVENT_ROW_VERSION,
    SIGNPOST_JUDGE_SYSTEM,
    SIGNPOST_JUDGE_VERSION,
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


def _date_on_source(stated: str, source: Mapping[str, Any]) -> bool:
    """True when the stated day already appears on the source's date field or in its text."""
    stated = " ".join(str(stated or "").split())
    if not stated or not _DAY_MONTH.search(stated):
        return False
    hay = f"{source.get('date') or ''} {source.get('title') or ''} {str(source.get('text') or '')[:400]}"
    folded = normalise(hay)
    tokens = [token for token in re.findall(r"[A-Za-z]+|\d{1,4}", stated) if token.casefold() not in {"st", "nd", "rd", "th", "of"}]
    return bool(tokens) and all(normalise(token) in folded for token in tokens)


def check_sequence(value: Any, cited_ids: Sequence[str], sources: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
    """Two to four dated steps. Each date must already be on the cited source. Returns (cleaned steps, problems)."""
    if not isinstance(value, list) or not (2 <= len(value) <= 4):
        return [], ["SEQUENCE_MISSING: sequence: write two to four dated steps when the assessment cites a source."]
    cleaned: list[dict[str, str]] = []
    found: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            found.append("SEQUENCE_SHAPE: sequence: each step is an object with date, text and source_id.")
            continue
        day, text, source_id = item.get("date"), item.get("text"), item.get("source_id")
        if not isinstance(day, str) or not day.strip() or not isinstance(text, str) or not text.strip():
            found.append("SEQUENCE_SHAPE: sequence: each step needs a date and one sentence.")
            continue
        if source_id not in cited_ids or source_id not in sources:
            found.append(f"SEQUENCE_SOURCE: sequence: '{source_id}' must be one of the cited source_ids.")
            continue
        if not _date_on_source(day, sources[source_id]):
            found.append(f"SEQUENCE_DATE: sequence: '{day}' must already appear on {source_id}'s date or page.")
            continue
        found += text_problems(text, haystack(sources, [source_id]), "sequence")
        cleaned.append({"date": day.strip(), "text": text.strip(), "source_id": str(source_id)})
    if found:
        return [], found
    return cleaned, []


def format_timeline(sequence: Sequence[Mapping[str, str]], implication: str) -> str:
    """The Implications column: dated steps first, then the conditional sentence."""
    steps = [f"{step['date']}: {step['text'].rstrip(' .')} [{step['source_id']}]." for step in sequence]
    return (" ".join(steps) + " " + implication.strip()).strip()


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
    # Anchors are the business's own names. A possibility may name them without those words appearing on the cited page.
    named = sources
    if anchors:
        extra = " ".join(str(item) for item in anchors)
        named = {
            key: {**dict(value), "text": f"{value.get('text') or ''} {extra}".strip()} if isinstance(value, Mapping) else value
            for key, value in sources.items()
        }
    found += sentence_cite_problems(text, named, field_name)
    return found


def check_counter_indicator(text: Any, cited_ids: Sequence[str], sources: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """What would show the assessment is wrong. It looks forward, so it needs no citation. It adds no number and no score word."""
    if not isinstance(text, str) or not text.strip():
        return ["COUNTER_MISSING: counter_indicator: name the development that would show this assessment is wrong."]
    found: list[str] = []
    if len(re.findall(r"[.!?](?:\s|$)", text.strip())) > 2:
        found.append("TOO_LONG: counter_indicator: one or two sentences.")
    return found + text_problems(strip_marks(text), haystack(sources, cited_ids), "counter_indicator")


def check_extras(
    draft: Any, cited_ids: Sequence[str], sources: Mapping[str, Mapping[str, Any]], anchors: Sequence[str], group: bool
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """The fields that ride on a validated assessment: the dated sequence, the implication, and for a Group condition the
    opportunity and the counter-indicator (spec 37). Returns the fields that passed and the problems of those that failed."""
    accepted: dict[str, Any] = {}
    failing: dict[str, list[str]] = {}
    if not anchors or not cited_ids:
        return accepted, failing
    draft = draft if isinstance(draft, Mapping) else {}
    steps, sequence_problems = check_sequence(draft.get("sequence"), cited_ids, sources)
    checks: dict[str, list[str]] = {
        "sequence": sequence_problems,
        "implication": check_implication(draft.get("implication"), cited_ids, sources, anchors),
    }
    if group:
        opportunity = draft.get("opportunity")
        checks["opportunity"] = [] if opportunity in (None, "") else check_implication(opportunity, cited_ids, sources, anchors, "opportunity")
        checks["counter_indicator"] = check_counter_indicator(draft.get("counter_indicator"), cited_ids, sources)
    for name, problems in checks.items():
        if problems:
            failing[name] = problems
        elif name == "sequence":
            accepted[name] = steps
        else:
            value = draft.get(name)
            if name == "counter_indicator" and isinstance(value, str):
                value = strip_marks(value)  # it needs no citation, and a local [S#] would mean nothing in the brief
            accepted[name] = value.strip() if isinstance(value, str) and value.strip() else None
    if "sequence" in failing:
        accepted.pop("implication", None)
    elif "sequence" in accepted and "implication" in accepted and accepted["implication"]:
        accepted["implication"] = format_timeline(accepted["sequence"], accepted["implication"])
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
    fails its checks is dropped, and its violations are returned. The assessment stands (spec 36)."""
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
    if issue.get("kind") == "UNSUPPORTED" and isinstance(issue.get("missing_evidence"), str) and issue["missing_evidence"].strip():
        if source_id is not None and source_id not in sources:
            found.append("UNKNOWN_SOURCE: unsupported claim")
    else:
        found += quote_problems([{"source_id": source_id, "quote": issue.get("quote")}], sources, "issue quote")
    correction = str(issue.get("correction") or "")
    if not correction.strip():
        found.append("BAD_SHAPE: correction is empty")
    else:
        found += text_problems(correction, haystack(sources, [source_id] if isinstance(source_id, str) else []), "correction")
    return found


def critique(client: Any, model: str, *, drafts: Sequence[Mapping[str, Any]], priors: Mapping[str, str], sources: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], CallLog]:
    """Return accepted and rejected objections. An objection about missing support does not need a proving quote."""
    payload = {"drafts": [dict(item) for item in drafts], "last_week": dict(priors), "sources": sources}
    log = CallLog("Critic", CRITIC_VERSION, model, "reader-drafts", canonical_hash(payload))
    raw = _call(client, model, CRITIC_SYSTEM, payload, log)
    if raw is None:
        return [], [], log
    if not isinstance(raw, Mapping) or not isinstance(raw.get("issues"), list):
        log.status = "WITHHELD"
        log.violations = ["BAD_SHAPE: the Critic must return an issues list; missing output is not a clean review."]
        return [], [], log
    item_ids = {str(item.get("item_id")) for item in drafts}
    accepted, rejected = [], []
    for issue in raw["issues"]:
        problems = check_issue(issue, item_ids, sources)
        value = dict(issue) if isinstance(issue, Mapping) else {"raw_issue": issue}
        (rejected if problems else accepted).append(value | ({"rejected_because": problems} if problems else {}))
    log.status = "WITHHELD" if rejected else "VALIDATED"
    log.violations = [f"REJECTED_ISSUE: {item.get('item_id')}: {item['rejected_because'][0]}" for item in rejected]
    return accepted, rejected, log


def report_items(client: Any, model: str, *, items: Sequence[Mapping[str, Any]], sources: Mapping[str, Mapping[str, Any]],
                 business: Mapping[str, Any] | None = None) -> tuple[dict[str, dict[str, Any]], CallLog]:
    """items: [{item_id, item_type, name, source_ids}]. business: the pack's name and lines of business, so news about an
    item's unrelated businesses is not reported. Returns item_id -> {status, summary, source_ids, violations}."""
    payload = {"items": [dict(item) for item in items], "sources": {key: sources[key] for key in sorted({s for item in items for s in item["source_ids"]}, key=lambda k: int(k[1:]))}}
    if business:
        payload["business"] = dict(business)
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


CASES = ("BEST", "MEDIUM", "WORST")
_CASE_MARK = re.compile(r"\[([SP]\d{1,3}(?:\s*,\s*[SP]\d{1,3})*)\]")


def _case_marks(text: str) -> list[str]:
    found: list[str] = []
    for group in _CASE_MARK.findall(text or ""):
        found += [part.strip() for part in group.split(",")]
    return found


def _strings(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [item.strip() for item in value if item.strip()]


def check_scenarios(draft: Any, sources: Mapping[str, Mapping[str, Any]], anchors: Sequence[str], owners: Sequence[str], uncertainties: Sequence[str]) -> list[str]:
    """Best, medium and worst cases for STE (spec 43). A valid draft has:

    - one case of each kind;
    - cited evidence, with attributed forecasts and grounded numbers;
    - observable signposts that no two cases share;
    - impacts that name STE's own anchors and read as a possibility;
    - responses with a known owner and a trigger among the case's signposts.

    sources holds signals (S#) and passages (P#)."""
    from grit.agents.situation import FORECAST, sentences

    if not isinstance(draft, Mapping):
        return ["BAD_SHAPE: the draft is not an object"]
    found: list[str] = []
    if not isinstance(draft.get("reasoning"), str) or not draft["reasoning"].strip():
        found.append("NO_REASONING: reasoning: think first, in plain sentences.")
    raw_builds_on = draft.get("builds_on")
    builds_on = _strings(raw_builds_on) if raw_builds_on is not None else []
    known = {normalise(item): item for item in uncertainties}
    if builds_on is None:
        found.append("BAD_SHAPE: builds_on: a list of key uncertainty names.")
    elif uncertainties:
        if not 1 <= len(builds_on) <= 2:
            found.append("BAD_COUNT: builds_on: one or two key uncertainties.")
        else:
            found += [f"UNKNOWN_UNCERTAINTY: builds_on: '{item}' is not one of the key uncertainties; copy a name exactly." for item in builds_on if normalise(item) not in known]
    elif builds_on:
        found.append("UNKNOWN_UNCERTAINTY: builds_on: no key uncertainties were supplied, so builds_on is an empty list.")
    cases = draft.get("cases")
    if not isinstance(cases, list) or [item.get("case") if isinstance(item, Mapping) else None for item in cases] != list(CASES):
        return found + ["BAD_CASES: cases: exactly three, BEST, MEDIUM and WORST, in that order."]
    seen_signposts: dict[str, str] = {}
    names: set[str] = set()
    for item in cases:
        where = f"{item['case']} case"
        name, narrative = item.get("name"), item.get("narrative")
        if not isinstance(name, str) or not name.strip() or not isinstance(narrative, str) or not narrative.strip():
            found.append(f"BAD_SHAPE: {where}: needs a name and a narrative.")
            continue
        if normalise(name) in names:
            found.append(f"DUPLICATE: {where}: each case needs its own name.")
        names.add(normalise(name))
        if not _MODAL.search(narrative):
            found.append(f"NOT_CONDITIONAL: {where}: narrative uses could, may or might.")
        elif _CERTAIN.search(narrative):
            found.append(f"CERTAINTY_CLAIM: {where}: narrative: '{_CERTAIN.search(narrative).group(0)}'.")
        evidence = _strings(item.get("evidence"))
        cited: list[str] = []
        if not evidence or not 1 <= len(evidence) <= 3:
            found.append(f"BAD_COUNT: {where}: one to three evidence lines.")
        for number, line in enumerate(evidence or [], start=1):
            ids = _case_marks(line)
            plain_line = _CASE_MARK.sub("", line).strip()
            if not ids:
                found.append(f"UNCITED: {where}: evidence {number} ends with its ids, such as [S1] or [P3].")
                continue
            unknown = [ref for ref in ids if ref not in sources]
            if unknown:
                found.append(f"UNKNOWN_SOURCE: {where}: evidence {number}: {', '.join(unknown)} was not supplied.")
            ids = [ref for ref in ids if ref in sources]
            cited += ids
            found += text_problems(plain_line, haystack(sources, ids), f"{where} evidence {number}")
            for sentence in sentences(line):
                forecast = FORECAST.search(_CASE_MARK.sub("", sentence))
                if not forecast:
                    continue
                names_ok = [str(alias) for ref in ids for alias in (sources[ref].get("publisher"), sources[ref].get("outlet"), *(sources[ref].get("aliases") or [])) if alias]
                if "according to" not in sentence.casefold() and not any(alias.casefold() in sentence.casefold() for alias in names_ok):
                    found.append(f"UNATTRIBUTED_FORECAST: {where}: evidence {number} uses '{forecast.group(0)}'. Name who makes the forecast.")
        signposts, counters = _strings(item.get("signposts")), _strings(item.get("counter_indicators"))
        if not signposts or not 3 <= len(signposts) <= 4:
            found.append(f"BAD_COUNT: {where}: three or four signposts.")
        if not counters or not 1 <= len(counters) <= 2:
            found.append(f"BAD_COUNT: {where}: one or two counter-indicators.")
        for text in signposts or []:
            key = normalise(text)
            if key in seen_signposts and seen_signposts[key] != item["case"]:
                found.append(f"SHARED_SIGNPOST: {where}: '{text[:60]}' is also a {seen_signposts[key]} signpost. Each case needs its own.")
            seen_signposts.setdefault(key, item["case"])
        impact = _strings(item.get("impact"))
        if not impact or not 1 <= len(impact) <= 3:
            found.append(f"BAD_COUNT: {where}: one to three impact sentences.")
        for number, text in enumerate(impact or [], start=1):
            if not _MODAL.search(text):
                found.append(f"NOT_CONDITIONAL: {where}: impact {number} uses could, may or might.")
            elif _CERTAIN.search(text):
                found.append(f"CERTAINTY_CLAIM: {where}: impact {number}: '{_CERTAIN.search(text).group(0)}'.")
            if not anchor_hits(text, anchors):
                found.append(f"NO_ANCHOR: {where}: impact {number} names a business area, line of business, entity or country as business_area lists it.")
        gain = item.get("where_ste_could_gain")
        if gain not in (None, ""):
            if not isinstance(gain, str):
                found.append(f"BAD_SHAPE: {where}: where_ste_could_gain is one sentence or null.")
                gain = ""
            elif not _MODAL.search(gain):
                found.append(f"NOT_CONDITIONAL: {where}: where_ste_could_gain uses could, may or might.")
            elif _CERTAIN.search(gain):
                found.append(f"CERTAINTY_CLAIM: {where}: where_ste_could_gain: '{_CERTAIN.search(gain).group(0)}'.")
            elif not anchor_hits(gain, anchors):
                found.append(f"NO_ANCHOR: {where}: where_ste_could_gain names a business area, line of business, entity or country as business_area lists it.")
        responses = item.get("responses")
        if not isinstance(responses, list) or not 1 <= len(responses) <= 2:
            found.append(f"BAD_COUNT: {where}: one or two responses.")
            responses = []
        for number, response in enumerate(responses, start=1):
            if not isinstance(response, Mapping) or not isinstance(response.get("action"), str) or not response["action"].strip():
                found.append(f"BAD_SHAPE: {where}: response {number} needs an action, an owner and a trigger.")
                continue
            if response.get("owner") not in owners:
                found.append(f"UNKNOWN_OWNER: {where}: response {number}: owner must be one of owners.")
            trigger = response.get("trigger")
            if not isinstance(trigger, int) or isinstance(trigger, bool) or not 1 <= trigger <= len(signposts or []):
                found.append(f"BAD_TRIGGER: {where}: response {number}: trigger is the number of one of this case's signposts.")
            if _CERTAIN.search(response["action"]):
                found.append(f"CERTAINTY_CLAIM: {where}: response {number}: '{_CERTAIN.search(response['action']).group(0)}'.")
        loose = [narrative, *(signposts or []), *(counters or []), *(impact or []), *([gain] if isinstance(gain, str) and gain.strip() else []), *[str(r.get("action")) for r in responses if isinstance(r, Mapping)]]
        if any(_case_marks(text) for text in loose):
            found.append(f"MARK_IN_TEXT: {where}: [S#] and [P#] marks belong in evidence only.")
        found += text_problems(" ".join(loose), haystack(sources, cited), where)
    return found


def propose_scenarios(
    client: Any, model: str, *, shift: Mapping[str, Any], business_area: Mapping[str, Any], owners: Sequence[str], conditions: Sequence[Mapping[str, Any]],
    situation: Mapping[str, Any] | None, uncertainties: Sequence[str], sources: Mapping[str, Mapping[str, Any]], reviewed: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, builds_on, cases, violations}. status is PROPOSED, WITHHELD or NO_EVIDENCE. The reasoning goes to the
    log entry only."""
    anchors = [str(item) for item in business_area.get("anchors") or []]
    payload: dict[str, Any] = {
        "shift": {key: shift.get(key) for key in ("themeId", "themeName", "keywords")},
        "situation": situation, "uncertainties": list(uncertainties),
        "business_area": {key: value for key, value in business_area.items() if key != "anchors"},
        "owners": list(owners), "conditions": [dict(item) for item in conditions],
        "sources": {key: {k: v for k, v in value.items() if k not in {"aliases", "report_id", "page", "page_label"}} for key, value in sources.items()},
    }
    if reviewed:
        payload["reviewed"] = dict(reviewed)
    log = CallLog("Strategist", STRATEGIST_VERSION, model, str(shift.get("themeId")), canonical_hash(payload))
    if not sources:
        log.status = "SKIPPED_NO_SOURCES"
        return {"status": "NO_EVIDENCE", "builds_on": [], "cases": [], "violations": []}, log
    violations: list[str] = []
    for _attempt in range(3):
        draft = _call(client, model, STRATEGIST_SYSTEM, payload, log)
        if draft is None:
            break
        if isinstance(draft, Mapping) and isinstance(draft.get("reasoning"), str):
            log.reasoning = draft["reasoning"].strip()
        violations = check_scenarios(draft, sources, anchors, owners, uncertainties)
        if not violations:
            log.status = "VALIDATED"
            keep = ("case", "name", "narrative", "evidence", "signposts", "counter_indicators", "impact", "where_ste_could_gain", "responses")
            known = {normalise(item): item for item in uncertainties}
            return {"status": "PROPOSED", "builds_on": [known[normalise(item)] for item in _strings(draft.get("builds_on") or []) or []],
                    "cases": [{key: item.get(key) for key in keep} for item in draft["cases"]], "violations": []}, log
        log.violations.extend(violations)
        payload = payload | {"previous_draft": {key: value for key, value in dict(draft).items() if key != "reasoning"} if isinstance(draft, Mapping) else draft, "violations": violations}
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "builds_on": [], "cases": [], "violations": violations or log.violations}, log


_KEY_WORD = re.compile(r"[A-Za-z0-9]+")


def _words_of(text: str) -> set[str]:
    return {word.casefold() for word in _KEY_WORD.findall(text or "")}


def check_keywords(draft: Any, items: Mapping[str, str]) -> list[str]:
    """Two to four phrases per item, every word drawn from the item's own text, each long enough to match safely."""
    if not isinstance(draft, Mapping) or not isinstance(draft.get("keywords"), Mapping):
        return ["BAD_SHAPE: return {\"keywords\": {ITEM_ID: [phrases]}}."]
    keywords = draft["keywords"]
    found = [f"UNKNOWN_ITEM: {key} is not an item." for key in keywords if key not in items]
    for key, text in items.items():
        phrases = keywords.get(key)
        if not isinstance(phrases, list) or not all(isinstance(item, str) and item.strip() for item in phrases) or not 2 <= len(phrases) <= 4:
            found.append(f"BAD_COUNT: {key}: two to four keyword phrases.")
            continue
        own = _words_of(text)
        for phrase in phrases:
            missing = sorted(_words_of(phrase) - own)
            if missing:
                found.append(f"NOT_IN_TEXT: {key}: '{phrase}' uses {', '.join(missing)}, which the item's text does not.")
            stripped = phrase.strip()
            if len(stripped) < 4 and not (stripped.isupper() or any(char.isdigit() for char in stripped)):
                found.append(f"TOO_SHORT: {key}: '{phrase}' needs four or more letters, or capitals.")
    return found


def propose_keywords(client: Any, model: str, *, subject: str, items: Mapping[str, str]) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, keywords}. status is PROPOSED or WITHHELD."""
    payload: dict[str, Any] = {"items": [{"id": key, "text": text} for key, text in items.items()]}
    log = CallLog("Keyworder", KEYWORDER_VERSION, model, subject, canonical_hash(payload))
    violations: list[str] = []
    for _attempt in range(3):
        draft = _call(client, model, KEYWORDER_SYSTEM, payload, log)
        if draft is None:
            break
        violations = check_keywords(draft, items)
        if not violations:
            log.status = "VALIDATED"
            return {"status": "PROPOSED", "keywords": {key: [phrase.strip() for phrase in draft["keywords"][key]] for key in items}}, log
        log.violations.extend(violations)
        payload = payload | {"previous_draft": draft, "violations": violations}
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "keywords": {}, "violations": violations or log.violations}, log


_DAY_MONTH = re.compile(
    r"\b(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b"
    r"|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b|\b20\d\d-\d\d-\d\d\b)", re.I)
_FIGURE = re.compile(r"\d")


def specificity_problems(summary: str, sources: Mapping[str, Mapping[str, Any]], ids: Sequence[str], where: str = "summary") -> list[str]:
    """A summary gives a date, and a figure when its cited sources state one (format v2, improvement point 4)."""
    found: list[str] = []
    plain = strip_marks(summary)
    if not _DAY_MONTH.search(plain):
        found.append(f"NO_DATE: {where}: give the date of the development, such as 27 Oct, as the sources state it.")
    cited = " ".join(str(sources[item].get("title") or "") + " " + str(sources[item].get("text") or "")[:600] for item in ids if item in sources)
    if _FIGURE.search(cited) and not _FIGURE.search(plain):
        found.append(f"NO_FIGURE: {where}: the cited sources give figures; state the main one exactly as written.")
    if "\u2026" in summary or "..." in summary:
        found.append(f"ELLIPSIS: {where}: write whole sentences, with no ellipsis.")
    return found


def check_event_row(draft: Any, sources: Mapping[str, Mapping[str, Any]], anchors: Sequence[str], owners: Sequence[str]) -> list[str]:
    if not isinstance(draft, Mapping):
        return ["BAD_SHAPE: the draft is not an object"]
    found: list[str] = []
    item, summary = draft.get("item"), draft.get("summary")
    if not isinstance(item, str) or not item.strip() or len(item.split()) > 10 or "\u2026" in item or "..." in item:
        found.append("BAD_ITEM: item: a label of at most ten words, with no ellipsis.")
    if not isinstance(summary, str) or not summary.strip():
        return found + ["BAD_SHAPE: summary is empty"]
    found += source_problems(draft.get("source_ids"), sources, "source_ids")
    ids = [ref for ref in draft.get("source_ids") or [] if isinstance(ref, str) and ref in sources]
    marks = marked_ids(summary)
    if not marks:
        found.append("UNCITED: summary: end each sentence with [S#] ids.")
    found += [f"UNLISTED_SOURCE: summary cites {mark}, which is not in source_ids." for mark in marks if mark not in ids]
    # A source's own date is a fact the row may state, so it counts as grounded text.
    dated = f"{haystack(sources, ids)} " + " ".join(str(sources[item].get("date") or "") for item in ids if item in sources)
    found += text_problems(summary, dated, "summary")
    found += sentence_cite_problems(summary, sources, "summary")
    found += specificity_problems(summary, sources, marks or ids)
    steps, sequence_problems = check_sequence(draft.get("sequence"), ids, sources)
    found += sequence_problems
    found += check_implication(draft.get("implications"), ids, sources, anchors, "implications")
    response = draft.get("response") if isinstance(draft.get("response"), Mapping) else {}
    action, owner = str(response.get("action") or ""), response.get("owner")
    if not action.startswith("Consider"):
        found.append("NOT_FOR_CONSIDERATION: response.action: start with 'Consider'.")
    if _MUST.search(action):
        found.append("DIRECTIVE: response.action: do not write must.")
    if owner not in owners:
        found.append(f"UNKNOWN_OWNER: response.owner: '{str(owner)[:40]}' is not one of owners.")
    found += text_problems(strip_marks(action), haystack(sources, ids), "response.action")
    if not found and steps and isinstance(draft, dict):
        draft["implications"] = format_timeline(steps, str(draft.get("implications") or ""))
        draft["sequence"] = steps
    return found


def draft_event_row(
    client: Any, model: str, *, event_id: str, business_area: Mapping[str, Any], owners: Sequence[str], risks: Sequence[Mapping[str, Any]],
    requests: Sequence[str], sources: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, item, summary, implications, response, source_ids, violations}. status is VALIDATED or WITHHELD."""
    anchors = [str(item) for item in business_area.get("anchors") or []]
    payload: dict[str, Any] = {
        "business_area": {key: value for key, value in business_area.items() if key != "anchors"}, "owners": list(owners),
        "risks": [dict(item) for item in risks], "requests": list(requests), "sources": sources,
    }
    log = CallLog("EventAssessor", EVENT_ROW_VERSION, model, event_id, canonical_hash(payload))
    violations: list[str] = []
    for _attempt in range(2):
        draft = _call(client, model, EVENT_ROW_SYSTEM, payload, log)
        if draft is None:
            break
        if isinstance(draft, Mapping) and isinstance(draft.get("reasoning"), str):
            log.reasoning = draft["reasoning"].strip()
        violations = check_event_row(draft, sources, anchors, owners)
        if not violations:
            log.status = "VALIDATED"
            return {
                "status": "VALIDATED", "item": draft["item"].strip(), "summary": draft["summary"].strip(),
                "implications": draft["implications"].strip(), "sequence": draft.get("sequence") or [],
                "response": {"action": strip_marks(draft["response"]["action"]), "owner": draft["response"]["owner"]},
                "source_ids": [ref for ref in draft["source_ids"] if ref in sources], "violations": [],
            }, log
        log.violations.extend(violations)
        payload = payload | {"previous_draft": {key: value for key, value in dict(draft).items() if key != "reasoning"} if isinstance(draft, Mapping) else draft, "violations": violations}
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "violations": violations or log.violations}, log


_GENERIC = frozenset({
    "about", "above", "across", "after", "against", "among", "around", "below", "between", "major", "global", "other", "under", "their",
    "these", "those", "which", "while", "where", "within", "would", "could", "should", "being", "since", "until", "there", "price", "prices",
    "level", "levels", "large", "public", "report", "reports", "announce", "issue", "issues", "increase", "increased", "growth", "market",
    "markets", "company", "companies", "government", "governments", "national", "official", "officials", "including", "following",
})


def _stems(text: str) -> set[str]:
    """Specific words, compared by their first five letters, so 'overruns' meets 'overrun'."""
    return {word[:5] for word in re.findall(r"[a-z]{5,}", normalise(text)) if word not in _GENERIC}


def check_signpost_matches(draft: Any, items: Mapping[str, Mapping[str, Any]], sources: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
    """The matches that pass, and the problems of those that do not. A match names a known item, one of that item's own
    candidates, and a verbatim quote from it. A failing match is dropped; the others stand."""
    if not isinstance(draft, Mapping) or not isinstance(draft.get("matches"), list):
        return [], ["BAD_SHAPE: return {\"matches\": [...]}"]
    kept: list[dict[str, str]] = []
    problems: list[str] = []
    for number, match in enumerate(draft["matches"], start=1):
        where = f"match {number}"
        if not isinstance(match, Mapping):
            problems.append(f"BAD_SHAPE: {where}: each match is an object")
            continue
        item, source_id = str(match.get("item") or ""), str(match.get("source_id") or "")
        if item not in items:
            problems.append(f"UNKNOWN_ITEM: {where}: {item[:30]} is not an item id")
            continue
        if source_id not in (items[item].get("candidates") or []):
            problems.append(f"NOT_A_CANDIDATE: {where}: {source_id} is not a candidate for {item}")
            continue
        found = quote_problems([{"source_id": source_id, "quote": match.get("quote")}], sources, where)
        if found:
            problems += found
            continue
        if not _stems(str(match.get("quote"))) & _stems(str(items[item].get("text") or "")):
            problems.append(f"OFF_TOPIC: {where}: the quote shares no specific word with {item}. Quote the words that show its development, or drop the match.")
            continue
        kept.append({"item": item, "source_id": source_id, "quote": str(match["quote"]).strip()})
    return kept, problems


def judge_signposts(
    client: Any, model: str, *, risk: str, items: Mapping[str, Mapping[str, Any]], sources: Mapping[str, Mapping[str, Any]], subject: str,
) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, matches, violations}. status is VALIDATED (every match passed), PARTIAL (some dropped) or WITHHELD."""
    payload: dict[str, Any] = {"risk": risk, "items": {key: {"text": value["text"], "candidates": value["candidates"]} for key, value in items.items()}, "sources": sources}
    log = CallLog("SignpostJudge", SIGNPOST_JUDGE_VERSION, model, subject, canonical_hash(payload))
    if not sources or not items:
        log.status = "SKIPPED_NO_SOURCES"
        return {"status": "SKIPPED", "matches": [], "violations": []}, log
    kept: list[dict[str, str]] = []
    problems: list[str] = []
    for _attempt in range(2):
        draft = _call(client, model, SIGNPOST_JUDGE_SYSTEM, payload, log)
        if draft is None:
            break
        kept, problems = check_signpost_matches(draft, items, sources)
        if not problems:
            log.status = "VALIDATED"
            return {"status": "VALIDATED", "matches": kept, "violations": []}, log
        log.violations.extend(problems)
        payload = payload | {"previous_draft": draft, "violations": problems}
    if kept:
        log.status = "PARTIAL"
        return {"status": "PARTIAL", "matches": kept, "violations": problems}, log
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "matches": [], "violations": problems or log.violations}, log
