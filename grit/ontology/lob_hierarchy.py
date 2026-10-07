"""Resolve a LOB label to the org chart LOB. The hierarchy file is hand-authored input."""

from __future__ import annotations

import re
from typing import Any


def key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def resolve_lob(name: str, hierarchy: dict[str, Any] | None) -> dict[str, Any] | None:
    """Find the org chart LOB for a label by its name or a confirmed alias. A label in unresolvedLabels never matches."""
    if not hierarchy:
        return None
    wanted = key(name)
    for lob in hierarchy.get("lobs") or []:
        if wanted in {key(lob["lobName"]), *(key(alias) for alias in lob.get("aliases") or [])}:
            return lob
    return None
