"""Decide whether a proposal may be accepted. The agent does not make this decision."""

from __future__ import annotations

from typing import Any

from grit.agents.prompts import AGENT_NAMES, FORBIDDEN_FIELDS


def validate_proposal(proposal: dict[str, Any], *, known_entity_ids: set[str], permitted_urls: set[str] | None = None) -> list[str]:
    reasons: list[str] = []
    if proposal.get("agent_name") not in AGENT_NAMES or proposal.get("is_proposal") is not True:
        reasons.append("INVALID_PROPOSAL")
    if proposal.get("forbidden_fields_present"):
        reasons.append("AGENT_COMPUTED_CONTROLLED_VALUE")
    if any(field in proposal for field in FORBIDDEN_FIELDS):
        reasons.append("AGENT_COMPUTED_CONTROLLED_VALUE")
    cited = [str(item) for item in proposal.get("cited_entity_ids") or []]
    if any(item not in known_entity_ids for item in cited):
        reasons.append("UNKNOWN_ENTITY")
    references = [str(item) for item in proposal.get("evidence_references") or []]
    if not references:
        reasons.append("UNEVIDENCED")
    if permitted_urls is not None and any(url not in permitted_urls for url in references):
        reasons.append("INVENTED_SOURCE")
    not_verifiable = list(proposal.get("not_verifiable") or [])
    if not_verifiable:
        reasons.append("NOT_VERIFIABLE")
    if not_verifiable and any(claim.get("established") is True for claim in proposal.get("claims") or []):
        reasons.append("FILLED_GAP")
    return list(dict.fromkeys(reasons))
