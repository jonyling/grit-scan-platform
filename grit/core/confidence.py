"""Evidence confidence for a drafted condition or item, by rule. Pure.

The model cites sources. This rule grades them; the model never reports a confidence.
  HIGH    two or more independent outlets (a wire service counts once), at least one rated HIGH in the reader's register.
  MEDIUM  two or more independent outlets, or one HIGH-rated outlet.
  LOW     one outlet that is not rated HIGH.
  None    nothing cited. The brief prints "not established".
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from grit.core.priority import outlet_groups

TREND_LABELS = {"ESCALATING": "Escalating", "EASING": "Easing", "NO_MATERIAL_CHANGE": "Unchanged"}


def evidence_confidence(cited: Sequence[Mapping[str, Any]]) -> tuple[str | None, str]:
    """cited: one entry per cited page with outlet and credibilityRating. Returns (level, reason)."""
    if not cited:
        return None, "No source is cited."
    groups = outlet_groups([str(item.get("outlet") or item.get("url") or "") for item in cited])
    rated = sorted({str(item.get("outlet")) for item in cited if item.get("credibilityRating") == "HIGH"})
    reason = f"{len(groups)} independent outlet(s); HIGH-rated: {', '.join(rated) if rated else 'none'}."
    if len(groups) >= 2 and rated:
        return "HIGH", reason
    if len(groups) >= 2 or rated:
        return "MEDIUM", reason
    return "LOW", reason


def trend_label(movement: str | None) -> str:
    return TREND_LABELS.get(str(movement), "Not established")
