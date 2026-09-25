from datetime import date

from grit_scan.impact_scoring import (
    AliasRecord,
    DimensionAssessment,
    EvidenceConfidence,
    Horizon,
    ImpactVerdict,
    MediaReach,
    NullProjectRegistry,
    ProjectStatus,
    RegistryEntry,
    RegistryKind,
    RiskObservations,
    SeverityBand,
    SeverityDimensionResult,
    SourceTier,
    TransmissionRegistry,
    TransmissionRung,
    ValueAtRiskBasis,
    get_coverage_gaps,
)


def test_transmission_factors_are_owned_by_the_rung() -> None:
    assert TransmissionRung.DIRECT.factor == 1.0
    assert TransmissionRung.COUNTERPARTY.factor == 0.7
    assert TransmissionRung.PERIMETER.factor == 0.4
    assert TransmissionRung.SECTOR.factor == 0.2
    assert TransmissionRung.AMBIENT.factor is None


def test_structured_observations_do_not_accept_free_text_facts() -> None:
    observation = RiskObservations(
        fatalities=0,
        permanent_incapacity=False,
        debarment_months=None,
        work_suspension_days=2,
        criminal_offence_or_censure=None,
        media_reach=MediaReach.NATIONAL,
    )
    assert observation.work_suspension_days == 2
    assert observation.media_reach is MediaReach.NATIONAL


def test_financial_absence_uses_the_value_at_risk_sentinel() -> None:
    result = SeverityDimensionResult(band=ValueAtRiskBasis.INSUFFICIENT_DATA, basis=ValueAtRiskBasis.INSUFFICIENT_DATA.value)
    assert result.is_absent is True
    assert result.band is ValueAtRiskBasis.INSUFFICIENT_DATA


def test_non_financial_absence_uses_not_assessed_not_the_financial_sentinel() -> None:
    result = SeverityDimensionResult(band=DimensionAssessment.NOT_ASSESSED, basis="no health & safety facts reported")
    assert result.is_absent is True
    assert result.band is DimensionAssessment.NOT_ASSESSED
    assert result.band is not ValueAtRiskBasis.INSUFFICIENT_DATA


def test_degraded_mode_reports_required_gaps() -> None:
    gaps = get_coverage_gaps(NullProjectRegistry())
    assert any("financial" in gap for gap in gaps)
    assert any("PERIMETER" in gap for gap in gaps)


def test_source_tiers_include_tier_three_and_four() -> None:
    assert SourceTier.TIER_3.value == "TIER_3"
    assert SourceTier.TIER_4.value == "TIER_4"


def test_base_entries_are_primary_and_aliases_are_optional_overlays() -> None:
    entry = RegistryEntry(
        entity_id="ENTITY-st-engineering-urban-solutions-ltd-taiwan-branch",
        canonical_name="ST Engineering Urban Solutions Ltd. - Taiwan Branch",
        registry_kind=RegistryKind.ENTITY,
        ba="Urban Solutions",
        country="Taiwan",
    )
    registry = TransmissionRegistry(
        entries=(entry,),
        aliases=(AliasRecord(entity_id=entry.entity_id, alias="STE Urban Solutions Taiwan"),),
        project_registry=NullProjectRegistry(),
        snapshot_hash="test",
    )
    assert registry.entries[0].canonical_name.startswith("ST Engineering")
    assert registry.aliases[0].entity_id == registry.entries[0].entity_id


def test_dates_and_project_status_are_typed_contract_fields() -> None:
    assert date(2026, 9, 21).isoformat() == "2026-09-21"
    assert ProjectStatus.LIVE.value == "LIVE"
    assert ProjectStatus.PIPELINE.value == "PIPELINE"


def test_existing_required_enums_remain_available() -> None:
    assert SeverityBand.INSIGNIFICANT == 0
    assert EvidenceConfidence.HIGH.value == "HIGH"
    assert Horizon.IMMINENT.value == "IMMINENT"
    assert ImpactVerdict.POTENTIAL_HIGH.value == "POTENTIAL_HIGH"
