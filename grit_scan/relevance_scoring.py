"""Deterministic Risk Level, Trend, and Basis-of-Relevance-to-Business labels.

These are the three PowerApps-visible columns (``grit_risklevel``, ``grit_trend``,
``grit_businessrelevancebasis``) that the URS-TW cascade in the archived GRIT-Scan
repository used to populate with hand-written, per-condition English prose keyed
to Taiwan-specific keywords (attack, blockade, Kinmen, ...). That approach does
not generalise: it only ever covers the conditions someone happened to write a
sentence for, and it silently invents a Taiwan link for every other reader.

This module replaces that with functions of the Ontology model alone: the
baseline condition's ``domain``, the matched STE themes, the exposure/LOB
descent outcome, and the already-computed communication priority. Nothing
here is keyed to a condition name, a reader ID, or a hardcoded keyword list,
so it applies to any reader pack loaded by ``grit_scan.ontology`` -- Urban
Solutions (``urs``) as well as the Taiwan pack (``urs-tw``). A condition or
theme this module has never seen still gets a label, because the label is
built from structure, not from a lookup table of prose.

Like ``priorityLabel`` in ``weekly_pipeline.rank()``, these are indicative
communication labels for briefing composition, not a formal GRIT severity or
likelihood rating. They do not touch the Supervisor-gated workflow score.
"""

from __future__ import annotations

from typing import Any

TREND_LABELS = ("ESCALATING", "EASING", "NO_MATERIAL_CHANGE")
RISK_LEVELS = ("HIGH", "MEDIUM", "LOW")
_MAX_BASIS_LENGTH = 4000


def classify_trend(movement: str | None) -> str:
    """Map a revision's ``movement`` straight onto the Dataverse Trend choice.

    ``weekly_pipeline`` has already normalised movement to one of these three
    values before a development is accepted; an unrecognised value is treated
    as no material change rather than invented as a direction.
    """
    text = str(movement or "").strip().upper()
    return text if text in TREND_LABELS else "NO_MATERIAL_CHANGE"


def classify_risk_level(item: dict[str, Any]) -> str:
    """Derive Risk Level from the ranked item's own priority label.

    ``priorityLabel`` (HIGH / MEDIUM / WATCHPOINT) is already computed in
    ``weekly_pipeline._priority`` from evidence confidence, movement, STE
    theme breadth, and evidenced descent -- every one of those is an ontology
    fact, not a guess at what the headline means. Risk Level is that same
    judgement re-expressed on the three-value scale the Dataverse choice
    column uses; WATCHPOINT (nothing rises to a management priority yet)
    becomes LOW.
    """
    label = str(item.get("priorityLabel") or "").strip().upper()
    if label == "HIGH":
        return "HIGH"
    if label == "MEDIUM":
        return "MEDIUM"
    return "LOW"


def compose_business_relevance_basis(item: dict[str, Any]) -> str:
    """Compose the "Basis of Relevance to Business Area" from ontology facts only.

    Precedence:
      1. An evidenced descent (``descentStatus == "SUPPORTED"``): state the
         business area / line of business / entity and the transmission path
         the descent matcher itself produced.
      2. A bound exposure entity with no evidenced descent yet: name the
         matched entities and say this is monitoring-only.
      3. Neither: fall back to the condition's domain and matched STE themes.

    Every branch is built from fields the ontology matchers already emit
    (``domain``, ``steThemes``, ``descent``, ``exposureEntities``); no branch
    is keyed to a specific condition name or reader pack.
    """
    domain = str(item.get("domain") or "Unclassified domain").strip()
    themes = [str(theme) for theme in (item.get("steThemes") or []) if str(theme).strip()]
    theme_text = "; ".join(themes) if themes else "no STE theme matched this cycle"

    if item.get("descentStatus") == "SUPPORTED" and item.get("descent"):
        descent = item["descent"]
        business_area = descent.get("businessArea") or "an unnamed business area"
        line_of_business = descent.get("lineOfBusiness") or "an unnamed line of business"
        entity = descent.get("entityOrUnit") or "an unnamed entity"
        path = descent.get("transmissionPath") or "not recorded"
        text = (
            f"{domain} condition with an evidenced business transmission path: "
            f"{business_area} / {line_of_business} ({entity}). Transmission: {path}. "
            f"STE themes: {theme_text}."
        )
        return text[:_MAX_BASIS_LENGTH]

    entities = list(item.get("exposureEntities") or [])
    entity_names = list(dict.fromkeys(str(entity.get("entityName")) for entity in entities if entity.get("entityName")))
    if entity_names:
        text = (
            f"{domain} condition touching bound exposure entities ({', '.join(entity_names)}) "
            f"via STE themes: {theme_text}. No evidenced entity-level transmission path yet; "
            "treat as monitoring-only until a descent is supported."
        )
        return text[:_MAX_BASIS_LENGTH]

    text = (
        f"{domain} condition tracked under STE themes: {theme_text}. "
        "No bound exposure entity or evidenced transmission path was matched this cycle; monitoring only."
    )
    return text[:_MAX_BASIS_LENGTH]
