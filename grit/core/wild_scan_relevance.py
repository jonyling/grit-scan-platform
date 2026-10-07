"""Best-effort Theme, Risk Level, Trend, and Basis of Relevance for wild-scan candidates.

``grit.core.relevance_scoring`` computes these four PowerApps columns for a
weekly ranked finding, which is always anchored to a governed baseline
condition an analyst has already assigned. A raw wild-scan candidate -- one
GDELT or Currents article, acquired automatically and never reviewed --
only has a title and a publisher. This module maps that title, on a
best-effort basis, onto the same governed URS-TW register the reviewed
weekly cascade uses, so the "Theme" column shows one of the ten real
URS-TW baseline condition names (e.g. "US-China strategic competition"),
never a repeat of the signal's own headline:

* The candidate's title is matched against the 14 governed STE Annual Risk
  themes (``grit.ontology.matchers.theme_matcher``, same matcher used
  everywhere else).
* Each of the 10 URS-TW baseline conditions already declares which of those
  14 themes it belongs to (``condition["steThemes"]``, authored data, not
  invented here). The condition with the most overlapping themes wins; a
  candidate that matches no theme gets no condition.
* Basis of Relevance to Business is the matched condition's own governed
  ``taiwanTransmission`` sentence -- real authored content already stored in
  the baseline, not prose invented per condition name in this module -- with
  a short, honest note appended that this is a single-source, unreviewed
  wild-scan match, not an analyst-confirmed link.
* Risk Level is read off the STE theme *tier* the article matched (KEY is
  the entity's own highest-priority annual risk tier, TIER_2 next, EMERGING
  last) -- a structural fact about the governed register, not a guess at
  the article's real-world severity.
* Trend is always ``NO_MATERIAL_CHANGE``: a single unverified article never
  carries the evidence to say a condition is escalating or easing. That
  judgement stays reserved for the reviewed weekly cascade, which is why
  the baseline condition's own ``direction`` is not used here either.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grit.ontology.matchers.text_norm import normalise_theme
from grit.ontology.matchers.theme_matcher import theme_match

_TIER_RISK_LEVEL = {
    "KEY": "HIGH",
    "KEY_RISK": "HIGH",
    "TIER_2": "MEDIUM",
    "TIER2_RISK": "MEDIUM",
    "EMERGING": "LOW",
    "EMERGING_RISK": "LOW",
}
_TIER_RANK = ("KEY", "KEY_RISK", "TIER_2", "TIER2_RISK", "EMERGING", "EMERGING_RISK")
_MAX_BASIS_LENGTH = 4000
_MAX_HEADLINE_LENGTH = 400
_STOPWORDS = {"and", "of", "in", "the", "for", "to", "from", "a", "an"}
NO_CONDITION_HEADLINE = "Unclassified wild-scan signal (no governed URS-TW condition matched)"


def match_wild_scan_themes(text: str, themes_register: Any, hints: list[str] | None = None) -> list[dict[str, str]]:
    """Return every STE theme this candidate's own text matches. May be empty."""
    return list(theme_match(text, themes_register, hints)["matchedThemes"])


def match_wild_scan_condition(
    matched_themes: list[dict[str, str]],
    baseline: dict[str, Any] | Path | str,
) -> dict[str, Any] | None:
    """Return the baseline condition whose own STE themes overlap the most.

    A condition's ``steThemes`` entries are the risk owner's own short label
    for a theme (e.g. "Cybersecurity risks"), not always the register's full
    themeName (e.g. "Cybersecurity risks leading to major IT outage/data
    exfiltration (N2 & IBUs)") -- so this compares normalised word sets, not
    an exact string. A tie is broken by baseline order (the condition
    register's own sequence), never by picking an arbitrary one. No theme
    matched at all means no condition is returned -- nothing is guessed.
    """
    matched_names = [str(theme.get("themeName")) for theme in matched_themes if theme.get("themeName")]
    if not matched_names:
        return None
    conditions = list(_load(baseline).get("conditions") or [])
    best: dict[str, Any] | None = None
    best_score = 0
    for condition in conditions:
        condition_themes = [str(theme) for theme in (condition.get("steThemes") or [])]
        score = sum(
            1
            for label in condition_themes
            if any(_theme_label_matches(label, name) for name in matched_names)
        )
        if score > best_score:
            best = condition
            best_score = score
    return best


def _theme_label_matches(short_label: str, theme_name: str) -> bool:
    """True if every significant word in a condition's short theme label
    appears in the register's full theme name (any word order)."""
    label_words = set(normalise_theme(short_label).split()) - _STOPWORDS
    name_words = set(normalise_theme(theme_name).split())
    return bool(label_words) and label_words.issubset(name_words)


def classify_wild_scan_risk_level(matched_themes: list[dict[str, str]]) -> str:
    """Highest tier among the themes matched wins; no match is LOW."""
    tiers = {str(theme.get("tier") or "") for theme in matched_themes}
    for tier in _TIER_RANK:
        if tier in tiers:
            return _TIER_RISK_LEVEL[tier]
    return "LOW"


def wild_scan_headline(condition: dict[str, Any] | None) -> str:
    """The Theme column: the matched condition's own governed name, or an
    honest placeholder -- never the candidate's own title repeated."""
    if condition and condition.get("name"):
        return str(condition["name"])[:_MAX_HEADLINE_LENGTH]
    return NO_CONDITION_HEADLINE


def compose_wild_scan_basis(condition: dict[str, Any] | None, *, publisher: str | None = None) -> str:
    """Basis of Relevance to Business: the matched condition's own governed
    transmission sentence, with an honest single-source/unreviewed note."""
    outlet = f" (source: {publisher})" if publisher else ""
    if condition is None:
        text = (
            f"Single-source, unreviewed wild-scan signal{outlet} that did not match any governed "
            "URS-TW condition this cycle. No business-area relevance is established; monitoring only."
        )
        return text[:_MAX_BASIS_LENGTH]
    transmission = str(condition.get("taiwanTransmission") or "").strip()
    name = str(condition.get("name") or "this condition")
    caveat = (
        f" (Single-source wild-scan signal{outlet}, matched to '{name}' by STE theme overlap; "
        "not yet confirmed by an analyst.)"
    )
    text = f"{transmission}{caveat}" if transmission else (
        f"Matched to governed URS-TW condition '{name}'{outlet} by STE theme overlap; "
        "not yet confirmed by an analyst."
    )
    return text[:_MAX_BASIS_LENGTH]


def _load(register: dict[str, Any] | Path | str) -> dict[str, Any]:
    if isinstance(register, dict):
        return register
    import json

    return json.loads(Path(register).read_text(encoding="utf-8-sig"))
