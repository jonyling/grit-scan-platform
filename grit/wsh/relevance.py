"""STE-context relevance check for WSH digest candidates (spec 47).

Deterministic scope drops residential, combat and unlinked noise first. This module asks the model only for
candidates that already passed that gate, and only whether the item is workplace safety relevant to STE's
watched industries. It does not invent findings or set scores.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from grit.wsh.prompt import RELEVANCE_PROMPT_VERSION, RELEVANCE_SYSTEM

INDUSTRIES = (
    "defence",
    "aerospace",
    "marine",
    "construction",
    "facilities management",
    "security services",
)


def relevance_task(signal: Mapping[str, Any], classification: Mapping[str, Any], section_id: str) -> dict[str, Any]:
    return {
        "prompt_version": RELEVANCE_PROMPT_VERSION,
        "section_id": section_id,
        "watched_industries": list(INDUSTRIES),
        "geography": "Singapore (phase 1); method papers may be global",
        "classification": {
            "place": classification.get("place"),
            "industries": classification.get("industries"),
            "hazards": classification.get("hazards"),
            "frameworks": classification.get("frameworks"),
            "regulators": classification.get("regulators"),
            "workplace_cue": classification.get("workplace_cue"),
            "exclude_cue": classification.get("exclude_cue"),
            "is_method": classification.get("is_method"),
            "is_regulatory": classification.get("is_regulatory"),
        },
        "source": {
            "title": signal.get("title"),
            "publisher": signal.get("publisher"),
            "published_at": signal.get("published_at"),
            "url": signal.get("url"),
            "text": (signal.get("text") or "")[:3500],
        },
        "decide": "KEEP or DROP",
        "rules": [
            "KEEP only if this is workplace safety (or a named WSH method/framework paper) relevant to one of the watched industries.",
            "DROP residential, clinical, combat, passenger-transport, school, hawker, or generic lifestyle heat stories.",
            "DROP if Singapore appears only as the speaker or flag state, not as the workplace locus.",
            "Do not invent investigation findings. reason must quote or paraphrase the source.",
            "Return JSON: decision (KEEP|DROP), reason (string), ste_industry (string or null).",
        ],
    }


def _parse_decision(raw: Mapping[str, Any]) -> dict[str, Any]:
    decision = str(raw.get("decision") or "").strip().upper()
    if decision not in {"KEEP", "DROP"}:
        # Some models bury the decision under claims. Treat a malformed answer as DROP, with a reason.
        return {"decision": "DROP", "reason": "Relevance draft withheld: decision was not KEEP or DROP.", "ste_industry": None}
    industry = raw.get("ste_industry")
    if industry is not None:
        industry = str(industry).strip() or None
    return {"decision": decision, "reason": str(raw.get("reason") or "not established"), "ste_industry": industry}


def check_relevance(
    signal: Mapping[str, Any],
    classification: Mapping[str, Any],
    section_id: str,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Ask Gemini whether a prefiltered candidate belongs in James Lek's STE WSH digest."""
    from grit.agents.gemini import gemini_client

    values_model = model or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    active = client or gemini_client()
    task = relevance_task(signal, classification, section_id)
    config = {
        "system_instruction": RELEVANCE_SYSTEM,
        "response_mime_type": "application/json",
        "temperature": 0.1,
    }
    try:
        response = active.models.generate_content(
            model=values_model,
            contents=json.dumps({"agent": "WSH-Relevance", "task": task}, default=str),
            config=config,
        )
        text = response.text or ""
    except Exception as exc:
        return {"decision": "DROP", "reason": f"Relevance check failed: {type(exc).__name__}", "ste_industry": None, "error": True}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"decision": "DROP", "reason": "Relevance draft withheld: not JSON.", "ste_industry": None, "error": True}
    if not isinstance(parsed, dict):
        return {"decision": "DROP", "reason": "Relevance draft withheld: not an object.", "ste_industry": None, "error": True}
    result = _parse_decision(parsed)
    result["prompt_version"] = RELEVANCE_PROMPT_VERSION
    return result
