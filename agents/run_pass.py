"""Run four agents on one Dataverse signal. They propose. Nothing is scored.

The Supervisor writes one execution and four agent-log rows. It does not
advance workflow state and does not write a score.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from agents.gemini import gemini_proposer
from agents.layer import run_agent
from agents.prompts import AGENT_NAMES
from agents.tools import AgentTools
from dataverse.client import DataverseClient, DataverseError
from dataverse.signals import agent_log_body, execution_body
from grit_scan.local_env import load_local_env
from grit_scan.workbook import workbook_path
from supervisor.service import Supervisor

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    load_local_env()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    proposer = gemini_proposer
    model = os.environ.get("GEMINI_MODEL")
    print(f"Model: {model or 'default'}")
    try:
        client = DataverseClient.from_env()
    except DataverseError as exc:
        print(exc)
        return 1
    signal = _latest_signal(client)
    if signal is None:
        print("No Dataverse signal to assess.")
        return 2
    names = _entity_names()
    named = _named_in_headline(names, signal["headline"])
    print(f"Signal: {signal['headline']}")
    print(f"Source: {signal['publisher']} | {signal['url']}")
    print(f"Register names in headline: {len(named)}")
    correlation_id = "AGENTS-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    supervisor = Supervisor(actor_id="supervisor-agents", known_entity_ids=frozenset(names))
    task = {
        "query": signal["headline"],
        "headline": signal["headline"],
        "publisher": signal["publisher"],
        "url": signal["url"],
        "search_results": [{"url": signal["url"], "title": signal["headline"]}],
        "permitted_entity_ids": named,
        "instruction": "Cite only the supplied URL. Cite an entity id only if it is in permitted_entity_ids. Do not score.",
    }
    tools = AgentTools(
        known_entity_ids=frozenset(names),
        search_results=[{"url": signal["url"], "title": signal["headline"]}],
    )
    prior: list[dict[str, str]] = []
    execution_id: str | None = None
    for agent_name in AGENT_NAMES:
        agent_task = dict(task)
        if agent_name == "Reporting":
            agent_task["prior_proposals"] = prior
        print(f"\n{agent_name}")
        try:
            proposal = run_agent(agent_name, agent_task, proposer, tools)
        except RuntimeError as exc:
            print(f"  Call failed: {exc}")
            print("  No agent-log row was written for this call.")
            continue
        result = supervisor.submit_agent_proposal(
            proposal,
            signal_id=signal["signal_id"],
            execution_id="pending",
            correlation_id=correlation_id,
            permitted_urls={signal["url"]},
        )
        validation = "ACCEPTED" if result["accepted"] else "REJECTED: " + ",".join(result["reason_codes"])
        if execution_id is None:
            execution_id = client.create(
                "grit_executions",
                execution_body(
                    name=correlation_id,
                    correlation_id=correlation_id,
                    started_at=fetched_at,
                    configuration_version="agents-1",
                ),
            )
            print(f"Execution: {execution_id}")
        record_id = client.create(
            "grit_agentexecutionlogs",
            agent_log_body(
                agent_name=agent_name,
                prompt_version=str(proposal.get("prompt_version") or ""),
                task=signal["headline"],
                citations=list(proposal.get("evidence_references") or []),
                response_hash=str(proposal.get("response_hash") or ""),
                rationale=proposal.get("rationale"),
                validation=validation,
                correlation_id=correlation_id,
                completed_at=fetched_at,
                execution_id=execution_id,
                signal_id=signal["signal_id"],
            ),
        )
        prior.append({"agent": agent_name, "validation": validation, "rationale": str(proposal.get("rationale") or "")[:500]})
        print(f"  {validation}")
        print(f"  Log: {record_id}")
        print(f"  Hash: {proposal.get('response_hash')}")
        if proposal.get("rationale"):
            print(f"  Rationale: {str(proposal['rationale'])[:240]}")
    print("\nWorkflow was not advanced. No score was written.")
    return 0


def _latest_signal(client: DataverseClient) -> dict[str, str] | None:
    payload = client.get(
        "grit_signals?$select=grit_signalid,grit_headline,grit_sourcename,grit_sourceurl,grit_correlationid"
        "&$orderby=createdon desc&$top=20"
    )
    for row in payload.get("value") or []:
        if not str(row.get("grit_correlationid") or "").startswith("CURRENTS-"):
            continue
        headline = str(row.get("grit_headline") or "").strip()
        url = str(row.get("grit_sourceurl") or "").strip()
        if headline and url:
            return {
                "signal_id": str(row["grit_signalid"]),
                "headline": headline,
                "publisher": str(row.get("grit_sourcename") or ""),
                "url": url,
            }
    return None


def _entity_names() -> dict[str, str]:
    workbook = workbook_path()
    aliases = ROOT / "config" / "registry_aliases.json"
    if not workbook.is_file() or not aliases.is_file():
        return {}
    from grit_scan.registry_loader import load_transmission_registry

    registry = load_transmission_registry(workbook, aliases)
    return {entry.entity_id: entry.canonical_name for entry in registry.entries}


def _named_in_headline(names: dict[str, str], headline: str) -> list[str]:
    text = headline.casefold()
    return [entity_id for entity_id, name in names.items() if name and name.casefold() in text]


if __name__ == "__main__":
    raise SystemExit(main())
