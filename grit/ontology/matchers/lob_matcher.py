"""Active-LOB scope gate. Inactive rows never match."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grit.ontology.matchers.text_norm import keyword_in_text, normalise


def lob_match(text: str, register: dict[str, Any] | Path | str) -> dict[str, Any]:
    active = [lob for lob in list(_load(register).get("lobs") or []) if lob.get("active") is True]
    if not text or not text.strip():
        return {"matched": False, "matchMethod": "EMPTY_INPUT", "lob": None}

    normalised = normalise(text)
    exact = [
        lob
        for lob in active
        if normalise(lob.get("lobName")) == normalised or normalise(lob.get("lobCode")) == normalised
    ]
    if len(exact) == 1:
        return {"matched": True, "matchMethod": "EXACT_NAME_OR_CODE", "lob": exact[0]}
    if len(exact) > 1:
        return {"matched": False, "matchMethod": "AMBIGUOUS_EXACT_MATCH", "lob": None}

    keyword_hits = []
    for lob in active:
        if any(keyword_in_text(normalised, normalise(keyword)) for keyword in list(lob.get("keywords") or [])):
            keyword_hits.append(lob)
    if len(keyword_hits) == 1:
        return {"matched": True, "matchMethod": "KEYWORD_MATCH", "lob": keyword_hits[0]}
    if len(keyword_hits) > 1:
        return {"matched": False, "matchMethod": "AMBIGUOUS_KEYWORD_MATCH", "lob": None}

    country_hits = [
        lob
        for lob in active
        if any(normalise(country) == normalised for country in list(lob.get("priorityCountries") or []))
    ]
    if len(country_hits) == 1:
        return {"matched": True, "matchMethod": "PRIORITY_COUNTRY_MATCH", "lob": country_hits[0]}
    if len(country_hits) > 1:
        return {"matched": False, "matchMethod": "AMBIGUOUS_COUNTRY_MATCH", "lob": None}
    return {"matched": False, "matchMethod": "NO_ACTIVE_LOB_MATCH", "lob": None}


def _load(register: dict[str, Any] | Path | str) -> dict[str, Any]:
    if isinstance(register, dict):
        return register
    return json.loads(Path(register).read_text(encoding="utf-8-sig"))
