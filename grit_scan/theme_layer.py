"""Theme and event layer. These functions propose or validate. They do not write."""

from __future__ import annotations

from typing import Any

from grit_scan.canonical import canonical_hash
from grit_scan.matchers.text_norm import keyword_in_text, normalise_theme

ESCALATION_NAME = "ThemeEscalationAssessment"
ESCALATION_VERSION = "1.0.0"
METRIC_NAME = "ThemeMetricCalculation"
METRIC_VERSION = "1.0.0"
GATE_NAME = "RegisterWriteGate"
GATE_VERSION = "1.0.0"
THEME_MATCH_NAME = "ThemeMatch"
EVENT_MATCH_NAME = "EventMatch"

THEME_STATUSES = ("PROPOSED", "ACTIVE", "ESCALATING", "STABLE", "DECLINING", "CLOSED", "REOPENED")
THEME_TYPES = ("STANDING_RISK", "EMERGING_THEME", "MANAGEMENT_REQUESTED", "WATCH_THEME")
EDGES = {
    "PROPOSED": {"PROPOSED", "ACTIVE", "CLOSED"},
    "ACTIVE": {"ACTIVE", "ESCALATING", "STABLE", "DECLINING", "CLOSED"},
    "ESCALATING": {"ESCALATING", "STABLE", "DECLINING"},
    "STABLE": {"STABLE", "ESCALATING", "DECLINING"},
    "DECLINING": {"DECLINING", "STABLE", "ESCALATING", "CLOSED"},
    "CLOSED": {"CLOSED", "REOPENED"},
    "REOPENED": {"REOPENED", "ACTIVE", "ESCALATING", "STABLE", "DECLINING", "CLOSED"},
}
VELOCITY_RANK = {"HIGH": 0, "MODERATE": 1, "LOW": 2, "NONE": 3}
_FORBIDDEN_RECORD_KEYS = {
    "workflow_state",
    "signal_transition",
    "final_controlled_score",
    "final_controlled_band",
    "tone",
    "goldstein",
}
_REQUIRED_MUTATION = (
    "mutation_type",
    "actor_type",
    "actor_authority",
    "configuration_version",
    "execution_id",
    "idempotency_key",
    "proposed_record",
    "content_hash",
)


