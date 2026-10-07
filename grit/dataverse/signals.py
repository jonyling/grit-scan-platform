"""Map a wild-scan candidate to a Dataverse signal. No score is written."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from grit.acquisition.candidate import Candidate
from grit.dataverse.choices import (
    ACTOR_SUPERVISOR,
    EVIDENCE_LOW,
    EXECUTION_COMPLETE,
    RISK_LEVEL_HIGH,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MEDIUM,
    TRANSITION_START,
    TREND_EASING,
    TREND_ESCALATING,
    TREND_NO_MATERIAL_CHANGE,
    TRIGGER_SYSTEM,
    WORKFLOW_NEW,
)
from grit.core.wild_scan_relevance import (
    classify_wild_scan_risk_level,
    compose_wild_scan_basis,
    match_wild_scan_condition,
    match_wild_scan_themes,
    wild_scan_headline,
)
from grit.settings import CONFIG_DIR

_EVIDENCE_CONFIDENCE_CHOICE = {"HIGH": 100000500, "MEDIUM": 100000501, "LOW": EVIDENCE_LOW}
_TREND_CHOICE = {
    "ESCALATING": TREND_ESCALATING,
    "EASING": TREND_EASING,
    "NO_MATERIAL_CHANGE": TREND_NO_MATERIAL_CHANGE,
}
_RISK_LEVEL_CHOICE = {"HIGH": RISK_LEVEL_HIGH, "MEDIUM": RISK_LEVEL_MEDIUM, "LOW": RISK_LEVEL_LOW}
_CONFIG_ROOT = CONFIG_DIR
_DEFAULT_THEMES_REGISTER = _CONFIG_ROOT / "urs-tw" / "URS-TW-STE-Annual-Risks.json"
_DEFAULT_BASELINE_REGISTER = _CONFIG_ROOT / "urs-tw" / "URS-TW-Global-Baseline.json"



def execution_body(
    *,
    name: str,
    correlation_id: str,
    started_at: str,
    configuration_version: str = "gdelt-wild-1",
) -> dict[str, Any]:
    return {
        "grit_executionname": name[:100],
        "grit_executionversion": 1,
        "grit_correlationid": correlation_id[:100],
        "grit_configurationversion": configuration_version,
        "grit_supervisorversion": configuration_version,
        "grit_startedatutc": started_at,
        "grit_completedatutc": started_at,
        "grit_executionstatus": EXECUTION_COMPLETE,
    }


def signal_body(
    candidate: Candidate,
    *,
    execution_id: str,
    correlation_id: str,
    themes_register: dict[str, Any] | Path | str = _DEFAULT_THEMES_REGISTER,
    baseline_register: dict[str, Any] | Path | str = _DEFAULT_BASELINE_REGISTER,
) -> dict[str, Any]:
    """A wild-scan candidate never gets a formal score, but its own title is
    matched against the governed URS-TW STE theme register and baseline
    conditions (see ``grit.core.wild_scan_relevance``), so ``grit_headline``
    ("Theme" in PowerApps) carries one of the ten real condition names --
    never a repeat of the signal's own title -- and Basis of Relevance
    quotes that condition's own governed transmission sentence."""
    if not candidate.title or not candidate.url:
        raise ValueError("A Dataverse signal needs a title and a URL")
    matched_themes = match_wild_scan_themes(candidate.title, themes_register)
    condition = match_wild_scan_condition(matched_themes, baseline_register)
    risk_level = classify_wild_scan_risk_level(matched_themes)
    basis = compose_wild_scan_basis(condition, publisher=candidate.publisher)
    return {
        "grit_signalname": candidate.title[:100],
        "grit_headline": wild_scan_headline(condition),
        "grit_sourcename": candidate.publisher,
        "grit_sourceurl": candidate.url,
        "grit_fingerprint": hashlib.sha256(candidate.title.encode("utf-8")).hexdigest(),
        "grit_contenthash": candidate.content_hash,
        "grit_signalversion": 1,
        "grit_evidenceconfidence": EVIDENCE_LOW,
        "grit_trend": TREND_NO_MATERIAL_CHANGE,
        "grit_risklevel": _RISK_LEVEL_CHOICE[risk_level],
        "grit_businessrelevancebasis": basis,
        "grit_correlationid": correlation_id[:100],
        "grit_ExecutionId@odata.bind": f"/grit_executions({execution_id})",
    }


def agent_log_body(
    *,
    agent_name: str,
    prompt_version: str,
    task: str,
    citations: list[str],
    response_hash: str,
    rationale: str | None,
    validation: str,
    correlation_id: str,
    completed_at: str,
    execution_id: str,
    signal_id: str,
) -> dict[str, Any]:
    """Record a proposal. This is not a score and not a workflow transition."""
    from grit.dataverse.choices import AGENT_LOG_SUCCEEDED

    return {
        "grit_agentexecutionname": f"{correlation_id} {agent_name}"[:100],
        "grit_agentname": agent_name[:100],
        "grit_agentversion": prompt_version[:100],
        "grit_task": task[:2000],
        "grit_citations": "\n".join(citations)[:4000],
        "grit_responsehash": response_hash,
        "grit_responsereference": (rationale or "No rationale was returned.")[:4000],
        "grit_supervisorvalidationresult": validation[:200],
        "grit_correlationid": correlation_id[:100],
        "grit_logstatus": AGENT_LOG_SUCCEEDED,
        "grit_requireshumansignoff": True,
        "grit_completedatutc": completed_at,
        "grit_ExecutionId@odata.bind": f"/grit_executions({execution_id})",
        "grit_SignalId@odata.bind": f"/grit_signals({signal_id})",
    }


