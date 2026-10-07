from datetime import date, timedelta

from grit.core.impact_scoring import (
    AliasRecord,
    ImpactVerdict,
    MediaReach,
    NullProjectRegistry,
    RegistryEntry,
    RegistryKind,
    RiskObservations,
    SeverityBand,
    SeverityBandBracket,
    SeverityDefinition,
    SeverityDimension,
    Signal,
    SourceEvidence,
    SourceTier,
    TransmissionRegistry,
    TransmissionRung,
    ValueAtRiskBasis,
    assess_impact,
)


_ENTITY = RegistryEntry(
    entity_id="ENTITY-st-engineering-urban-solutions-ltd-taiwan-branch",
    canonical_name="ST Engineering Urban Solutions Ltd. - Taiwan Branch",
    registry_kind=RegistryKind.ENTITY,
    ba="Urban Solutions",
    country="Taiwan",
)
_SCAN_DATE = date(2026, 9, 21)


def _registry() -> TransmissionRegistry:
    return TransmissionRegistry(
        entries=(_ENTITY,),
        aliases=(AliasRecord(entity_id=_ENTITY.entity_id, alias="STE Urban Solutions Taiwan"),),
        project_registry=NullProjectRegistry(),
        snapshot_hash="test-snapshot",
    )


def _severity_definition() -> SeverityDefinition:
    return SeverityDefinition(
        version="test-fixture",
        preceding_year_ebit=100.0,
        brackets={
            SeverityBand.INSIGNIFICANT: SeverityBandBracket(lower_percent=0.0, lower_amount=None, upper_percent=0.01, upper_amount=1.0),
            SeverityBand.MINOR: SeverityBandBracket(lower_percent=0.01, lower_amount=1.0, upper_percent=0.05, upper_amount=5.0),
            SeverityBand.MODERATE: SeverityBandBracket(lower_percent=0.05, lower_amount=5.0, upper_percent=0.20, upper_amount=20.0),
            SeverityBand.MAJOR: SeverityBandBracket(lower_percent=0.20, lower_amount=20.0, upper_percent=0.50, upper_amount=50.0),
            SeverityBand.SEVERE: SeverityBandBracket(lower_percent=0.50, lower_amount=50.0, upper_percent=None, upper_amount=None),
        },
    )


def _source(outlet: str) -> SourceEvidence:
    return SourceEvidence(
        url=f"https://example.test/{outlet}",
        source_tier=SourceTier.TIER_1,
        independent_outlet=outlet,
        url_check_passed=True,
        dates_cross_checked=True,
        figures_cross_checked=True,
    )


def _signal() -> Signal:
    return Signal(
        signal_id="SIG-ASSESS-001",
        scan_date=_SCAN_DATE,
        published_date=_SCAN_DATE - timedelta(days=1),
        event_date=_SCAN_DATE - timedelta(days=1),
        expected_impact_date=_SCAN_DATE,
        title="Forced emergency braking incident",
        body="ST Engineering Urban Solutions Ltd. - Taiwan Branch train system triggered forced emergency braking.",
        country="Taiwan",
        entity="ST Engineering Urban Solutions Ltd. - Taiwan Branch",
        lob="Mobility Rail",
        sources=(_source("Outlet A"), _source("Outlet B")),
        observations=RiskObservations(
            fatalities=None,
            permanent_incapacity=True,
            debarment_months=None,
            work_suspension_days=None,
            criminal_offence_or_censure=None,
            media_reach=MediaReach.NATIONAL,
        ),
        value_at_risk=None,
        value_at_risk_basis=ValueAtRiskBasis.INSUFFICIENT_DATA,
    )


def test_degraded_mode_high_negative_impact_fires_from_health_safety_alone() -> None:
    assessment = assess_impact(_signal(), _registry(), _severity_definition())
    assert assessment.transmission.rung is TransmissionRung.DIRECT
    assert assessment.severity.max_band is SeverityBand.MAJOR
    assert assessment.severity.driving_dimension is SeverityDimension.HEALTH_SAFETY
    assert assessment.verdict.verdict is ImpactVerdict.HIGH_NEGATIVE_IMPACT
    assert any("financial" in gap for gap in assessment.coverage_gaps)


def test_assess_impact_is_deterministic_across_repeated_calls() -> None:
    signal = _signal()
    registry = _registry()
    definition = _severity_definition()
    results = [assess_impact(signal, registry, definition) for _ in range(100)]
    assert all(result == results[0] for result in results)
