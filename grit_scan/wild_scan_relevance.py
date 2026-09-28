"""Best-effort Trend, Risk Level, and Basis of Relevance for wild-scan candidates.

``grit_scan.relevance_scoring`` computes these three PowerApps columns for a
weekly ranked finding, which is always anchored to a governed baseline
condition (domain, descent, evidence movement). A raw wild-scan candidate --
one GDELT or Currents article, acquired automatically and never reviewed by
an analyst -- has none of that. It only has a title, a publisher, and
whatever STE theme keywords match its own text.

This module is the honest, narrower equivalent for that case:

* ``steThemes`` come from the same ``grit_scan.matchers.theme_matcher``
  keyword register used everywhere else in this codebase -- no separate
  matching logic.
* Risk Level is read off the STE theme *tier* the article matched (KEY is
  the entity's own highest-priority annual risk tier, TIER_2 next, EMERGING
  last). This is a structural fact about the governed register, not a guess
  at the article's real-world severity.
* Trend is always ``NO_MATERIAL_CHANGE``: a single unverified article never
  carries the evidence to say a condition is escalating or easing. That
  judgement stays reserved for the reviewed weekly cascade.
* Basis of Relevance to Business names the matched theme(s) and says plainly
  that this is a single-source, unreviewed signal -- never invents a
  business-area or transmission-path claim the wild-scan lane has no
  evidence for.
"""

from __future__ import annotations

from typing import Any

from grit_scan.matchers.theme_matcher import theme_match

_TIER_RISK_LEVEL = {"KEY": "HIGH", "TIER_2": "MEDIUM", "EMERGING": "LOW"}
_MAX_BASIS_LENGTH = 4000


def match_wild_scan_themes(text: str, themes_register: Any, hints: list[str] | None = None) -> list[dict[str, str]]:
    """Return every STE theme this candidate's own text matches. May be empty."""
    return list(theme_match(text, themes_register, hints)["matchedThemes"])


def classify_wild_scan_risk_level(matched_themes: list[dict[str, str]]) -> str:
    """Highest tier among the themes matched wins; no match is LOW."""
    tiers = {str(theme.get("tier") or "") for theme in matched_themes}
    for tier in ("KEY", "TIER_2", "EMERGING"):
        if tier in tiers:
            return _TIER_RISK_LEVEL[tier]
    return "LOW"


def compose_wild_scan_basis(matched_themes: list[dict[str, str]], *, publisher: str | None = None) -> str:
    """Compose Basis of Relevance to Business for a single unreviewed article."""
    theme_names = [str(theme.get("themeName")) for theme in matched_themes if theme.get("themeName")]
    outlet = f" (source: {publisher})" if publisher else ""
    if theme_names:
        text = (
            f"Single-source, unreviewed signal{outlet} whose own text matched the governed "
            f"STE annual risk theme(s): {'; '.join(theme_names)}. No analyst has verified this "
            "article and no business-area transmission path is established; monitoring only."
        )
    else:
        text = (
            f"Single-source, unreviewed signal{outlet} that did not match any governed STE "
            "annual risk theme this cycle. No business-area relevance is established; monitoring only."
        )
    return text[:_MAX_BASIS_LENGTH]