def workflow_body(
    candidate: Candidate,
    *,
    signal_id: str,
    execution_id: str,
    correlation_id: str,
    accepted_at: str,
    actor_id: str,
    reason_code: str = "GDELT_ARTLIST_CANDIDATE",
    reason: str = "GDELT artlist candidate. Article body was not fetched. Outlet credibility is not established.",
    configuration_version: str = "gdelt-wild-1",
) -> dict[str, Any]:
    return {
        "grit_workflowstatename": f"{correlation_id} NEW"[:100],
        "grit_transitionsequence": 0,
        "grit_state": WORKFLOW_NEW,
        "grit_transitiontype": TRANSITION_START,
        "grit_transitionreasoncode": reason_code,
        "grit_transitionreason": reason,
        "grit_transitiontimestamputc": accepted_at,
        "grit_actortype": ACTOR_SUPERVISOR,
        "grit_actorid": actor_id,
        "grit_triggertype": TRIGGER_SYSTEM,
        "grit_correlationid": correlation_id[:100],
        "grit_configurationversion": configuration_version,
        "grit_supervisorversion": configuration_version,
        "grit_inputhash": candidate.content_hash,
        "grit_statesnapshothash": hashlib.sha256(f"{candidate.content_hash}:NEW".encode("utf-8")).hexdigest(),
        "grit_ExecutionId@odata.bind": f"/grit_executions({execution_id})",
        "grit_SignalId@odata.bind": f"/grit_signals({signal_id})",
    }


def ranked_finding_content_hash(item: dict[str, Any]) -> str:
    """Identity key for a weekly-ranked finding, primary source URL first.

    A finding with no source at all still needs a stable identity across
    re-runs, so it falls back to the deterministic ``developmentId`` the
    weekly pipeline assigned; two distinct findings never collapse into one
    signal because of a shared or missing URL.
    """
    sources = list(item.get("sources") or [])
    primary = str(sources[0]) if sources else ""
    key = primary or f"NO-SOURCE-{item.get('developmentId') or item.get('conditionId') or ''}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def ranked_finding_body(
    item: dict[str, Any],
    *,
    execution_id: str,
    correlation_id: str,
    country_market: str | None = None,
    line_of_business: str | None = None,
) -> dict[str, Any]:
    """Map one ``weekly_pipeline.rank()`` item to a Dataverse ``grit_signals`` body.

    This is the Urban Solutions (or any reader pack) equivalent of the
    archived URS-TW cascade's PowerShell import: it writes Trend, Risk Level,
    and Basis of Relevance to Business Area, all three computed by
    ``grit.core.relevance_scoring`` from the Ontology model, never invented
    here. It writes no formal severity or likelihood score.
    """
    condition = item.get("condition") or item.get("conditionId")
    if not condition:
        raise ValueError("A ranked finding needs a condition to become a Dataverse signal")
    headline = str(item.get("headline") or item.get("summary") or condition)
    sources = list(item.get("sources") or [])
    confidence = str(item.get("evidenceConfidence") or "LOW").strip().upper()
    trend = str(item.get("trend") or "NO_MATERIAL_CHANGE")
    risk_level = str(item.get("riskLevel") or "LOW")
    basis = str(item.get("businessRelevanceBasis") or "")
    body: dict[str, Any] = {
        "grit_signalname": headline[:100],
        "grit_headline": str(condition)[:400],
        "grit_trend": _TREND_CHOICE.get(trend, TREND_NO_MATERIAL_CHANGE),
        "grit_risklevel": _RISK_LEVEL_CHOICE.get(risk_level, RISK_LEVEL_LOW),
        "grit_businessrelevancebasis": basis[:4000],
        "grit_evidenceconfidence": _EVIDENCE_CONFIDENCE_CHOICE.get(confidence, EVIDENCE_LOW),
        "grit_fingerprint": hashlib.sha256(headline.encode("utf-8")).hexdigest(),
        "grit_contenthash": ranked_finding_content_hash(item),
        "grit_signalversion": 1,
        "grit_correlationid": correlation_id[:100],
        "grit_ExecutionId@odata.bind": f"/grit_executions({execution_id})",
    }
    if sources:
        body["grit_sourceurl"] = str(sources[0])
    if country_market:
        body["grit_countrymarket"] = country_market
    if line_of_business:
        body["grit_lineofbusiness"] = line_of_business
    return body


def ranked_workflow_body(
    content_hash: str,
    *,
    signal_id: str,
    execution_id: str,
    correlation_id: str,
    accepted_at: str,
    actor_id: str,
    reason_code: str = "URS_WEEKLY_RANKED_FINDING",
    reason: str = "Urban Solutions weekly ontology finding, ranked by the deterministic priority function.",
    configuration_version: str = "urs-weekly-1",
) -> dict[str, Any]:
    """Same NEW workflow row as ``workflow_body``, for a ranked finding rather than a raw candidate."""
    return {
        "grit_workflowstatename": f"{correlation_id} NEW"[:100],
        "grit_transitionsequence": 0,
        "grit_state": WORKFLOW_NEW,
        "grit_transitiontype": TRANSITION_START,
        "grit_transitionreasoncode": reason_code,
        "grit_transitionreason": reason,
        "grit_transitiontimestamputc": accepted_at,
        "grit_actortype": ACTOR_SUPERVISOR,
        "grit_actorid": actor_id,
        "grit_triggertype": TRIGGER_SYSTEM,
        "grit_correlationid": correlation_id[:100],
        "grit_configurationversion": configuration_version,
        "grit_supervisorversion": configuration_version,
        "grit_inputhash": content_hash,
        "grit_statesnapshothash": hashlib.sha256(f"{content_hash}:NEW".encode("utf-8")).hexdigest(),
        "grit_ExecutionId@odata.bind": f"/grit_executions({execution_id})",
        "grit_SignalId@odata.bind": f"/grit_signals({signal_id})",
    }
