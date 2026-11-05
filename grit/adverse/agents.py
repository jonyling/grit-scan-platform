"""The adverse news scan's agents (spec 46): Screener, Assessor and Critic. They propose; grit.adverse.checks decides.

Each agent sees sources by id (S1, S2, ...) and never a URL. Every call is logged to the pool's execution log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from grit.adverse import checks
from grit.agents.loop.llm import LoopError, call_json

SCREEN_BATCH = 25
CRITIC_BATCH = 30

ROLE = (
    "You act as an experienced Chief Risk Officer, intelligence analyst and risk management professional for ST Engineering "
    "(STE), a Singapore-listed technology, defence and engineering group. Its business areas are Commercial Aerospace, "
    "Defence & Public Security, Digital Systems & Cybersecurity, and Urban Solutions. The objective is early warning for "
    "management, not a news summary. Quality is more important than quantity. Never speculate, exaggerate, infer unsupported "
    "conclusions, create hypothetical concerns, assume STE exposure where none is stated, treat neutral or positive news as "
    "adverse, or present an unverified relationship as confirmed. Describe allegations, investigations and charges as such."
)

SCREENER = ROLE + (
    " You screen candidate news items by title and lead. For each candidate id, choose one section: "
    "A = a material adverse development affecting STE, its subsidiaries, joint ventures, programmes, or a registered customer, "
    "supplier or partner (only when the candidate's link is DIRECT or COUNTERPARTY); "
    "B = a significant failure, incident, enforcement action or control weakness at a competitor, peer or adjacent-industry "
    "company from which STE can learn; "
    "C = a credible emerging risk signal (cyber threat, customer or supplier finances, defence budgets, geopolitics, regulation, "
    "export controls, sanctions, industry safety, quality or supply-chain concern) with a connection to STE's businesses; "
    "NONE = minor, routine, localised, immaterial, positive, neutral, opinion, petty crime, a namesake, or unrelated to STE. "
    "When unsure whether an item is material, prefer NONE. Return {\"decisions\": [{\"id\", \"section\", \"reason\"}]} with one "
    "decision for every id. reason is one short sentence."
)

_COMMON = (
    " Read the verified source pages (S1, S2, ...) and the register facts. First answer the five tests in tests: "
    "adverse_consequence (the specific adverse consequence), affected_party (who is impacted), management_relevance (why STE "
    "management would care), impact_pathway (the credible, evidence-based pathway to impact), and materiality (why the impact "
    "is material enough for management attention). If any test cannot be answered clearly from facts in the sources, return "
    "{\"include\": false, \"exclusion_reason\": \"...\"}. Otherwise return include: true and the fields below. "
    "Facts: a list of {statement, source_id, quote}; each quote is 20 or more characters copied exactly from that source. "
    "assessment: your evidence-based assessment, kept apart from facts. unverified: a list of points that remain unverified. "
    "Use only numbers that appear in the sources. Never write a URL. Do not use the words severity, likelihood or probability "
    "in prose. event_date: YYYY-MM-DD of the underlying event, with event_date_quote copied exactly from a source that states "
    "it, or 'not stated'. When publication_stated is false, give publication_date (YYYY-MM-DD) with publication_date_quote "
    "copied exactly from the page and containing the year, or 'not stated'. If earlier_report is given, set update_status to "
    "NO_MATERIAL_UPDATE when the sources only repeat known facts, or MATERIAL_UPDATE with new_development_quote copied exactly "
    "from a source and stating the new development (new findings, charges, judgment, settlement, financial impact, project or "
    "customer impact, regulatory action, affected entities, disruption or management response). Otherwise NEW. "
    "headline: one plain sentence naming the entity and what happened. severity: Critical (actual or imminent Group-level "
    "impact needing immediate EXCO attention), High (significant impact or credible exposure needing prompt follow-up), "
    "Medium (relevant, needs monitoring or targeted follow-up, no Group-level impact established), Low (limited but relevant "
    "with a credible connection to STE)."
)

ASSESSOR_A = ROLE + " You assess one Section A item: adverse news directly relevant to STE." + _COMMON + (
    " Fields: category (A1 Financial, A2 Project delivery, A3 Regulatory and compliance, A4 Cyber and security, A5 Safety, "
    "A6 Reputation, A7 Strategic: give the code), event, affected_entity, relationship (one of the allowed values), why_adverse, "
    "exposure (one of the allowed values; do not equate a commercial relationship with material exposure; if the "
    "relationship or dependency is not established, use 'Relationship identified, but material dependency not confirmed'), "
    "impacts (a list of {area, status, basis}; status CONFIRMED, POTENTIAL or NOT_ESTABLISHED; list only relevant areas the "
    "evidence supports), severity, action (Monitor, Follow-up with Business Area, Follow-up with Group Function, Escalate to "
    "EXCO), owner (a business area from the register facts or a Group Function from the allowed list, only where the evidence "
    "supports it, else null), action_rationale (why the action is proportionate), escalation_triggers (values from the "
    "allowed immediate-escalation list that the facts show, else []), exco_triggers (values from the mandatory EXCO list that "
    "the facts credibly show and that are material, else []), update_status."
)
ASSESSOR_B = ROLE + (
    " You assess one Section B item: a competitor, peer or industry incident. The central question is what STE can learn "
    "before the same issue happens to it. Never state or imply that STE has the same weakness."
) + _COMMON + (
    " Fields: event, affected_entity, why_material, control_weakness (what control, governance process or oversight failed, "
    f"only as the source states it, with control_weakness_quote copied exactly; else exactly '{checks.NO_CONTROL_WEAKNESS}'), "
    "risk_categories (values from the allowed list), lessons (specific, practical things STE could consider reviewing: "
    "policies, procedures, control design or effectiveness, third-party due diligence, contracting, monitoring, assurance, "
    "training, incident response, escalation), existing_exposure (one of the allowed values), coverage ('Unable to determine' "
    "unless internal material is supplied), internal_review (values from the allowed list), severity, update_status."
)
ASSESSOR_C = ROLE + (
    " You assess one Section C emerging risk signal. Include it only if credible evidence shows it may reasonably develop "
    "into a future material risk for STE. Do not report general trends without a credible connection to STE's businesses, "
    "markets, customers, suppliers, technologies or operating environment. The source supplies the facts and need not "
    "mention STE; you make the connection from ste_context (business areas, lines of business, and countries where STE "
    "entities are registered) and the register facts. Answer the tests from the source facts plus that context. Include the "
    "signal only when you can name the specific business area or line of business and the route by which the change reaches "
    "it (its market, customers, suppliers, technology access or operating country); otherwise exclude it. For an emerging "
    "signal, adverse_consequence and impact_pathway describe what could reasonably follow from the evidenced change, not an "
    "impact that has already happened; the change itself must be a fact in the source, not a forecast you make."
) + _COMMON + (
    " Fields: event (the signal: what has changed), affected_entity (who or what market is affected), why_it_matters, "
    "connection (one sentence that names ST Engineering, a business area or a line of business from the anchors), "
    "how_it_could_evolve (only a credible, evidence-based pathway), trigger_points (observable developments to watch), "
    "time_horizon (Immediate, Near term, Medium term, Longer term, or Unable to determine; any value but the last needs "
    "horizon_quote copied exactly from a source), response (Monitor, Obtain further information, Follow-up with Business "
    "Area, Follow-up with Group Function, Escalate to EXCO), severity, update_status."
)
ASSESSORS = {"A": ASSESSOR_A, "B": ASSESSOR_B, "C": ASSESSOR_C}

CRITIC = ROLE + (
    " You are the independent Critic. Review every item a colleague drafted for the weekly Adverse News & Lessons Learnt Scan. "
    "Object only where an item breaks a rule: NOT_ADVERSE (neutral or positive news), NOT_MATERIAL (management is unlikely to "
    "care or act), UNSUPPORTED_FACT (a statement the quotes do not support), ASSUMED_EXPOSURE (STE exposure or a relationship "
    "presented as confirmed without evidence), SPECULATIVE (a hypothetical concern or unsupported conclusion), DUPLICATE (the "
    "same event as another item), OUT_OF_PERIOD (no development in the reporting period), OVERSTATED (the rating or action "
    "is more than the evidence carries). Return {\"verdicts\": [{\"id\", \"verdict\", \"rule\", \"reason\"}], \"same_event\": [[id, id, ...]]}: "
    "verdict KEEP, DOWNGRADE (one step lower) or WITHDRAW, with one rule for each objection. Give a verdict for every id. "
    "same_event lists groups of ids, within one section, that report the same underlying event (for example one breach "
    "reported by several affected customers); those items are merged, so do not withdraw them as DUPLICATE."
)


@dataclass
class CallRecord:
    agent: str
    key: str
    attempts: int = 0
    status: str = "SUCCEEDED"
    violations: list[str] = field(default_factory=list)


def _call(client: Any, model: str, system: str, payload: dict[str, Any], record: CallRecord, thinking: str | None) -> dict[str, Any] | None:
    record.attempts += 1
    try:
        return call_json(client, model=model, system=system, payload=payload, retries=1, pause=2.0, thinking=thinking)
    except LoopError as exc:
        record.status = "LLM_ERROR"
        record.violations.append(f"LLM_ERROR: {exc}")
        return None


def screen_batch(client: Any, model: str, batch: Sequence[Mapping[str, Any]], *, allowed_a: set[str], thinking: str | None = "LOW"
                 ) -> tuple[dict[str, dict[str, str]] | None, CallRecord]:
    """Decisions for one batch, or None when the model fails twice or leaves ids out. A Section A decision needs a DIRECT or
    COUNTERPARTY link (allowed_a); otherwise it is dropped and recorded."""
    record = CallRecord("Screener", ",".join(str(item["id"]) for item in batch[:1]) + f"+{len(batch) - 1}")
    ids = [str(item["id"]) for item in batch]
    payload: dict[str, Any] = {"candidates": list(batch)}
    for _attempt in range(2):
        raw = _call(client, model, SCREENER, payload, record, thinking)
        if raw is None:
            return None, record
        decisions = {str(d.get("id")): d for d in raw.get("decisions") or [] if isinstance(d, Mapping)}
        missing = [key for key in ids if key not in decisions or decisions[key].get("section") not in {"A", "B", "C", "NONE"}]
        if not missing:
            out: dict[str, dict[str, str]] = {}
            for key in ids:
                section = str(decisions[key]["section"])
                reason = str(decisions[key].get("reason") or "")
                if section == "A" and key not in allowed_a:
                    record.violations.append(f"{key}: Section A needs a DIRECT or COUNTERPARTY link; dropped.")
                    section = "NONE"
                out[key] = {"section": section, "reason": reason}
            return out, record
        record.violations.append(f"MISSING_DECISIONS: {missing[:5]}")
        payload = {"candidates": list(batch), "violations": [f"Give one decision with section A, B, C or NONE for each of: {', '.join(missing)}"]}
    record.status = "WITHHELD"
    return None, record


def allowed_values(section: str) -> dict[str, Any]:
    common = {"severity": list(checks.SEVERITIES), "update_status": list(checks.UPDATE_STATUS)}
    if section == "A":
        return common | {"category": list(checks.CATEGORIES_A), "relationship": list(checks.RELATIONSHIPS), "exposure": list(checks.EXPOSURES),
                         "impact_area": list(checks.IMPACT_AREAS), "impact_status": list(checks.IMPACT_STATUS), "action": list(checks.ACTIONS),
                         "group_functions": list(checks.GROUP_FUNCTIONS), "escalation_triggers": list(checks.TRIGGERS_ESCALATION),
                         "exco_triggers": list(checks.TRIGGERS_EXCO)}
    if section == "B":
        return common | {"risk_categories": list(checks.RISK_CATEGORIES_B), "existing_exposure": list(checks.EXPOSURE_B),
                         "coverage": list(checks.COVERAGE), "internal_review": list(checks.INTERNAL_REVIEWS)}
    return common | {"time_horizon": list(checks.HORIZONS), "response": list(checks.RESPONSES_C)}


def assess_item(client: Any, model: str, section: str, task: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]], *,
                stated_publication: bool, previous: Mapping[str, Any] | None, anchors: Sequence[str], key: str,
                thinking: str | None = "LOW") -> tuple[dict[str, Any] | None, CallRecord]:
    """One Assessor draft that passes the checks, or None. A failing draft gets one retry with its violations."""
    record = CallRecord("Assessor", key)
    payload: dict[str, Any] = {**task, "section": section, "sources": {k: dict(v) for k, v in sources.items()},
                               "allowed_values": allowed_values(section), "publication_stated": stated_publication}
    if previous:
        payload["earlier_report"] = previous
    if section == "C":
        payload["anchors"] = list(anchors)
        payload["ste_context"] = {"business_areas_and_lines": list(anchors), **(dict(task.get("ste_context") or {}))}
    for attempt in range(2):
        draft = _call(client, model, ASSESSORS[section], payload, record, thinking)
        if draft is None:
            return None, record
        problems = checks.check_draft(draft, section, sources, stated_publication=stated_publication, previous=previous,
                                      anchors=anchors if section == "C" else ())
        if not problems:
            return draft, record
        record.violations.extend(problems)
        payload = payload | {"previous_draft": draft, "violations": problems}
    record.status = "WITHHELD"
    return None, record


def critique(client: Any, model: str, items: Sequence[Mapping[str, Any]], period: Mapping[str, str], *, thinking: str | None = "LOW"
             ) -> tuple[dict[str, dict[str, Any]] | None, list[list[str]], list[CallRecord]]:
    """Verdicts by item id, and groups of ids that report the same event. Verdicts are None when any batch fails, so the scan
    records that the Critic did not finish."""
    verdicts: dict[str, dict[str, Any]] = {}
    groups: list[list[str]] = []
    records: list[CallRecord] = []
    for start in range(0, len(items), CRITIC_BATCH):
        batch = list(items[start:start + CRITIC_BATCH])
        record = CallRecord("Critic", f"{start + 1}-{start + len(batch)}")
        records.append(record)
        raw = _call(client, model, CRITIC, {"reporting_period": dict(period), "items": batch}, record, thinking)
        if raw is None:
            return None, groups, records
        for verdict in raw.get("verdicts") or []:
            if isinstance(verdict, Mapping) and verdict.get("id"):
                verdicts[str(verdict["id"])] = dict(verdict)
        ids = {str(item["id"]) for item in batch}
        for found in raw.get("same_event") or []:
            if isinstance(found, list):
                members = [str(member) for member in found if str(member) in ids]
                if len(members) >= 2:
                    groups.append(list(dict.fromkeys(members)))
        missing = [str(item["id"]) for item in batch if str(item["id"]) not in verdicts]
        if missing:
            record.violations.append(f"MISSING_VERDICTS: {missing[:5]}; treated as KEEP.")
    return verdicts, groups, records


def critic_view(item: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("headline", "event", "affected_entity", "relationship", "why_adverse", "why_material", "why_it_matters", "how_it_could_evolve",
            "assessment", "exposure", "severity", "action", "response", "lessons", "control_weakness", "event_date", "publication_date",
            "update_status")
    view = {"id": item["item_id"], "section": item["section"], **{key: item[key] for key in keys if item.get(key) not in (None, "", [])}}
    view["facts"] = [{"statement": f.get("statement"), "quote": f.get("quote")} for f in item.get("facts") or []]
    view["link"] = item.get("link")
    return view


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


Logger = Callable[[CallRecord], None]
