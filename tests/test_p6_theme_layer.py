from pathlib import Path

from grit_scan.canonical import canonical_hash
from grit_scan.theme_layer import (
    ESCALATION_VERSION,
    METRIC_VERSION,
    event_match,
    horizon_pack_sections,
    theme_escalation_assessment,
    theme_metric_calculation,
    theme_register_match,
)
from supervisor.service import Supervisor

ROOT = Path(__file__).parents[1]
CONFIG = {
    "version": "lifecycle-1",
    "minimum_event_count": 2,
    "minimum_source_diversity": 2,
    "activate_minimum_events": 2,
    "reopen_minimum_events": 2,
    "increasing_threshold": 0.2,
    "decreasing_threshold": -0.2,
    "velocity_high": 0.55,
    "velocity_moderate": 0.2,
    "weights": {
        "event_growth_rate": 0.4,
        "affected_lob_count": 0.15,
        "geography_count": 0.1,
        "source_diversity": 0.15,
        "regulatory_activity": 0.1,
        "standing_risk_activity": 0.1,
    },
    "normalisers": {
        "affected_lob_count": 4,
        "geography_count": 4,
        "source_diversity": 4,
        "regulatory_activity": 4,
        "standing_risk_activity": 4,
    },
    "confidence": {"KEYWORD_MATCH": 0.8, "MANAGEMENT_REQUEST": 0.9, "ENTITY_OVERLAP": 0.8},
    "minimum_confidence": 0.6,
}


def _metrics(**overrides: object) -> dict:
    context = {
        "theme_id": "THM-1",
        "theme_version": 1,
        "theme_type": "EMERGING_THEME",
        "current_lifecycle_state": "ACTIVE",
        "supporting_event_count": 2,
        "event_growth_rate": 1.0,
        "standing_risk_activity": None,
        "affected_lob_count": 0,
        "geography_count": 0,
        "source_diversity": 2,
        "regulatory_activity": 0,
        "metric_period_start_utc": "2026-09-19T00:00:00Z",
        "metric_period_end_utc": "2026-09-26T00:00:00Z",
        "configuration_version": "lifecycle-1",
        "theme_metric_calculation_version": METRIC_VERSION,
        "function_version": ESCALATION_VERSION,
        "idempotency_key": "eval-1",
        "metric_snapshot_hash": "snapshot",
    }
    context.update(overrides)
    return context


def _mutation(mutation_type: str, record: dict, **overrides: object) -> dict:
    payload = {
        "mutation_type": mutation_type,
        "actor_type": "SUPERVISOR",
        "actor_authority": "ANNUAL_REPORT_GOVERNANCE",
        "human_approval_reference": "AR-2026-01",
        "configuration_version": "lifecycle-1",
        "execution_id": "EXEC-1",
        "idempotency_key": f"{mutation_type}:{record.get('theme_id') or record.get('event_id')}",
        "proposed_record": record,
        "content_hash": canonical_hash(record),
    }
    payload.update(overrides)
    return payload


def test_same_metrics_have_the_same_result_hash() -> None:
    first = theme_escalation_assessment(_metrics(), CONFIG)
    second = theme_escalation_assessment(_metrics(idempotency_key="eval-2"), CONFIG)
    assert first["canonical_result_hash"] == second["canonical_result_hash"]
    assert first["input_hash"] != second["input_hash"]
    assert first["escalation_recommendation"] == "ESCALATE"
    assert first["lifecycle_state_proposal"] == "ESCALATING"


def test_high_event_count_cannot_escalate_without_diversity() -> None:
    result = theme_escalation_assessment(
        _metrics(supporting_event_count=40, source_diversity=1, event_growth_rate=1),
        CONFIG,
    )
    assert result["escalation_recommendation"] == "INSUFFICIENT_EVIDENCE"
    assert result["lifecycle_state_proposal"] == "ACTIVE"
    assert result["write_instruction"] == "NONE"
    assert result["reason_code"] == "EVIDENCE_GATE_FAILED"


