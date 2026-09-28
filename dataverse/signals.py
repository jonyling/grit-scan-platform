"""Map a wild-scan candidate to a Dataverse signal. No score is written."""

from __future__ import annotations

import hashlib
from typing import Any

from acquisition.candidate import Candidate
from dataverse.choices import (
    ACTOR_SUPERVISOR,
    EVIDENCE_LOW,
    EXECUTION_COMPLETE,
    TRANSITION_START,
    TRIGGER_SYSTEM,
    WORKFLOW_NEW,
)


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


def signal_body(candidate: Candidate, *, execution_id: str, correlation_id: str) -> dict[str, Any]:
    if not candidate.title or not candidate.url:
        raise ValueError("A Dataverse signal needs a title and a URL")
    return {
        "grit_signalname": candidate.title[:100],
        "grit_headline": candidate.title[:400],
        "grit_sourcename": candidate.publisher,
        "grit_sourceurl": candidate.url,
        "grit_fingerprint": hashlib.sha256(candidate.title.encode("utf-8")).hexdigest(),
        "grit_contenthash": candidate.content_hash,
        "grit_signalversion": 1,
        "grit_evidenceconfidence": EVIDENCE_LOW,
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
    from dataverse.choices import AGENT_LOG_SUCCEEDED

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
