"""Hard reader-band overrides and score→band mapping. Pure: agents never write these outcomes.

Before score composition decides the reader-facing band, evaluate a small ordered override catalog.
A hit sets band = Escalating and records override_rule_id + override_reason. Scoring may still run
for audit fields (tier, cues, reasons) but does not decide band membership when an override fires.

Bands (reader-facing): Escalating / Elevated / Watch.
Governance: deterministic functions in grit/core decide; R&A owns the instrument register.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.core.people import StaffGrounding, qualifying_threat_in_lead
from grit.core.red_alert import names_jurisdiction

BAND_ESCALATING = "Escalating"
BAND_ELEVATED = "Elevated"
BAND_WATCH = "Watch"
BANDS = (BAND_ESCALATING, BAND_ELEVATED, BAND_WATCH)

OVERRIDE_STAFF_THREAT = "OVERRIDE_STAFF_THREAT"
OVERRIDE_REG_IN_FORCE = "OVERRIDE_REG_IN_FORCE"

LEAD_CHARS = 600

# Sort key: Escalating first. Unknown / missing → after Watch.
BAND_ORDER: dict[str, int] = {
    BAND_ESCALATING: 0,
    BAND_ELEVATED: 1,
    BAND_WATCH: 2,
}

# Default significance → band when config/score-band-thresholds.json is absent.
_DEFAULT_SIGNIFICANCE_TO_BAND = {
    "TOP": BAND_ESCALATING,
    "SIGNIFICANT": BAND_ELEVATED,
    "SECONDARY": BAND_WATCH,
    "LOW": BAND_WATCH,
}

# Shared reader-band vocabulary for adverse / WSH product surfaces (P3 polish).
# Product section titles stay as-is; these aliases let one reader language map across lanes.
PRODUCT_BAND_ALIASES: dict[str, str] = {
    "Immediate Attention": BAND_ESCALATING,
    "Immediate Escalation Recommended": BAND_ESCALATING,
    "Enhanced Monitoring": BAND_ELEVATED,
    "Elevated Monitoring Required": BAND_ELEVATED,
    "Management Action Recommended": BAND_ELEVATED,
    "Watch": BAND_WATCH,
    "monitoring": BAND_WATCH,
    "No Material Concern": BAND_WATCH,
}

READER_BAND_VOCAB_NOTE = (
    "Reader bands across weekly briefs: Escalating / Elevated / Watch. "
    "Adverse Immediate Attention ≈ Escalating; Enhanced Monitoring ≈ Elevated; "
    "monitoring / watch items ≈ Watch. WSH digests use the same three-band language for severity cues."
)


@dataclass(frozen=True)
class OverrideHit:
    rule_id: str
    reason: str
    band: str = BAND_ESCALATING

    def as_record(self) -> dict[str, Any]:
        return {"override_rule_id": self.rule_id, "override_reason": self.reason, "band": self.band}


@lru_cache(maxsize=1)
def _load_score_band_thresholds(path: str | None = None) -> dict[str, Any]:
    from grit.settings import CONFIG_DIR

    file = Path(path) if path else CONFIG_DIR / "score-band-thresholds.json"
    if not file.is_file():
        return {
            "tier": {"escalating_max": 1, "elevated_max": 3},
            "significance": dict(_DEFAULT_SIGNIFICANCE_TO_BAND),
        }
    return json.loads(file.read_text(encoding="utf-8"))


def load_score_band_thresholds(path: Path | str | None = None) -> dict[str, Any]:
    """R&A-owned score→band cutoffs. Defaults match the P0 mapping when the file is missing."""
    return dict(_load_score_band_thresholds(str(path) if path else None))


def clear_score_band_threshold_cache() -> None:
    """Tests may swap the thresholds file; drop the cached parse."""
    _load_score_band_thresholds.cache_clear()


def band_from_tier(tier: int | None, *, thresholds: Mapping[str, Any] | None = None) -> str:
    """Map legacy briefing tier 1–4 to the three reader bands using R&A cutoffs."""
    if tier is None:
        return BAND_WATCH
    config = thresholds if thresholds is not None else load_score_band_thresholds()
    tier_cfg = config.get("tier") if isinstance(config.get("tier"), Mapping) else {}
    escalating_max = int(tier_cfg.get("escalating_max", 1))
    elevated_max = int(tier_cfg.get("elevated_max", 3))
    value = int(tier)
    if value <= escalating_max:
        return BAND_ESCALATING
    if value <= elevated_max:
        return BAND_ELEVATED
    return BAND_WATCH


def band_from_significance(
    significance: str | None, *, thresholds: Mapping[str, Any] | None = None
) -> str | None:
    """Map reasoned significance_band TOP/SIGNIFICANT/SECONDARY/LOW, or None when unknown."""
    if not significance:
        return None
    config = thresholds if thresholds is not None else load_score_band_thresholds()
    mapping = config.get("significance") if isinstance(config.get("significance"), Mapping) else _DEFAULT_SIGNIFICANCE_TO_BAND
    return mapping.get(str(significance).strip().upper()) or _DEFAULT_SIGNIFICANCE_TO_BAND.get(
        str(significance).strip().upper()
    )


def product_band_alias(label: str | None) -> str | None:
    """Map an adverse / WSH product label onto Escalating / Elevated / Watch when known."""
    if not label:
        return None
    text = str(label).strip()
    if text in BANDS:
        return text
    return PRODUCT_BAND_ALIASES.get(text)


def band_sort_key(band: str | None) -> int:
    return BAND_ORDER.get(str(band or ""), 9)


def reader_band_of(row: Mapping[str, Any] | None) -> str:
    """The reader band on an assessment / routed row, falling back to tier when band is absent."""
    if not row:
        return BAND_WATCH
    priority = row.get("priority_json") if "priority_json" in row else row.get("priority")
    if isinstance(priority, str):
        try:
            priority = json.loads(priority)
        except json.JSONDecodeError:
            priority = {}
    if isinstance(priority, Mapping) and priority.get("band") in BANDS:
        return str(priority["band"])
    if row.get("band") in BANDS:
        return str(row["band"])
    return band_from_tier(row.get("tier") if row.get("tier") is not None else (priority or {}).get("tier") if isinstance(priority, Mapping) else None)


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in phrase.split())
    return re.compile(rf"(?<![\w]){body}(?![\w])", re.IGNORECASE)


def _find_phrase(phrases: Sequence[str], text: str) -> str | None:
    for phrase in phrases:
        if _phrase_pattern(phrase).search(text):
            return phrase
    return None


def _names_pack_jurisdiction(text: str, jurisdictions: Sequence[str]) -> str | None:
    country = names_jurisdiction(text, jurisdictions)
    if country:
        return country
    for candidate in jurisdictions:
        if _phrase_pattern(candidate).search(text):
            return candidate
    return None


@lru_cache(maxsize=1)
def _load_instruments(path: str | None = None) -> dict[str, Any]:
    from grit.settings import CONFIG_DIR

    file = Path(path) if path else CONFIG_DIR / "override-instruments.json"
    if not file.is_file():
        return {"inForcePhrases": [], "speculativePhrases": [], "instruments": []}
    return json.loads(file.read_text(encoding="utf-8"))


def load_instruments(path: Path | str | None = None) -> dict[str, Any]:
    """R&A-owned instrument / in-force phrase register. Empty lists when the file is missing."""
    return dict(_load_instruments(str(path) if path else None))


def staff_threat_override(
    title: str,
    text: str,
    *,
    grounding: StaffGrounding | None,
    jurisdictions: Sequence[str] = (),
    terms: Sequence[str] | None = None,
) -> OverrideHit | None:
    """Strike / physical threat in a country with URS staff or pack entities → Escalating."""
    lead = f"{title or ''}\n{str(text or '')[:LEAD_CHARS]}"
    effective_terms = terms if terms is not None else grounding.terms if grounding is not None else None
    hits = qualifying_threat_in_lead(title, text, effective_terms)
    if not hits:
        return None
    if grounding is not None:
        countries = ", ".join(grounding.countries)
        terms_hit = ", ".join(hits)
        return OverrideHit(
            OVERRIDE_STAFF_THREAT,
            f"People-risk ({terms_hit}) in staffed country {countries}: hard override to Escalating.",
        )
    if not hits or not jurisdictions:
        return None
    country = _names_pack_jurisdiction(lead, jurisdictions)
    if not country:
        return None
    return OverrideHit(
        OVERRIDE_STAFF_THREAT,
        f"People-risk ({', '.join(hits)}) in pack-entity jurisdiction {country}: hard override to Escalating.",
    )


def regulation_in_force_override(
    title: str,
    text: str,
    *,
    jurisdictions: Sequence[str] = (),
    instruments: Mapping[str, Any] | None = None,
) -> OverrideHit | None:
    """Regulation / export-control already in force in a URS jurisdiction → Escalating.

    Speculative wording (may regulate, proposes, …) alone does not fire.
    """
    if not jurisdictions:
        return None
    register = instruments if instruments is not None else load_instruments()
    lead = f"{title or ''}\n{str(text or '')[:LEAD_CHARS]}"
    country = _names_pack_jurisdiction(lead, jurisdictions)
    if not country:
        return None
    in_force = _find_phrase(list(register.get("inForcePhrases") or []), lead)
    if not in_force:
        return None
    # Speculative framing in the same sentence as the in-force claim blocks the override
    # (e.g. "tariffs may take effect next year").
    if _speculative_in_force_sentence(lead, in_force, list(register.get("speculativePhrases") or [])):
        return None
    for item in register.get("instruments") or []:
        phrase = _find_phrase(list(item.get("phrases") or []), lead)
        if not phrase:
            continue
        label = str(item.get("label") or item.get("id") or phrase)
        return OverrideHit(
            OVERRIDE_REG_IN_FORCE,
            f"{label} ('{phrase}') {in_force} in URS jurisdiction {country}: hard override to Escalating.",
        )
    return None


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max((text.rfind(mark, 0, start) for mark in (". ", "\n", "? ", "! ")), default=-1)
    rights = [index for index in (text.find(mark, end) for mark in (". ", "\n", "? ", "! ")) if index >= 0]
    return text[left + 1 : min(rights) if rights else len(text)]


def _speculative_in_force_sentence(lead: str, in_force_phrase: str, speculative: Sequence[str]) -> bool:
    match = _phrase_pattern(in_force_phrase).search(lead)
    if match is None or not speculative:
        return False
    return _find_phrase(speculative, _sentence_around(lead, match.start(), match.end())) is not None


def first_override(
    title: str,
    text: str,
    *,
    grounding: StaffGrounding | None = None,
    jurisdictions: Sequence[str] = (),
    terms: Sequence[str] | None = None,
    instruments: Mapping[str, Any] | None = None,
) -> OverrideHit | None:
    """Ordered P0 catalog: staff/entity threat first, then regulation in force."""
    return staff_threat_override(title, text, grounding=grounding, jurisdictions=jurisdictions, terms=terms) or regulation_in_force_override(
        title, text, jurisdictions=jurisdictions, instruments=instruments
    )


def score_to_band(
    priority: Mapping[str, Any] | None,
    *,
    red: bool = False,
    thresholds: Mapping[str, Any] | None = None,
) -> str:
    """Band from score inputs only (no override). RED candidates land in Escalating."""
    if red:
        return BAND_ESCALATING
    record = priority or {}
    significance = band_from_significance(record.get("significance_band"), thresholds=thresholds)
    if significance:
        return significance
    return band_from_tier(record.get("tier"), thresholds=thresholds)


def apply_reader_band(
    priority: Mapping[str, Any] | None,
    *,
    title: str = "",
    text: str = "",
    grounding: StaffGrounding | None = None,
    jurisdictions: Sequence[str] = (),
    terms: Sequence[str] | None = None,
    instruments: Mapping[str, Any] | None = None,
    red: bool = False,
) -> dict[str, Any]:
    """Attach reader `band` (and override fields when a hard rule hits). Does not invent score cues.

    When an override fires, band is Escalating and tier is forced to 1 for compatibility with
    existing elevated / sort paths; original score fields (big, cues, reasons) remain for audit.
    """
    record = dict(priority or {})
    hit = first_override(
        title,
        text,
        grounding=grounding,
        jurisdictions=jurisdictions,
        terms=terms,
        instruments=instruments,
    )
    if hit is not None:
        record["band"] = hit.band
        record["override_rule_id"] = hit.rule_id
        record["override_reason"] = hit.reason
        reasons = list(record.get("reasons") or [])
        if hit.reason not in reasons:
            reasons.append(hit.reason)
        record["reasons"] = reasons
        # Compat: Escalating membership historically keyed off tier 1 for elevated event selection.
        # Keep the score's tier under a stable audit key, including when the score was already tier 1.
        if "score_tier" not in record:
            record["score_tier"] = record.get("tier")
        record["tier"] = 1
        return record

    record.pop("override_rule_id", None)
    record.pop("override_reason", None)
    # Score cutoffs load from R&A-owned config/score-band-thresholds.json (not the instrument register).
    record["band"] = score_to_band(record, red=red)
    return record
