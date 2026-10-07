"""Match a finding to every governed STE theme it actually touches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grit.ontology.matchers.text_norm import keyword_in_text, normalise_theme


def theme_match(
    text: str,
    register: dict[str, Any] | Path | str,
    hints: list[str] | None = None,
) -> dict[str, Any]:
    themes = list(_load(register).get("themes") or [])
    normalised = normalise_theme(text)
    matched: list[dict[str, str]] = []
    seen: set[str] = set()

    for theme in themes:
        keywords = [*(theme.get("keywords") or []), theme.get("themeName")]
        if any(keyword_in_text(normalised, normalise_theme(keyword)) for keyword in keywords):
            _add(matched, seen, theme, "KEYWORD_MATCH")

    for hint in hints or []:
        if not str(hint).strip():
            continue
        normalised_hint = normalise_theme(hint)
        hits = [
            theme
            for theme in themes
            if normalise_theme(theme.get("themeName")) == normalised_hint or theme.get("themeId") == hint
        ]
        if len(hits) == 1:
            _add(matched, seen, hits[0], "VALIDATED_HINT")

    return {"matchedThemes": matched, "matchCount": len(matched)}


def _add(matched: list[dict[str, str]], seen: set[str], theme: dict[str, Any], method: str) -> None:
    theme_id = str(theme.get("themeId") or "")
    if theme_id in seen:
        return
    seen.add(theme_id)
    matched.append(
        {
            "themeId": theme_id,
            "themeName": str(theme.get("themeName") or ""),
            "tier": str(theme.get("tier") or ""),
            "matchMethod": method,
        }
    )


def _load(register: dict[str, Any] | Path | str) -> dict[str, Any]:
    if isinstance(register, dict):
        return register
    return json.loads(Path(register).read_text(encoding="utf-8-sig"))