def test_standing_risk_decline_never_returns_close_candidate() -> None:
    result = theme_escalation_assessment(
        _metrics(
            theme_type="STANDING_RISK",
            current_lifecycle_state="DECLINING",
            standing_risk_activity=0,
            event_growth_rate=-1,
        ),
        CONFIG,
    )
    assert result["escalation_recommendation"] == "DE_ESCALATE"
    assert result["lifecycle_state_proposal"] == "DECLINING"
    assert result["escalation_recommendation"] != "CLOSE_CANDIDATE"
    ordinary = theme_escalation_assessment(
        _metrics(theme_type="EMERGING_THEME", current_lifecycle_state="DECLINING", event_growth_rate=-1),
        CONFIG,
    )
    assert ordinary["escalation_recommendation"] == "CLOSE_CANDIDATE"


def test_missing_standing_activity_does_not_promote() -> None:
    result = theme_escalation_assessment(_metrics(theme_type="STANDING_RISK", standing_risk_activity=None), CONFIG)
    assert result["reason_code"] == "STANDING_RISK_ACTIVITY_ABSENT"
    assert result["write_instruction"] == "NONE"
    unknown = theme_escalation_assessment(_metrics(current_lifecycle_state="PUBLISHED"), CONFIG)
    assert unknown["reason_code"] == "UNKNOWN_LIFECYCLE_STATE"
    assert unknown["lifecycle_state_proposal"] is None


def test_metric_calculation_does_not_invent_growth() -> None:
    metrics = theme_metric_calculation(
        {
            "theme_id": "THM-1",
            "accepted_events": [
                {"accepted": True, "lob_ids": ["Rail"], "geography_ids": ["VN"], "source_ids": ["reuters.com"], "regulatory": False},
                {"accepted": False, "lob_ids": ["Ignored"], "source_ids": ["ignored.example"]},
            ],
            "prior_event_count": 0,
        }
    )
    assert metrics["supporting_event_count"] == 1
    assert metrics["affected_lob_count"] == 1
    assert metrics["source_diversity"] == 1
    assert metrics["event_growth_rate"] is None
    assert metrics["growth_reason"] == "PRIOR_BASELINE_ABSENT"


def test_title_similarity_does_not_match_or_merge() -> None:
    events = event_match(
        {"title": "Yard delay", "entity_ids": ["ENT-1"]},
        [{"event_id": "EVT-9", "title": "Yard delay", "entity_ids": ["ENT-2"]}],
        CONFIG,
    )
    assert events["candidates"] == []
    assert events["reason_code"] == "TITLE_SIMILARITY_INSUFFICIENT"
    assert events["authoritative_identity_created"] is False
    themes = theme_register_match(
        {"title": "Supply interruption", "summary": "Unrelated weather."},
        [{"theme_id": "THM-1", "theme_name": "Supply interruption", "theme_type": "STANDING_RISK", "theme_status": "ACTIVE", "keywords": ["supply interruption"]}],
        CONFIG,
    )
    assert themes["candidates"] == []
    assert themes["emerging_theme_proposal"] is None
    assert themes["reason_code"] == "TITLE_SIMILARITY_INSUFFICIENT"