def theme_metric_calculation(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Count governed fields on accepted events. Do not read prose as a metric."""
    events = snapshot.get("accepted_events")
    if not isinstance(events, list):
        return _metric_gap(snapshot, "ACCEPTED_EVENTS_ABSENT")
    accepted = [event for event in events if isinstance(event, dict) and event.get("accepted") is True]
    lobs: set[str] = set()
    geographies: set[str] = set()
    sources: set[str] = set()
    regulatory = 0
    for event in accepted:
        lobs.update(str(item) for item in event.get("lob_ids") or [] if str(item).strip())
        geographies.update(str(item) for item in event.get("geography_ids") or [] if str(item).strip())
        sources.update(str(item) for item in event.get("source_ids") or [] if str(item).strip())
        if event.get("regulatory") is True:
            regulatory += 1
    current = len(accepted)
    prior = snapshot.get("prior_event_count")
    if isinstance(prior, bool) or not isinstance(prior, int) or prior < 0:
        growth = None
        growth_reason = "PRIOR_BASELINE_ABSENT"
    elif prior == 0 and current > 0:
        growth = None
        growth_reason = "PRIOR_BASELINE_ABSENT"
    elif prior == 0:
        growth = 0.0
        growth_reason = "NO_CHANGE"
    else:
        growth = (current - prior) / prior
        growth_reason = "CALCULATED"
    activity = snapshot.get("standing_risk_activity")
    if isinstance(activity, bool) or not isinstance(activity, (int, float)):
        activity = None
    metrics = {
        "theme_id": snapshot.get("theme_id"),
        "theme_version": snapshot.get("theme_version"),
        "supporting_event_count": current,
        "event_growth_rate": growth,
        "growth_reason": growth_reason,
        "affected_lob_count": len(lobs),
        "geography_count": len(geographies),
        "source_diversity": len(sources),
        "regulatory_activity": regulatory,
        "standing_risk_activity": activity,
        "metric_period_start_utc": snapshot.get("metric_period_start_utc"),
        "metric_period_end_utc": snapshot.get("metric_period_end_utc"),
        "configuration_version": snapshot.get("configuration_version"),
        "function_version": METRIC_VERSION,
    }
    metrics["metric_snapshot_hash"] = canonical_hash(
        {key: value for key, value in metrics.items() if key != "metric_snapshot_hash"}
    )
    return metrics


def theme_escalation_assessment(context: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one supplied metric snapshot. No reads, writes, or agent calls."""
    input_hash = canonical_hash(_escalation_input(context))
    problem = _escalation_problem(context, configuration)
    if problem is not None:
        return _escalation_result(
            context,
            input_hash,
            direction="INSUFFICIENT_EVIDENCE",
            velocity="NONE",
            recommendation="INSUFFICIENT_EVIDENCE",
            proposal=context.get("current_lifecycle_state") if context.get("current_lifecycle_state") in THEME_STATUSES else None,
            reason_code=problem,
            write_instruction="NONE",
            configuration=configuration,
        )
    current = context["current_lifecycle_state"]
    if current == "CLOSED":
        return _closed_result(context, configuration, input_hash)
    if current == "PROPOSED":
        return _proposed_result(context, configuration, input_hash)
    if not _evidence_gate(context, configuration):
        return _escalation_result(
            context,
            input_hash,
            direction="INSUFFICIENT_EVIDENCE",
            velocity="NONE",
            recommendation="INSUFFICIENT_EVIDENCE",
            proposal=current,
            reason_code="EVIDENCE_GATE_FAILED",
            write_instruction="NONE",
            configuration=configuration,
        )
    score = _movement_score(context, configuration)
    if score is None:
        return _escalation_result(
            context,
            input_hash,
            direction="INSUFFICIENT_EVIDENCE",
            velocity="NONE",
            recommendation="INSUFFICIENT_EVIDENCE",
            proposal=current,
            reason_code="MOVEMENT_WEIGHT_ABSENT",
            write_instruction="NONE",
            configuration=configuration,
        )
    direction = _direction(score, configuration)
    velocity = _velocity(score, configuration)
    recommendation, proposal, reason = _map_movement(context, direction)
    if proposal not in EDGES[current]:
        return _escalation_result(
            context,
            input_hash,
            direction="INSUFFICIENT_EVIDENCE",
            velocity="NONE",
            recommendation="INSUFFICIENT_EVIDENCE",
            proposal=current,
            reason_code="ILLEGAL_LIFECYCLE_EDGE",
            write_instruction="NONE",
            configuration=configuration,
            score=score,
        )
    return _escalation_result(
        context,
        input_hash,
        direction=direction,
        velocity=velocity,
        recommendation=recommendation,
        proposal=proposal,
        reason_code=reason,
        write_instruction="PROPOSE",
        configuration=configuration,
        score=score,
    )


def event_match(signal: dict[str, Any], events: list[dict[str, Any]], configuration: dict[str, Any]) -> dict[str, Any]:
    """Return event candidates. Title similarity alone is never a match."""
    candidates: list[dict[str, Any]] = []
    title_only = False
    signal_title = normalise_theme(str(signal.get("title") or ""))
    for event in events:
        shared_entities = _overlap(signal.get("entity_ids"), event.get("entity_ids"))
        shared_geographies = _overlap(signal.get("geography_ids"), event.get("geography_ids"))
        shared_evidence = _overlap(signal.get("evidence_references"), event.get("evidence_references"))
        titles_similar = bool(signal_title) and signal_title == normalise_theme(str(event.get("title") or ""))
        if not shared_entities and not shared_geographies and not shared_evidence:
            title_only = title_only or titles_similar
            continue
        method = "ENTITY_OVERLAP" if shared_entities else "GEOGRAPHY_OVERLAP" if shared_geographies else "EVIDENCE_OVERLAP"
        confidence = (configuration.get("confidence") or {}).get(method)
        minimum = configuration.get("minimum_confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not isinstance(minimum, (int, float)):
            continue
        if confidence < minimum:
            continue
        candidates.append(
            {
                "event_id": event.get("event_id"),
                "match_method": method,
                "match_confidence": confidence,
                "authoritative_identity_created": False,
            }
        )
    return {
        "function_name": EVENT_MATCH_NAME,
        "candidates": candidates,
        "match_count": len(candidates),
        "reason_code": "CANDIDATES" if candidates else "TITLE_SIMILARITY_INSUFFICIENT" if title_only else "NO_MATCH",
        "authoritative_identity_created": False,
    }


def theme_register_match(event: dict[str, Any], themes: list[dict[str, Any]], configuration: dict[str, Any]) -> dict[str, Any]:
    """Return theme candidates. This does not create a theme."""
    body = normalise_theme(" ".join([str(event.get("summary") or ""), *[str(item) for item in event.get("evidence_references") or []]]))
    title = normalise_theme(str(event.get("title") or ""))
    candidates: list[dict[str, Any]] = []
    title_only = False
    minimum = configuration.get("minimum_confidence")
    for theme in themes:
        if theme.get("theme_status") == "CLOSED":
            continue
        request_id = str(event.get("management_request_id") or "")
        if event.get("management_request_active") is True and request_id and request_id == str(theme.get("management_request_id") or ""):
            candidates.append(_theme_candidate(theme, "MANAGEMENT_REQUEST", configuration))
            continue
        keywords = [*(theme.get("keywords") or []), theme.get("theme_name")]
        body_hit = any(keyword_in_text(body, normalise_theme(str(keyword))) for keyword in keywords if str(keyword or "").strip())
        title_hit = any(keyword_in_text(title, normalise_theme(str(keyword))) for keyword in keywords if str(keyword or "").strip())
        if body_hit:
            candidate = _theme_candidate(theme, "KEYWORD_MATCH", configuration)
            if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and candidate["match_confidence"] >= minimum:
                candidates.append(candidate)
            continue
        title_only = title_only or title_hit
    if candidates:
        return _theme_match_result(candidates, "CANDIDATES", emerging=None)
    criteria = event.get("emergence_criteria") if isinstance(event.get("emergence_criteria"), dict) else {}
    if _emergence_met(criteria, configuration):
        return _theme_match_result(
            [],
            "EMERGING_THEME_PROPOSED",
            emerging={
                "theme_type": "EMERGING_THEME",
                "theme_status": "PROPOSED",
                "match_method": "EMERGENCE_CRITERIA",
                "authoritative_identity_created": False,
            },
        )
    reason = "TITLE_SIMILARITY_INSUFFICIENT" if title_only else "INSUFFICIENT_EVIDENCE"
    return _theme_match_result([], reason, emerging=None)


def register_write_gate(mutation: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate one typed register mutation. No writes."""
    record = mutation.get("proposed_record") if isinstance(mutation.get("proposed_record"), dict) else {}
    content_hash = canonical_hash(record) if record else ""
    input_hash = canonical_hash(
        {
            "mutation_type": mutation.get("mutation_type"),
            "idempotency_key": mutation.get("idempotency_key"),
            "content_hash": mutation.get("content_hash"),
            "actor_authority": mutation.get("actor_authority"),
            "configuration_version": mutation.get("configuration_version"),
        }
    )
    missing = [field for field in _REQUIRED_MUTATION if mutation.get(field) is None or mutation.get(field) == ""]
    if missing or not record:
        return _gate(mutation, input_hash, "DENY", "MISSING_INPUT")
    if _FORBIDDEN_RECORD_KEYS.intersection(record) or mutation.get("workflow_state") or mutation.get("signal_transition"):
        return _gate(mutation, input_hash, "DENY", "NOT_A_SIGNAL_TRANSITION")
    if mutation.get("content_hash") != content_hash:
        return _gate(mutation, input_hash, "DENY", "CONTENT_HASH_MISMATCH")
    existing = (snapshot.get("mutations") or {}).get(mutation.get("idempotency_key"))
    if existing is not None:
        if existing == content_hash:
            return _gate(mutation, input_hash, "ALREADY_APPLIED", "IDEMPOTENT_REPLAY")
        return _gate(mutation, input_hash, "DENY", "IDEMPOTENCY_CONFLICT")
    problem = _policy_problem(mutation, record, snapshot)
    if problem is not None:
        return _gate(mutation, input_hash, "DENY", problem)
    return _gate(mutation, input_hash, "ALLOW", "REGISTER_MUTATION_VALID")


def horizon_pack_sections(
    themes: list[dict[str, Any]],
    *,
    package_approved: bool,
    regulatory_attention_threshold: float,
) -> dict[str, Any]:
    """Build the pack sections. An empty section stays visible."""
    standing = [theme for theme in themes if theme.get("theme_type") == "STANDING_RISK"]
    emerging = [
        theme
        for theme in themes
        if theme.get("theme_status") == "PROPOSED" or theme.get("newly_active") is True
    ]
    escalating = sorted(
        (theme for theme in themes if theme.get("theme_status") == "ESCALATING"),
        key=lambda theme: (VELOCITY_RANK.get(str(theme.get("velocity")), 9), str(theme.get("theme_id"))),
    )
    attention = [theme for theme in themes if _needs_attention(theme, regulatory_attention_threshold)]
    declining = [theme for theme in themes if theme.get("theme_status") == "DECLINING"]
    closed = [theme for theme in themes if theme.get("theme_status") == "CLOSED"]
    return {
        "publication_label": "FACT" if package_approved else "PROPOSAL",
        "standing_risks": _section(standing),
        "top_emerging_themes": _section(emerging),
        "fastest_escalating_themes": _section(escalating),
        "themes_requiring_management_attention": _section(attention),
        "declining_themes": _section(declining),
        "closed_themes": _section(closed),
    }


def _escalation_problem(context: dict[str, Any], configuration: dict[str, Any]) -> str | None:
    if context.get("function_version") != ESCALATION_VERSION:
        return "FUNCTION_VERSION_MISMATCH"
    if context.get("theme_metric_calculation_version") != METRIC_VERSION:
        return "METRIC_VERSION_MISMATCH"
    if context.get("current_lifecycle_state") not in THEME_STATUSES:
        return "UNKNOWN_LIFECYCLE_STATE"
    if context.get("theme_type") not in THEME_TYPES:
        return "THEME_TYPE_ABSENT"
    if not configuration or configuration.get("version") != context.get("configuration_version"):
        return "CONFIGURATION_ABSENT"
    period_start = str(context.get("metric_period_start_utc") or "")
    period_end = str(context.get("metric_period_end_utc") or "")
    if not period_start or not period_end or period_end < period_start:
        return "METRIC_PERIOD_INVALID"
    required = (
        "supporting_event_count",
        "event_growth_rate",
        "affected_lob_count",
        "geography_count",
        "source_diversity",
        "regulatory_activity",
    )
    for field in required:
        if _non_negative(context.get(field)) is None and field != "event_growth_rate":
            return "METRIC_ABSENT"
        if field == "event_growth_rate" and _growth(context.get(field)) is None:
            return "METRIC_ABSENT"
    if context.get("theme_type") == "STANDING_RISK" and _non_negative(context.get("standing_risk_activity")) is None:
        return "STANDING_RISK_ACTIVITY_ABSENT"
    return None


def _evidence_gate(context: dict[str, Any], configuration: dict[str, Any]) -> bool:
    events = _non_negative(context.get("supporting_event_count"))
    diversity = _non_negative(context.get("source_diversity"))
    minimum_events = _non_negative(configuration.get("minimum_event_count"))
    minimum_diversity = _non_negative(configuration.get("minimum_source_diversity"))
    if None in {events, diversity, minimum_events, minimum_diversity}:
        return False
    return events >= minimum_events and diversity >= minimum_diversity


def _closed_result(context: dict[str, Any], configuration: dict[str, Any], input_hash: str) -> dict[str, Any]:
    reopen_minimum = _non_negative(configuration.get("reopen_minimum_events"))
    events = _non_negative(context.get("supporting_event_count"))
    if reopen_minimum is None or events is None or events < reopen_minimum or not _evidence_gate(context, configuration):
        return _escalation_result(
            context,
            input_hash,
            direction="INSUFFICIENT_EVIDENCE",
            velocity="NONE",
            recommendation="INSUFFICIENT_EVIDENCE",
            proposal="CLOSED",
            reason_code="CLOSED_REOPEN_EVIDENCE_ABSENT",
            write_instruction="NONE",
            configuration=configuration,
        )
    return _escalation_result(
        context,
        input_hash,
        direction="INCREASING",
        velocity="LOW",
        recommendation="REOPEN_CANDIDATE",
        proposal="REOPENED",
        reason_code="REOPEN_EVIDENCE_MET",
        write_instruction="PROPOSE",
        configuration=configuration,
    )


def _proposed_result(context: dict[str, Any], configuration: dict[str, Any], input_hash: str) -> dict[str, Any]:
    activate_minimum = _non_negative(configuration.get("activate_minimum_events"))
    events = _non_negative(context.get("supporting_event_count"))
    if activate_minimum is None or events is None or events < activate_minimum or not _evidence_gate(context, configuration):
        return _escalation_result(
            context,
            input_hash,
            direction="INSUFFICIENT_EVIDENCE",
            velocity="NONE",
            recommendation="INSUFFICIENT_EVIDENCE",
            proposal="PROPOSED",
            reason_code="ACTIVATION_EVIDENCE_ABSENT",
            write_instruction="NONE",
            configuration=configuration,
        )
    return _escalation_result(
        context,
        input_hash,
        direction="STABLE",
        velocity="LOW",
        recommendation="MAINTAIN_ACTIVE_MONITORING",
        proposal="ACTIVE",
        reason_code="ACTIVATION_EVIDENCE_MET",
        write_instruction="PROPOSE",
        configuration=configuration,
    )


def _map_movement(context: dict[str, Any], direction: str) -> tuple[str, str, str]:
    current = context["current_lifecycle_state"]
    theme_type = context["theme_type"]
    if direction == "INCREASING":
        return "ESCALATE", "ESCALATING", "MOVEMENT_INCREASING"
    if direction == "DECREASING":
        if current == "DECLINING" and theme_type != "STANDING_RISK":
            return "CLOSE_CANDIDATE", "CLOSED", "SUSTAINED_DECLINE"
        return "DE_ESCALATE", "DECLINING", "MOVEMENT_DECREASING"
    return "MAINTAIN_ACTIVE_MONITORING", "STABLE" if current != "REOPENED" else "REOPENED", "MOVEMENT_STABLE"


def _movement_score(context: dict[str, Any], configuration: dict[str, Any]) -> float | None:
    weights = configuration.get("weights")
    normalisers = configuration.get("normalisers")
    if not isinstance(weights, dict) or not isinstance(normalisers, dict):
        return None
    parts = {
        "event_growth_rate": _clamp(_growth(context.get("event_growth_rate")) or 0, -1, 1),
        "affected_lob_count": _unit(context.get("affected_lob_count"), normalisers.get("affected_lob_count")),
        "geography_count": _unit(context.get("geography_count"), normalisers.get("geography_count")),
        "source_diversity": _unit(context.get("source_diversity"), normalisers.get("source_diversity")),
        "regulatory_activity": _unit(context.get("regulatory_activity"), normalisers.get("regulatory_activity")),
    }
    if any(part is None for part in parts.values()) or any(weights.get(key) is None for key in parts):
        return None
    score = sum(float(weights[key]) * float(parts[key]) for key in parts)
    if context.get("theme_type") != "STANDING_RISK":
        return score
    weight = weights.get("standing_risk_activity")
    unit = _unit(context.get("standing_risk_activity"), normalisers.get("standing_risk_activity"))
    if weight is None or unit is None:
        return None
    return score + float(weight) * float(unit)


def _policy_problem(mutation: dict[str, Any], record: dict[str, Any], snapshot: dict[str, Any]) -> str | None:
    mutation_type = mutation.get("mutation_type")
    authority = mutation.get("actor_authority")
    actor = mutation.get("actor_type")
    if mutation_type in {"SEED", "RESEED", "PROMOTE_STANDING"}:
        if actor == "AGENT" or authority != "ANNUAL_REPORT_GOVERNANCE":
            return "AGENT_CANNOT_CHANGE_STANDING_RISK"
        if not mutation.get("human_approval_reference"):
            return "APPROVAL_REQUIRED"
        if record.get("theme_type") != "STANDING_RISK":
            return "STANDING_RISK_TYPE_REQUIRED"
    if mutation_type == "SEED":
        if record.get("theme_status") != "ACTIVE":
            return "SEED_STATUS_INVALID"
        if not all(record.get(field) for field in ("theme_name", "theme_description", "source_document", "register_version", "approval_reference")):
            return "SEED_PROVENANCE_ABSENT"
        return None
    if mutation_type == "RESEED":
        if record.get("supersedes_theme_version") not in (snapshot.get("theme_ids") or set()):
            return "LOOKUP_MISSING"
        if record.get("delete_relationships") is True:
            return "RELATIONSHIP_DELETION_FORBIDDEN"
        return None
    if mutation_type == "PROMOTE_STANDING":
        if record.get("supersedes_theme_version") not in (snapshot.get("theme_ids") or set()):
            return "LOOKUP_MISSING"
        return None
    if mutation_type == "PROPOSE_EMERGING":
        if record.get("theme_type") != "EMERGING_THEME" or record.get("theme_status") != "PROPOSED":
            return "EMERGING_STATUS_INVALID"
        if record.get("match_method") == "TITLE_SIMILARITY":
            return "TITLE_SIMILARITY_INSUFFICIENT"
        return None
    if mutation_type == "ACCEPT_EVENT":
        if not record.get("evidence_references") or not record.get("event_summary"):
            return "EVENT_EVIDENCE_ABSENT"
        return None
    if mutation_type == "ATTACH":
        if record.get("theme_id") not in (snapshot.get("theme_ids") or set()) or record.get("event_id") not in (snapshot.get("event_ids") or set()):
            return "LOOKUP_MISSING"
        if record.get("match_method") in {None, "TITLE_SIMILARITY"}:
            return "TITLE_SIMILARITY_INSUFFICIENT"
        if record.get("theme_definition_change") is True:
            return "STANDING_DEFINITION_UNCHANGED"
        return None
    if mutation_type in {"SET_LIFECYCLE", "CLOSE"}:
        theme_id = record.get("theme_id")
        current = (snapshot.get("theme_status") or {}).get(theme_id)
        proposed = record.get("theme_status")
        if theme_id not in (snapshot.get("theme_ids") or set()) or current not in THEME_STATUSES or proposed not in THEME_STATUSES:
            return "LOOKUP_MISSING"
        if proposed not in EDGES[current]:
            return "ILLEGAL_LIFECYCLE_EDGE"
        theme_type = (snapshot.get("theme_type") or {}).get(theme_id)
        if record.get("theme_type") not in {None, theme_type}:
            return "THEME_TYPE_CHANGE_FORBIDDEN"
        if theme_type == "STANDING_RISK" and proposed == "CLOSED" and authority != "ANNUAL_REPORT_GOVERNANCE":
            return "STANDING_RISK_NOT_CLOSED_BY_METRICS"
        if record.get("escalation_recommendation") in {"ESCALATE", "CLOSE_CANDIDATE", "REOPEN_CANDIDATE"} and not mutation.get("human_approval_reference"):
            return "APPROVAL_REQUIRED"
        return None
    if mutation_type == "MERGE":
        if record.get("match_method") == "TITLE_SIMILARITY":
            return "TITLE_SIMILARITY_INSUFFICIENT"
        if record.get("source_theme_id") not in (snapshot.get("theme_ids") or set()) or record.get("target_theme_id") not in (snapshot.get("theme_ids") or set()):
            return "LOOKUP_MISSING"
        if not mutation.get("human_approval_reference"):
            return "APPROVAL_REQUIRED"
        return None
    return "MUTATION_TYPE_UNKNOWN"


def _emergence_met(criteria: dict[str, Any], configuration: dict[str, Any]) -> bool:
    if criteria.get("requested") is not True:
        return False
    minimum_references = configuration.get("emergence_minimum_references", 2)
    minimum_outlets = configuration.get("emergence_minimum_outlets", 2)
    references = criteria.get("evidence_reference_count")
    outlets = criteria.get("distinct_outlets")
    if not isinstance(references, int) or not isinstance(outlets, int):
        return False
    return references >= minimum_references and outlets >= minimum_outlets


def _needs_attention(theme: dict[str, Any], regulatory_threshold: float) -> bool:
    if theme.get("evidence_coverage") == "INSUFFICIENT_EVIDENCE":
        return False
    if theme.get("escalation_recommendation") == "ESCALATE":
        return True
    regulatory = theme.get("regulatory_activity")
    return isinstance(regulatory, (int, float)) and not isinstance(regulatory, bool) and regulatory >= regulatory_threshold


def _section(themes: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for theme in themes:
        event_count = theme.get("supporting_event_count")
        entries.append(
            {
                "theme_id": theme.get("theme_id"),
                "theme_type": theme.get("theme_type"),
                "theme_status": theme.get("theme_status"),
                "direction_of_travel": theme.get("direction_of_travel"),
                "velocity": theme.get("velocity"),
                "citations": list(theme.get("event_ids") or []),
                "absence": "NO_SUPPORTING_EVENTS" if event_count == 0 else None,
            }
        )
    return {"entries": entries, "absence": None if entries else "NONE_IN_PERIOD"}


def _theme_candidate(theme: dict[str, Any], method: str, configuration: dict[str, Any]) -> dict[str, Any]:
    confidence = (configuration.get("confidence") or {}).get(method)
    return {
        "theme_id": theme.get("theme_id"),
        "theme_type": theme.get("theme_type"),
        "theme_status": theme.get("theme_status"),
        "match_method": method,
        "match_confidence": confidence,
        "authoritative_identity_created": False,
    }


def _theme_match_result(candidates: list[dict[str, Any]], reason: str, *, emerging: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "function_name": THEME_MATCH_NAME,
        "candidates": candidates,
        "match_count": len(candidates),
        "reason_code": reason,
        "emerging_theme_proposal": emerging,
        "authoritative_identity_created": False,
    }


def _escalation_result(
    context: dict[str, Any],
    input_hash: str,
    *,
    direction: str,
    velocity: str,
    recommendation: str,
    proposal: str | None,
    reason_code: str,
    write_instruction: str,
    configuration: dict[str, Any],
    score: float | None = None,
) -> dict[str, Any]:
    decision = {
        "function_name": ESCALATION_NAME,
        "function_version": ESCALATION_VERSION,
        "theme_id": context.get("theme_id"),
        "theme_version": context.get("theme_version"),
        "theme_type": context.get("theme_type"),
        "configuration_version": context.get("configuration_version"),
        "direction_of_travel": direction,
        "velocity": velocity,
        "escalation_recommendation": recommendation,
        "lifecycle_state_proposal": proposal,
        "reason_code": reason_code,
        "write_instruction": write_instruction,
        "movement_score": score,
    }
    decision["canonical_result_hash"] = canonical_hash(decision)
    decision["input_hash"] = input_hash
    decision["metric_snapshot_hash"] = context.get("metric_snapshot_hash")
    decision["idempotency_key"] = context.get("idempotency_key")
    return decision


def _escalation_input(context: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "theme_id",
        "theme_version",
        "theme_type",
        "current_lifecycle_state",
        "supporting_event_count",
        "event_growth_rate",
        "standing_risk_activity",
        "affected_lob_count",
        "geography_count",
        "source_diversity",
        "regulatory_activity",
        "metric_period_start_utc",
        "metric_period_end_utc",
        "configuration_version",
        "theme_metric_calculation_version",
        "function_version",
        "idempotency_key",
        "metric_snapshot_hash",
    )
    return {field: context.get(field) for field in fields}


def _gate(mutation: dict[str, Any], input_hash: str, decision: str, reason_code: str) -> dict[str, Any]:
    result = {
        "function_name": GATE_NAME,
        "function_version": GATE_VERSION,
        "decision": decision,
        "reason_code": reason_code,
        "mutation_type": mutation.get("mutation_type"),
        "idempotency_key": mutation.get("idempotency_key"),
        "workflow_rows_written": 0,
    }
    result["canonical_result_hash"] = canonical_hash(result)
    result["input_hash"] = input_hash
    return result


def _metric_gap(snapshot: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "function_name": METRIC_NAME,
        "function_version": METRIC_VERSION,
        "theme_id": snapshot.get("theme_id"),
        "reason_code": reason,
        "supporting_event_count": None,
        "event_growth_rate": None,
        "standing_risk_activity": None,
    }


def _overlap(left: Any, right: Any) -> set[str]:
    if not isinstance(left, list) or not isinstance(right, list):
        return set()
    return {str(item) for item in left if str(item).strip()} & {str(item) for item in right if str(item).strip()}


def _direction(score: float, configuration: dict[str, Any]) -> str:
    increasing = configuration.get("increasing_threshold")
    decreasing = configuration.get("decreasing_threshold")
    if not isinstance(increasing, (int, float)) or not isinstance(decreasing, (int, float)):
        return "STABLE"
    if score >= float(increasing):
        return "INCREASING"
    if score <= float(decreasing):
        return "DECREASING"
    return "STABLE"


def _velocity(score: float, configuration: dict[str, Any]) -> str:
    high = configuration.get("velocity_high")
    moderate = configuration.get("velocity_moderate")
    magnitude = abs(score)
    if isinstance(high, (int, float)) and magnitude >= float(high):
        return "HIGH"
    if isinstance(moderate, (int, float)) and magnitude >= float(moderate):
        return "MODERATE"
    if magnitude > 0:
        return "LOW"
    return "NONE"


def _unit(value: Any, normaliser: Any) -> float | None:
    number = _non_negative(value)
    scale = _non_negative(normaliser)
    if number is None or scale is None or scale == 0:
        return None
    return min(number / scale, 1.0)


def _non_negative(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _growth(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not -10 <= float(value) <= 10:
        return None
    return float(value)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


