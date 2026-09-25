"""Run one agent turn. The model proposes. Tools supply checks. Nothing is written here."""

from __future__ import annotations

from typing import Any, Callable

from agents.prompts import FORBIDDEN_FIELDS, PROMPTS
from agents.tools import AgentTools
from grit_scan.canonical import canonical_hash

Proposer = Callable[[str, str, dict[str, Any]], dict[str, Any]]


def run_agent(agent_name: str, task: dict[str, Any], proposer: Proposer, tools: AgentTools) -> dict[str, Any]:
    prompt_version, prompt = PROMPTS[agent_name]
    raw = proposer(agent_name, prompt, task)
    if not isinstance(raw, dict):
        raw = {}
    tool_results = _required_checks(agent_name, task, raw, tools)
    claims = []
    blocked = any(item.get("status") == "NOT_VERIFIABLE" for item in tool_results)
    for claim in list(raw.get("claims") or []):
        if not isinstance(claim, dict):
            continue
        claims.append(
            {
                "statement": str(claim.get("statement") or ""),
                "established": False if blocked else bool(claim.get("established")),
            }
        )
    references = [str(item) for item in raw.get("evidence_references") or []]
    if agent_name == "Researcher":
        permitted = {str(item.get("url")) for item in tools.search_results if item.get("url")}
        references = [url for url in references if url in permitted]
    proposal = {
        "agent_name": agent_name,
        "prompt_version": prompt_version,
        "is_proposal": True,
        "cited_entity_ids": [str(item) for item in raw.get("cited_entity_ids") or []],
        "evidence_references": references,
        "claims": claims,
        "rationale": None if raw.get("rationale") is None else str(raw.get("rationale")),
        "tool_results": tool_results,
        "not_verifiable": [item for item in tool_results if item.get("status") == "NOT_VERIFIABLE"],
        "forbidden_fields_present": sorted(field for field in FORBIDDEN_FIELDS if field in raw),
    }
    proposal["response_hash"] = canonical_hash(proposal)
    return proposal


def _required_checks(agent_name: str, task: dict[str, Any], raw: dict[str, Any], tools: AgentTools) -> list[dict[str, Any]]:
    if agent_name == "Researcher":
        return [tools.search(str(task.get("query") or ""))]
    if agent_name != "Critic":
        return []
    results = [tools.check_entity(str(item)) for item in raw.get("cited_entity_ids") or []]
    references = [str(item) for item in raw.get("evidence_references") or []]
    results.append(tools.check_corroboration(references))
    results.extend(tools.check_url(url) for url in references)
    return results
