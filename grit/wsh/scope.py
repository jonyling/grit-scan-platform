"""Classify WSH signals and decide STE-context relevance (spec 47).

An item is kept only when it is workplace safety for STE's watched industries
(defence, aerospace, marine, construction, facilities management, security services),
or a named method/framework source. Residential, clinical, combat and generic hits are dropped.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


def _text(title: str, body: str = "") -> str:
    return f"{title}\n{body}".casefold()


def _has_cue(text: str, cues: list[str] | tuple[str, ...], *, allow_negated: bool = False) -> str | None:
    for cue in cues:
        needle = cue.casefold()
        matched = False
        if " " in needle or "-" in needle:
            matched = needle in text
        else:
            matched = bool(re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text))
        if not matched:
            continue
        if not allow_negated and re.search(
            rf"\b(?:no|not|without|nor)\b.{{0,40}}{re.escape(needle)}|{re.escape(needle)}.{{0,24}}\b(?:not cited|is not|are not)\b",
            text,
        ):
            continue
        return cue
    return None


def watched_places(scope: Mapping[str, Any]) -> list[str]:
    places = scope.get("places") or {}
    phase = int(scope.get("geographyPhase") or 1)
    found = list(places.get("phase1") or [])
    if phase >= 2:
        found.extend(places.get("phase2") or [])
    return found


def _place_locus(title: str, body: str, scope: Mapping[str, Any]) -> str | None:
    """Singapore (or a phase-2 place) as the workplace/regulatory locus.

    Title matches count. Body matches count only next to workplace or regulator cues, so a scraped
    sidebar ('Merz will visit Singapore') does not locate an Indian or German story in Singapore.
    """
    places = watched_places(scope)
    false_positives = [item.casefold() for item in scope.get("placeFalsePositives") or []]

    def scrub(raw: str) -> str:
        text = raw.casefold()
        for false in false_positives:
            text = text.replace(false, " ")
        return text

    def hit(text: str) -> str | None:
        for place in places:
            p = place.casefold()
            if p in {"sg"} and not re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text):
                continue
            for pattern in (
                rf"in {re.escape(p)}\b",
                rf"at (?:a |an )?[^.]{{0,40}}\b{re.escape(p)}\b",
                rf"{re.escape(p)} construction",
                rf"{re.escape(p)} workplace",
                rf"{re.escape(p)} worksite",
                rf"{re.escape(p)} shipyard",
                rf"\b{re.escape(p)}\b",
            ):
                if re.search(pattern, text):
                    if p in {"singapore", "republic of singapore"} and re.search(
                        rf"\b{re.escape(p)}\b.{{0,40}}\b(visit|visiting|diplomatic|ties|says|said|flagged)\b", text
                    ):
                        continue
                    return place
        return None

    title_hit = hit(scrub(title))
    if title_hit:
        return title_hit
    # Body: only windows around workplace / MOM / WSH cues.
    body_text = scrub(body)
    anchors = list(scope.get("workplaceCues") or []) + ["ministry of manpower", "mom", "wsh council", "workplace safety"]
    windows = []
    for cue in anchors:
        for match in re.finditer(re.escape(cue.casefold()), body_text):
            start = max(0, match.start() - 80)
            end = min(len(body_text), match.end() + 80)
            windows.append(body_text[start:end])
    return hit(" \n ".join(windows)) if windows else None


def classify(title: str, body: str = "", *, scope: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a page to place, industry, hazard, workplace/exclude cues, framework body and regulator."""
    text = _text(title, body)
    place = _place_locus(title, body, scope)
    industries = []
    for row in scope.get("industries") or []:
        cue = _has_cue(text, row.get("cues") or [])
        if cue:
            industries.append({"id": row["id"], "label": row["label"], "matched": cue})
    hazards = []
    for row in scope.get("highEnergyHazards") or []:
        cue = _has_cue(text, row.get("cues") or [])
        if cue:
            hazards.append({"id": row["id"], "label": row["label"], "matched": cue})
    frameworks = []
    for row in scope.get("frameworkBodies") or []:
        cue = _has_cue(text, row.get("cues") or [])
        if cue:
            frameworks.append({"id": row["id"], "label": row["label"], "matched": cue})
    regulators = []
    for row in scope.get("regulators") or []:
        cue = _has_cue(text, row.get("cues") or [])
        if cue:
            regulators.append({"id": row["id"], "label": row["label"], "matched": cue})
    workplace = _has_cue(text, scope.get("workplaceCues") or [])
    excluded = _has_cue(text, scope.get("excludeCues") or [])
    emerging = _has_cue(text, scope.get("emergingTopics") or [])
    method_ids = set(scope.get("methodSources") or [])
    is_method = any(item["id"] in method_ids for item in frameworks)
    regulatory_words = (
        "circular", "regulation", "subsidiary legislation", "guideline", "guidelines",
        "stop-work", "stop work", "workplace safety and health act",
    )
    is_regulatory = bool(regulators) and any(
        re.search(rf"(?<!\w){re.escape(word)}(?!\w)" if " " not in word else re.escape(word), text)
        for word in regulatory_words
    )
    return {
        "place": place,
        "in_watched_place": place is not None,
        "industries": industries,
        "hazards": hazards,
        "frameworks": frameworks,
        "regulators": regulators,
        "workplace_cue": workplace,
        "exclude_cue": excluded,
        "emerging_topic": emerging,
        "is_workplace": workplace is not None or bool(industries) or bool(regulators),
        "is_method": is_method,
        "is_regulatory": is_regulatory,
    }