def test_registry_seed_reseed_and_denied_writes_do_not_touch_workflow() -> None:
    supervisor = Supervisor(actor_id="supervisor-01")
    supervisor.accept_signal("SIG-1", "EXEC-1", "corr-1", accepted_at="2026-09-26T00:00:00Z")
    before = len(supervisor.ledger.rows())
    seeded = supervisor.apply_register_mutation(
        _mutation(
            "SEED",
            {
                "theme_id": "THM-1",
                "theme_name": "Supply interruption",
                "theme_description": "A governed annual-report risk supplied by configuration.",
                "theme_type": "STANDING_RISK",
                "theme_status": "ACTIVE",
                "source_document": "Annual Report fixture",
                "register_version": "2026",
                "approval_reference": "AR-2026-01",
                "configuration_version": "lifecycle-1",
            },
        )
    )
    assert seeded["decision"] == "ALLOW"
    assert seeded["record"]["payload"]["theme_type"] == "STANDING_RISK"
    event = supervisor.apply_register_mutation(
        _mutation(
            "ACCEPT_EVENT",
            {
                "event_id": "EVT-1",
                "event_title": "Yard delay",
                "event_summary": "A supply interruption stopped the yard.",
                "evidence_references": ["https://www.reuters.com/yard"],
                "configuration_version": "lifecycle-1",
            },
            actor_authority="REGISTER_STEWARD",
        )
    )
    assert event["decision"] == "ALLOW"
    matched = theme_register_match(
        {"title": "Other", "summary": "A supply interruption stopped the yard.", "evidence_references": ["https://www.reuters.com/yard"]},
        [{"theme_id": "THM-1", "theme_name": "Supply interruption", "theme_type": "STANDING_RISK", "theme_status": "ACTIVE", "keywords": ["supply interruption"]}],
        CONFIG,
    )
    assert matched["candidates"][0]["theme_id"] == "THM-1"
    attached = supervisor.apply_register_mutation(
        _mutation(
            "ATTACH",
            {
                "theme_id": "THM-1",
                "event_id": "EVT-1",
                "match_method": "KEYWORD_MATCH",
                "match_confidence": 0.8,
                "configuration_version": "lifecycle-1",
            },
            actor_authority="REGISTER_STEWARD",
        )
    )
    assert attached["decision"] == "ALLOW"
    reseeded = supervisor.apply_register_mutation(
        _mutation(
            "RESEED",
            {
                "theme_id": "THM-1",
                "theme_name": "Supply interruption",
                "theme_description": "Refreshed definition from the next annual report.",
                "theme_type": "STANDING_RISK",
                "theme_status": "ACTIVE",
                "source_document": "Annual Report fixture",
                "register_version": "2027",
                "approval_reference": "AR-2027-01",
                "configuration_version": "lifecycle-1",
                "supersedes_theme_version": "THM-1",
            },
            idempotency_key="reseed:THM-1",
        )
    )
    assert reseeded["decision"] == "ALLOW"
    records = supervisor.theme_register.records()
    assert sum(1 for record in records if record["identity"] == "THM-1") == 2
    assert any(record["record_kind"] == "THEME_EVENT" for record in records)
    assert records[0]["payload"]["theme_description"].startswith("A governed")
    declined = supervisor.apply_register_mutation(
        _mutation(
            "SET_LIFECYCLE",
            {"theme_id": "THM-1", "theme_status": "DECLINING", "configuration_version": "lifecycle-1"},
            actor_authority="REGISTER_STEWARD",
            idempotency_key="decline:THM-1",
        )
    )
    assert declined["decision"] == "ALLOW"
    assert supervisor.theme_register.snapshot()["theme_type"]["THM-1"] == "STANDING_RISK"
    assert supervisor.theme_register.snapshot()["theme_status"]["THM-1"] == "DECLINING"
    closed = supervisor.apply_register_mutation(
        _mutation(
            "CLOSE",
            {"theme_id": "THM-1", "theme_status": "CLOSED", "escalation_recommendation": "CLOSE_CANDIDATE"},
            actor_authority="REGISTER_STEWARD",
            human_approval_reference=None,
            idempotency_key="close:THM-1",
        )
    )
    assert closed["decision"] == "DENY"
    assert closed["reason_code"] == "STANDING_RISK_NOT_CLOSED_BY_METRICS"
    assert closed["record"] is None
    promoted = supervisor.apply_register_mutation(
        _mutation(
            "PROMOTE_STANDING",
            {"theme_id": "THM-2", "theme_type": "STANDING_RISK", "supersedes_theme_version": "THM-2"},
            actor_type="AGENT",
            actor_authority="AGENT",
            idempotency_key="promote:THM-2",
        )
    )
    assert promoted["reason_code"] == "AGENT_CANNOT_CHANGE_STANDING_RISK"
    merged = supervisor.apply_register_mutation(
        _mutation(
            "MERGE",
            {"source_theme_id": "THM-1", "target_theme_id": "THM-1", "match_method": "TITLE_SIMILARITY"},
            idempotency_key="merge:title",
        )
    )
    assert merged["reason_code"] == "TITLE_SIMILARITY_INSUFFICIENT"
    replay = supervisor.apply_register_mutation(
        _mutation(
            "SEED",
            {
                "theme_id": "THM-1",
                "theme_name": "Supply interruption",
                "theme_description": "A governed annual-report risk supplied by configuration.",
                "theme_type": "STANDING_RISK",
                "theme_status": "ACTIVE",
                "source_document": "Annual Report fixture",
                "register_version": "2026",
                "approval_reference": "AR-2026-01",
                "configuration_version": "lifecycle-1",
            },
        )
    )
    assert replay["decision"] == "ALREADY_APPLIED"
    assert len(supervisor.ledger.rows()) == before
    source = (ROOT / "grit_scan" / "theme_layer.py").read_text(encoding="utf-8")
    assert "assess_impact" not in source
    assert "Supply interruption" not in source
    assert "cybersecurity" not in source


