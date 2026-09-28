"""One weekly pipeline: normalise, revise, rank, write the review delta.

Acquisition must already have emitted candidates. This module does not scrape,
and it does not call the harm scorer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from grit_scan.matchers.credibility_lookup import credibility_lookup
from grit_scan.matchers.exposure_matcher import exposure_entity_match
from grit_scan.matchers.lob_matcher import lob_match
from grit_scan.matchers.theme_matcher import theme_match
from grit_scan.ontology import OntologyPack
from grit_scan.relevance_scoring import (
    classify_risk_level,
    classify_trend,
    compose_business_relevance_basis,
)

_MOVEMENT = {
    "ESCALATING": "ESCALATING",
    "INCREASING": "ESCALATING",
    "WORSENING": "ESCALATING",
    "EASING": "EASING",
    "DECREASING": "EASING",
    "IMPROVING": "EASING",
    "NO_MATERIAL_CHANGE": "NO_MATERIAL_CHANGE",
    "STABLE": "NO_MATERIAL_CHANGE",
    "NO_CHANGE": "NO_MATERIAL_CHANGE",
}
_CONFIDENCE = {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "MED": "MEDIUM", "LOW": "LOW"}
_PLACEHOLDER = re.compile(r"example\.com|specific-source-url|placeholder", re.I)


def extract_json_block(raw_text: str) -> Any:
    open_bracket = raw_text.find("[")
    open_brace = raw_text.find("{")
    if open_bracket >= 0 and (open_brace < 0 or open_bracket <= open_brace):
        start = open_bracket
    elif open_brace >= 0:
        start = open_brace
    else:
        raise ValueError("No JSON array found in scan output")
    close_bracket = raw_text.rfind("]")
    close_brace = raw_text.rfind("}")
    end = close_bracket if close_bracket >= close_brace else close_brace
    if end < start:
        raise ValueError("No JSON array found in scan output")
    text = raw_text[start : end + 1].strip()
    if not text.startswith("["):
        text = "[" + text
    if not text.endswith("]"):
        text = text + "]"
    payload = json.loads(text)
    return payload if isinstance(payload, list) else [payload]


def import_scan(raw_text: str, baseline: dict[str, Any], *, revision_id: str, revision_date: str) -> dict[str, Any]:
    conditions = list(baseline.get("conditions") or [])
    accepted: list[dict[str, Any]] = []
    rejected: list[str] = []
    counters: dict[str, int] = {}
    for candidate in extract_json_block(raw_text):
        condition = _resolve_condition(str(candidate.get("conditionHint") or ""), conditions)
        if condition is None:
            rejected.append(f"unmatched conditionHint: '{candidate.get('conditionHint')}'")
            continue
        movement = _MOVEMENT.get(str(candidate.get("movement") or "").strip().upper())
        if movement is None:
            rejected.append(f"{condition['id']}: unrecognised movement '{candidate.get('movement')}'")
            continue
        confidence = _CONFIDENCE.get(str(candidate.get("evidenceConfidence") or "").strip().upper())
        if confidence is None:
            rejected.append(f"{condition['id']}: unrecognised evidenceConfidence '{candidate.get('evidenceConfidence')}'")
            continue
        summary = str(candidate.get("summary") or "")
        if not summary.strip():
            rejected.append(f"{condition['id']}: missing summary")
            continue
        raw_sources = list(candidate.get("sources") or [])
        valid = [source for source in raw_sources if _real_source(source)]
        if movement != "NO_MATERIAL_CHANGE" and not valid:
            rejected.append(f"{condition['id']}: no valid, specific source article for a reported change")
            continue
        source_urls = [str(source.get("url")) for source in valid]
        if not source_urls and raw_sources:
            source_urls = [str(source.get("url")) for source in raw_sources if source.get("url")]
        counters[condition["id"]] = counters.get(condition["id"], 0) + 1
        record = {
            "developmentId": f"DEV-{condition['id']}-{counters[condition['id']]:02d}",
            "conditionId": condition["id"],
            "movement": movement,
            "evidenceConfidence": confidence,
            "headline": str(candidate.get("headline") or summary),
            "summary": summary,
            "sources": source_urls,
        }
        descent = _import_descent(candidate.get("descentCandidate"), source_urls)
        if descent is not None:
            record["descent"] = descent
        accepted.append(record)
    return {
        "revisionId": revision_id,
        "revisionDate": revision_date,
        "developments": accepted,
        "rejected": rejected,
    }


def revise(document: dict[str, Any], pack: OntologyPack) -> dict[str, Any]:
    conditions = list(pack.baseline.get("conditions") or [])
    by_id = {condition["id"]: condition for condition in conditions}
    revisions: list[dict[str, Any]] = []
    for development in list(document.get("developments") or []):
        condition = by_id.get(development.get("conditionId"))
        if condition is None or not development.get("developmentId") or not development.get("summary"):
            continue
        if development.get("movement") not in {"ESCALATING", "EASING", "NO_MATERIAL_CHANGE"}:
            continue
        exposure = exposure_entity_match(
            f"{development.get('headline') or ''} {development.get('summary') or ''}",
            pack.exposure,
        )
        entities = exposure["matchedEntities"]
        descent_status, descent = _revise_descent(development, entities, pack)
        theme = theme_match(
            str(development.get("summary") or ""),
            pack.themes,
            list(development.get("steThemeHints") or []),
        )
        if theme["matchCount"]:
            themes = [item["themeName"] for item in theme["matchedThemes"]]
            classification = "PER_FINDING_KEYWORD_MATCH"
        else:
            themes = list(condition.get("steThemes") or [])
            classification = "CONDITION_DEFAULT_FALLBACK"
        revisions.append(
            {
                "developmentId": development["developmentId"],
                "conditionId": condition["id"],
                "condition": condition["name"],
                "domain": condition.get("domain"),
                "movement": development["movement"],
                "evidenceConfidence": development["evidenceConfidence"],
                "headline": development.get("headline") or development["summary"],
                "summary": development["summary"],
                "sources": list(development.get("sources") or []),
                "steThemes": themes,
                "steThemeClassification": classification,
                "exposureEntities": entities,
                "descentStatus": descent_status,
                "descent": descent,
            }
        )
    reviewed = {item["conditionId"] for item in revisions}
    return {
        "revisionId": document.get("revisionId"),
        "revisionDate": document.get("revisionDate"),
        "baselineId": pack.baseline.get("baselineId"),
        "baselineEffectiveDate": pack.baseline.get("effectiveDate"),
        "status": "REVISION_READY_FOR_NARRATIVE",
        "conditionCount": len(conditions),
        "developmentsReceived": len(revisions),
        "changedConditions": sum(item["movement"] != "NO_MATERIAL_CHANGE" for item in revisions),
        "supportedDescents": sum(item["descentStatus"] == "SUPPORTED" for item in revisions),
        "unreviewedConditionIds": [condition["id"] for condition in conditions if condition["id"] not in reviewed],
        "revisions": revisions,
    } | {
        key: pack.baseline[key]
        for key in ("deltaTitle", "posture", "idleLine")
        if pack.baseline.get(key)
    }


def rank(revision: dict[str, Any], pack: OntologyPack) -> dict[str, Any]:
    ranked = [_priority(item, pack) for item in revision.get("revisions") or [] if item.get("movement") != "NO_MATERIAL_CHANGE"]
    ranked.sort(key=lambda item: item["priorityScore"], reverse=True)
    return {
        "revisionId": revision.get("revisionId"),
        "revisionDate": revision.get("revisionDate"),
        "baselineId": revision.get("baselineId"),
        "rankingNote": (
            "priorityLabel is an indicative communication priority for briefing composition only; "
            "it is not a formal GRIT severity/likelihood rating."
        ),
        "rankedItems": ranked,
    }


def compose_delta(revision: dict[str, Any]) -> str:
    changed = [item for item in revision.get("revisions") or [] if item.get("movement") != "NO_MATERIAL_CHANGE"]
    supported = [item for item in revision.get("revisions") or [] if item.get("descentStatus") == "SUPPORTED"]
    unreviewed = list(revision.get("unreviewedConditionIds") or [])
    lines = [
        str(revision.get("deltaTitle") or "URS TAIWAN WEEKLY STRATEGIC DELTA"),
        "",
        f"Revision: {revision.get('revisionDate')}",
        f"Baseline: {revision.get('baselineId')}",
        "Status: Draft for senior-management review",
        "",
        "BOTTOM LINE",
    ]
    if not changed:
        lines.append(str(revision.get("idleLine") or "No material change was recorded against the standing Taiwan baseline."))
    else:
        lines.append(
            f"{len(changed)} baseline condition(s) moved during this revision. "
            "The changed conditions and their STE transmission paths are listed below."
        )
    if not supported:
        lines.append("No change has sufficient evidence to descend to a named business area, line of business or entity.")
    else:
        lines.append(
            f"{len(supported)} change(s) have sufficient evidence for entity-level follow-up; "
            "these are not assertions of loss or confirmed incident."
        )
    lines.extend(["", "CHANGED CONDITIONS"])
    if not changed:
        lines.append("- None recorded.")
    for item in changed:
        lines.append(f"- {item.get('condition')}: {item.get('movement')}. {item.get('summary')}")
        theme_note = (
            "classified from this finding"
            if item.get("steThemeClassification") == "PER_FINDING_KEYWORD_MATCH"
            else "condition default -- finding text matched no theme keywords"
        )
        lines.append(f"  STE themes ({theme_note}): {'; '.join(item.get('steThemes') or [])}.")
        lines.append(
            f"  Evidence confidence: {item.get('evidenceConfidence')}. Sources: {'; '.join(item.get('sources') or [])}"
        )
        if item.get("descentStatus") == "SUPPORTED" and item.get("descent"):
            descent = item["descent"]
            lines.append(
                f"  Descent: {descent.get('businessArea')} -> {descent.get('lineOfBusiness')} -> {descent.get('entityOrUnit')}."
            )
            lines.append(f"  Transmission: {descent.get('transmissionPath')}")
            lob = descent.get("lobMatch") or {}
            if lob:
                lines.append(
                    f"  Validated LOB: {lob.get('lobName')} [{lob.get('lobCode')}], "
                    f"owner {lob.get('owner')} (matched via {lob.get('matchMethod')})."
                )
        else:
            lines.append("  Descent: not justified by the supplied evidence.")
    lines.extend(
        [
            "",
            "REVIEW COVERAGE",
            f"Developments received: {revision.get('developmentsReceived')} of {revision.get('conditionCount')} baseline conditions.",
        ]
    )
    if not unreviewed:
        lines.append("All baseline conditions were reviewed.")
    else:
        lines.append(
            "Conditions without a submitted development record: "
            + ", ".join(unreviewed)
            + ". These require explicit review before a complete weekly conclusion is made."
        )
    lines.extend(
        [
            "",
            "MANAGEMENT POSTURE",
            str(
                revision.get("posture")
                or "Maintain Taiwan as an active strategic watchpoint. Treat entity-level action as evidence-led and do not infer a confirmed loss event from a changed global condition alone."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def run_weekly_pipeline(
    scan_text: str,
    pack: OntologyPack,
    output_dir: Path,
    *,
    revision_date: str,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    revision_input = import_scan(
        scan_text,
        pack.baseline,
        revision_id=f"{pack.baseline.get('revisionPrefix') or 'URS-TW'}-WEEKLY-{revision_date}",
        revision_date=revision_date,
    )
    revision = revise(revision_input, pack)
    ranking = rank(revision, pack)
    delta = compose_delta(revision)
    paths = {
        "revision_input": output_dir / "revision-input.json",
        "revision": output_dir / "revision.json",
        "ranking": output_dir / "ranking.json",
        "delta": output_dir / "weekly-delta.txt",
    }
    _write_json(paths["revision_input"], {key: revision_input[key] for key in ("revisionId", "revisionDate", "developments")})
    _write_json(paths["revision"], revision)
    _write_json(paths["ranking"], ranking)
    paths["delta"].write_text(delta, encoding="utf-8")
    return paths


def _priority(item: dict[str, Any], pack: OntologyPack) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    confidence = item.get("evidenceConfidence")
    if confidence == "HIGH":
        score += 2
        reasons.append("high evidence confidence (+2)")
    elif confidence == "MEDIUM":
        score += 1
        reasons.append("medium evidence confidence (+1)")
    else:
        reasons.append("low evidence confidence (+0)")
    if item.get("movement") == "ESCALATING":
        score += 1
        reasons.append("escalating movement (+1)")
    elif item.get("movement") == "EASING":
        score -= 1
        reasons.append("easing movement (-1)")
    themes = list(item.get("steThemes") or [])
    if "Taiwan business concentration" in themes:
        score += 2
        reasons.append("hits 'Taiwan business concentration' (+2)")
    theme_bonus = min(2, max(0, len(themes) - 1))
    if theme_bonus:
        score += theme_bonus
        reasons.append(f"touches {len(themes)} STE themes (+{theme_bonus})")
    if item.get("descentStatus") == "SUPPORTED":
        score += 2
        reasons.append("evidenced entity-level transmission path (+2)")
    checks = [credibility_lookup(url, pack.source_register) for url in list(item.get("sources") or [])]
    label = "HIGH" if score >= 5 else "MEDIUM" if score >= 3 else "WATCHPOINT"
    ranked_item = {
        "developmentId": item.get("developmentId"),
        "conditionId": item.get("conditionId"),
        "condition": item.get("condition"),
        "domain": item.get("domain"),
        "movement": item.get("movement"),
        "evidenceConfidence": confidence,
        "priorityScore": score,
        "priorityLabel": label,
        "priorityRationale": "; ".join(reasons),
        "headline": item.get("headline") or item.get("summary"),
        "summary": item.get("summary"),
        "sources": list(item.get("sources") or []),
        "sourceCredibility": checks,
        "hasActiveRatedSource": any(check["active"] and check["credibilityRating"] in {"HIGH", "MEDIUM"} for check in checks),
        "unratedSources": [check["url"] for check in checks if check["credibilityRating"] == "UNRATED"],
        "steThemes": themes,
        "descentStatus": item.get("descentStatus"),
        "descent": item.get("descent"),
        "exposureEntities": item.get("exposureEntities"),
    }
    ranked_item["riskLevel"] = classify_risk_level(ranked_item)
    ranked_item["trend"] = classify_trend(ranked_item["movement"])
    ranked_item["businessRelevanceBasis"] = compose_business_relevance_basis(ranked_item)
    return ranked_item


def _resolve_condition(hint: str, conditions: list[dict[str, Any]]) -> dict[str, Any] | None:
    hint = hint.strip()
    if not hint:
        return None
    exact = [condition for condition in conditions if condition.get("name") == hint or condition.get("id") == hint]
    if exact:
        return exact[0]
    normalised_hint = _condition_text(hint)
    normalised = [condition for condition in conditions if _condition_text(condition.get("name")) == normalised_hint]
    if len(normalised) == 1:
        return normalised[0]
    domain = [condition for condition in conditions if condition.get("domain") == hint]
    if len(domain) == 1:
        return domain[0]
    partial = [
        condition
        for condition in conditions
        if normalised_hint
        and (
            normalised_hint in _condition_text(condition.get("name"))
            or _condition_text(condition.get("name")) in normalised_hint
        )
    ]
    return partial[0] if len(partial) == 1 else None


def _condition_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"['’]s\b", "", text)
    text = re.sub(r"['’,.]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _real_source(source: dict[str, Any]) -> bool:
    url = str(source.get("url") or "")
    if not url.strip() or _PLACEHOLDER.search(url):
        return False
    path = url.split("://", 1)[-1].split("/", 1)[-1].strip("/") if "://" in url else ""
    return len(path) >= 3


def _import_descent(raw: Any, source_urls: list[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not source_urls:
        return None
    fields = ("businessArea", "lineOfBusiness", "entityOrUnit", "transmissionPath")
    if any(not str(raw.get(field) or "").strip() for field in fields):
        return None
    return {field: raw[field] for field in fields} | {"sources": source_urls}


def _revise_descent(development: dict[str, Any], entities: list[dict[str, Any]], pack: OntologyPack) -> tuple[str, dict[str, Any] | None]:
    raw = development.get("descent")
    if not isinstance(raw, dict):
        return "NOT_JUSTIFIED", None
    fields = ("businessArea", "lineOfBusiness", "entityOrUnit", "transmissionPath")
    complete = all(str(raw.get(field) or "").strip() for field in fields) and list(raw.get("sources") or [])
    lob_candidate = str(raw.get("lineOfBusiness") or raw.get("businessArea") or "")
    matched = lob_match(lob_candidate, pack.lob_profiles) if complete else {"matched": False}
    if complete and development.get("evidenceConfidence") == "HIGH" and matched.get("matched"):
        lob = matched["lob"]
        return "SUPPORTED", {
            "businessArea": raw.get("businessArea"),
            "lineOfBusiness": raw.get("lineOfBusiness"),
            "entityOrUnit": raw.get("entityOrUnit"),
            "transmissionPath": _transmission_summary(entities),
            "sources": list(raw.get("sources") or []),
            "lobMatch": {
                "lobId": lob.get("lobId"),
                "lobCode": lob.get("lobCode"),
                "lobName": lob.get("lobName"),
                "baName": lob.get("baName"),
                "owner": lob.get("owner"),
                "matchMethod": matched.get("matchMethod"),
            },
        }
    return "INSUFFICIENT_EVIDENCE", None


def _transmission_summary(entities: list[dict[str, Any]]) -> str | None:
    if not entities:
        return None
    names = list(dict.fromkeys(entity["entityName"] for entity in entities))
    edges: list[str] = []
    seen: set[str] = set()
    for entity in entities:
        for link in entity.get("linkedExposure") or []:
            key = "|".join(sorted((entity["entityId"], link["entityId"])))
            if key not in seen:
                seen.add(key)
                edges.append(f"{entity['entityName']} -> {link['entityName']}")
    summary = "Bound exposure: " + ", ".join(names)
    if edges:
        summary += ". Links: " + "; ".join(edges)
    return summary


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