def _method_context_ok(classification: Mapping[str, Any]) -> bool:
    """Reject surname/acronym false positives (e.g. Hallowell mayor, HFACS name-drop in combat history)."""
    frameworks = classification.get("frameworks") or []
    if not frameworks:
        return False
    # A multi-word cue (Energy Wheel, Campbell Institute, Matthew Hallowell) is enough.
    if any(" " in str(row.get("matched") or "") for row in frameworks):
        return True
    # Single-token cues need nearby safety/workplace context from the classification itself.
    if classification.get("hazards") or classification.get("workplace_cue") or classification.get("emerging_topic"):
        return True
    if classification.get("industries") and classification.get("is_workplace"):
        return True
    return False


def candidate_for_reasoning(
    classification: Mapping[str, Any],
    *,
    section_id: str | None = None,
    title: str = "",
    body: str = "",
    url: str = "",
    publisher: str = "",
) -> tuple[bool, str]:
    """Wide prefilter for Assessment/Critic. Wider than the hard gate on method/emerging, not on foreign incidents."""
    from grit.wsh.bulletins import is_bulletin

    if classification.get("exclude_cue"):
        return False, f"EXCLUDED:{classification['exclude_cue']}"
    if is_bulletin(title, body, url, publisher):
        return True, "CANDIDATE_BULLETIN"
    if classification.get("is_method") and classification.get("frameworks") and _method_context_ok(classification):
        return True, "CANDIDATE_METHOD"
    if classification.get("is_regulatory") and classification.get("regulators"):
        singapore_regulators = {"mom", "wshc", "wsha", "scdf"}
        if any(row["id"] in singapore_regulators for row in classification.get("regulators") or []):
            return True, "CANDIDATE_REGULATORY"
        if classification.get("in_watched_place"):
            return True, "CANDIDATE_REGULATORY"
        return False, "REGULATORY_OUTSIDE_PLACE"
    if classification.get("in_watched_place") and classification.get("is_workplace"):
        return True, "CANDIDATE_SG_WORKPLACE"
    if classification.get("in_watched_place") and classification.get("hazards"):
        return True, "CANDIDATE_SG_HAZARD"
    if classification.get("in_watched_place") and classification.get("emerging_topic"):
        return True, "CANDIDATE_SG_EMERGING"
    # Do not admit foreign industry incidents here — Assessment should not spend budget on them.
    if section_id in {"LEADING", "SIF", "HFACS"} and classification.get("frameworks") and _method_context_ok(classification):
        return True, "CANDIDATE_FRAMEWORK"
    return False, "NOT_A_CANDIDATE"


def ste_relevance(classification: Mapping[str, Any], *, section_id: str | None, title: str = "", body: str = "") -> tuple[bool, str]:
    """Hard STE-context gate. Theme sightings alone are not enough."""
    from grit.wsh.bulletins import is_bulletin

    if classification.get("exclude_cue"):
        return False, f"EXCLUDED:{classification['exclude_cue']}"
    if is_bulletin(title, body):
        return True, "COUNCIL_BULLETIN"
    if classification.get("is_method") and classification.get("frameworks"):
        # Method papers may be global; implication is written for STE industries in Singapore.
        return True, "METHOD_PAPER"
    if classification.get("is_regulatory") and classification.get("regulators"):
        singapore_regulators = {"mom", "wshc", "wsha", "scdf"}
        if any(row["id"] in singapore_regulators for row in classification.get("regulators") or []):
            return True, "REGULATORY_SG"
        if classification.get("in_watched_place"):
            return True, "REGULATORY_IN_PLACE"
        return False, "REGULATORY_OUTSIDE_PLACE"

    if section_id == "SERIOUS" or (classification.get("hazards") and section_id in {None, "SERIOUS"}):
        if not classification.get("in_watched_place"):
            return False, "INCIDENT_OUTSIDE_PLACE"
        if not classification.get("is_workplace"):
            return False, "NOT_WORKPLACE"
        if not classification.get("industries"):
            return False, "NO_STE_INDUSTRY"
        if not classification.get("hazards"):
            # Aggregate WSH fatality stats with industry may still be useful; require workplace + industry + place.
            if not (classification.get("regulators") or classification.get("workplace_cue")):
                return False, "NO_HAZARD_OR_REGULATOR"
        return True, "STE_WORKPLACE_INCIDENT"

    if section_id in {"LEADING", "SIF", "HFACS"}:
        if not classification.get("frameworks"):
            return False, "NO_FRAMEWORK_BODY"
        return True, "FRAMEWORK"

    if section_id == "EMERGING":
        if not classification.get("emerging_topic") and not classification.get("is_regulatory"):
            return False, "NO_EMERGING_TOPIC"
        if not classification.get("in_watched_place") and not classification.get("regulators"):
            return False, "EMERGING_OUTSIDE_PLACE"
        if not classification.get("is_workplace") and not classification.get("regulators"):
            return False, "EMERGING_NOT_WORKPLACE"
        # Prefer industry link; allow MOM/WSHC heat-stress circulars without naming construction.
        if not classification.get("industries") and not classification.get("regulators"):
            return False, "EMERGING_NO_STE_LINK"
        return True, "EMERGING_STE"

    # No catch-all: a theme sighting or a stray industry word without a section contract is out of scope.
    return False, "OUT_OF_SCOPE"