def test_emerging_theme_is_proposed_only_through_the_gate() -> None:
    no_criteria = theme_register_match({"title": "Odd note", "summary": "No governed keyword."}, [], CONFIG)
    assert no_criteria["reason_code"] == "INSUFFICIENT_EVIDENCE"
    assert no_criteria["emerging_theme_proposal"] is None
    proposed = theme_register_match(
        {
            "title": "Odd note",
            "summary": "No governed keyword.",
            "emergence_criteria": {"requested": True, "evidence_reference_count": 2, "distinct_outlets": 2},
        },
        [],
        CONFIG,
    )
    assert proposed["reason_code"] == "EMERGING_THEME_PROPOSED"
    assert proposed["authoritative_identity_created"] is False
    supervisor = Supervisor(actor_id="supervisor-01")
    record = {
        "theme_name": "Unattached development",
        "theme_type": "EMERGING_THEME",
        "theme_status": "PROPOSED",
        "match_method": "EMERGENCE_CRITERIA",
        "configuration_version": "lifecycle-1",
    }
    denied = supervisor.apply_register_mutation(_mutation("PROPOSE_EMERGING", record, actor_authority="AGENT", human_approval_reference=None))
    assert denied["decision"] == "ALLOW"
    assert denied["record"]["payload"]["theme_type"] == "EMERGING_THEME"
    assert denied["record"]["payload"]["theme_status"] == "PROPOSED"


def test_horizon_pack_keeps_absence_visible_and_orders_by_velocity() -> None:
    sections = horizon_pack_sections(
        [
            {
                "theme_id": "THM-STAND",
                "theme_type": "STANDING_RISK",
                "theme_status": "ACTIVE",
                "supporting_event_count": 0,
                "event_ids": [],
                "evidence_coverage": "INSUFFICIENT_EVIDENCE",
            },
            {
                "theme_id": "THM-LOUD",
                "theme_type": "EMERGING_THEME",
                "theme_status": "ESCALATING",
                "velocity": "LOW",
                "supporting_event_count": 50,
                "event_ids": ["EVT-50"],
                "escalation_recommendation": "ESCALATE",
            },
            {
                "theme_id": "THM-FAST",
                "theme_type": "EMERGING_THEME",
                "theme_status": "ESCALATING",
                "velocity": "HIGH",
                "supporting_event_count": 1,
                "event_ids": ["EVT-1"],
                "escalation_recommendation": "ESCALATE",
            },
        ],
        package_approved=False,
        regulatory_attention_threshold=2,
    )
    assert sections["publication_label"] == "PROPOSAL"
    assert sections["standing_risks"]["entries"][0]["absence"] == "NO_SUPPORTING_EVENTS"
    assert [item["theme_id"] for item in sections["fastest_escalating_themes"]["entries"]] == ["THM-FAST", "THM-LOUD"]
    assert sections["closed_themes"]["absence"] == "NONE_IN_PERIOD"
    assert "top_emerging_themes" in sections
    assert "themes_requiring_management_attention" in sections
    assert "declining_themes" in sections