def in_scope(classification: Mapping[str, Any], *, section_id: str | None = None, title: str = "", body: str = "") -> tuple[bool, str]:
    """Phase-1 keep rule with STE workplace relevance."""
    return ste_relevance(classification, section_id=section_id, title=title, body=body)


def section_for_signal(classification: Mapping[str, Any], theme_ids: list[str], scope: Mapping[str, Any], *, title: str = "", body: str = "") -> str | None:
    """Pick the digest section. Framework / hazard / emerging cues outweigh a bare theme sighting when they conflict."""
    from grit.wsh.bulletins import detect_series, is_enforcement_campaign

    by_theme = {row["themeId"]: row["id"] for row in scope.get("sections") or []}
    if is_enforcement_campaign(title, body):
        return "SERIOUS"
    series = detect_series(title, body)
    if series == "ALERT":
        return "SERIOUS"
    if classification.get("is_method") or classification.get("frameworks"):
        ids = {item["id"] for item in classification.get("frameworks") or []}
        if "hfacs" in ids:
            return "HFACS"
        if ids & {"campbell", "icmm"}:
            return "LEADING"
        if ids & {"csra", "eei", "nsc"} or classification.get("is_method"):
            return "SIF"
    if classification.get("emerging_topic") or (classification.get("is_regulatory") and classification.get("emerging_topic")):
        return "EMERGING"
    if classification.get("is_regulatory") and classification.get("emerging_topic"):
        return "EMERGING"
    if classification.get("hazards") and classification.get("is_workplace"):
        return "SERIOUS"
    if classification.get("is_regulatory"):
        return "EMERGING"
    if "WSH-BULLETIN-01" in theme_ids and series in {"ADVISORY", "INSIGHTS"}:
        return "EMERGING" if classification.get("emerging_topic") else "SERIOUS"
    for theme_id in theme_ids:
        if theme_id in by_theme:
            return by_theme[theme_id]
    return None


def match_alert(title: str, body: str, classification: Mapping[str, Any], patterns: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the first pack-local alert rule that hits, or None. Method papers and non-STE noise never alert."""
    if classification.get("is_method") or classification.get("exclude_cue"):
        return None
    section = "EMERGING" if classification.get("is_regulatory") else ("SERIOUS" if classification.get("hazards") else None)
    keep, _reason = ste_relevance(classification, section_id=section, title=title, body=body)
    if not keep:
        return None
    text = _text(title, body)
    for rule in patterns.get("patterns") or []:
        if rule.get("requiresPlace") and not classification.get("in_watched_place"):
            continue
        if rule.get("requiresIndustryOrHazard") and not (classification.get("industries") or classification.get("hazards")):
            continue
        if rule.get("requiresRegulator") and not classification.get("regulators"):
            continue
        if rule.get("kind") == "SERIOUS_INCIDENT" and not classification.get("is_workplace"):
            continue
        if rule.get("kind") == "SERIOUS_INCIDENT" and not classification.get("industries"):
            continue
        matched = None
        for phrase in rule.get("phrases") or []:
            if phrase.casefold() in text:
                matched = phrase
                break
        if not matched:
            continue
        sentence = next((part for part in re.split(r"(?<=[.!?])\s+", f"{title}. {body}") if matched.casefold() in part.casefold()), f"{title} {body}")
        if any(word.casefold() in sentence.casefold() for word in rule.get("excludeInSentence") or []):
            continue
        return {
            "rule_id": rule["ruleId"],
            "kind": rule.get("kind"),
            "label": rule.get("label"),
            "matched": matched,
            "reasons": [
                f"Matched '{matched}'",
                *([f"Place: {classification['place']}"] if classification.get("place") else []),
                *([f"Industry: {item['label']}"] for item in classification.get("industries") or []),
                *([f"Hazard: {item['label']}"] for item in classification.get("hazards") or []),
                *([f"Regulator: {item['label']}"] for item in classification.get("regulators") or []),
                *([f"Workplace: {classification['workplace_cue']}"] if classification.get("workplace_cue") else []),
            ],
        }
    return None
