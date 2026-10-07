from grit.core.impact_scoring import (
    DimensionAssessment,
    EvidenceConfidence,
    EvidenceResult,
    ImpactVerdict,
    SeverityBand,
    SeverityDimension,
    SeverityResult,
    SourceTier,
    TransmissionResult,
    TransmissionRung,
    TransmissionSource,
    is_high_negative_impact,
)


def _severity(max_band, driving_dimension=None) -> SeverityResult:
    return SeverityResult(
        max_band=max_band,
        driving_dimension=driving_dimension,
        dimensions={},
        severity_definition_version="test",
        rationale="test",
    )


def _transmission(rung: TransmissionRung) -> TransmissionResult:
    return TransmissionResult(
        rung=rung,
        matched_entity=None,
        matched_alias=None,
        registry_kind=None,
        character_offset=None,
        source=TransmissionSource.AUTO,
        rationale="test",
    )


def _evidence(confidence: EvidenceConfidence) -> EvidenceResult:
    return EvidenceResult(
        confidence=confidence,
        source_tier=SourceTier.TIER_1,
        corroborating_outlet_count=2,
        gating_urls_resolved=True,
        weak_source_present=False,
        caveat_required=False,
        caveat_text=None,
        rationale="test",
    )


def test_high_negative_impact_requires_major_direct_and_high_evidence() -> None:
    result = is_high_negative_impact(
        _severity(SeverityBand.MAJOR, SeverityDimension.HEALTH_SAFETY),
        _transmission(TransmissionRung.DIRECT),
        _evidence(EvidenceConfidence.HIGH),
    )
    assert result.verdict is ImpactVerdict.HIGH_NEGATIVE_IMPACT
    assert "Major" in result.rationale
    assert "Health & Safety" in result.rationale


def test_evidence_gating_medium_is_potential_high_never_high() -> None:
    result = is_high_negative_impact(
        _severity(SeverityBand.MAJOR, SeverityDimension.HEALTH_SAFETY),
        _transmission(TransmissionRung.DIRECT),
        _evidence(EvidenceConfidence.MEDIUM),
    )
    assert result.verdict is ImpactVerdict.POTENTIAL_HIGH
    assert result.verdict is not ImpactVerdict.HIGH_NEGATIVE_IMPACT


def test_low_evidence_is_potential_high_not_high() -> None:
    result = is_high_negative_impact(
        _severity(SeverityBand.SEVERE, SeverityDimension.FINANCIAL),
        _transmission(TransmissionRung.COUNTERPARTY),
        _evidence(EvidenceConfidence.LOW),
    )
    assert result.verdict is ImpactVerdict.POTENTIAL_HIGH


def test_perimeter_transmission_is_not_high_even_with_high_evidence() -> None:
    result = is_high_negative_impact(
        _severity(SeverityBand.MAJOR, SeverityDimension.HEALTH_SAFETY),
        _transmission(TransmissionRung.PERIMETER),
        _evidence(EvidenceConfidence.HIGH),
    )
    assert result.verdict is ImpactVerdict.NOT_HIGH


def test_moderate_severity_is_not_high_even_with_direct_and_high_evidence() -> None:
    result = is_high_negative_impact(
        _severity(SeverityBand.MODERATE, SeverityDimension.FINANCIAL),
        _transmission(TransmissionRung.DIRECT),
        _evidence(EvidenceConfidence.HIGH),
    )
    assert result.verdict is ImpactVerdict.NOT_HIGH


def test_absent_severity_never_satisfies_the_gate() -> None:
    result = is_high_negative_impact(
        _severity(DimensionAssessment.NOT_ASSESSED, None),
        _transmission(TransmissionRung.DIRECT),
        _evidence(EvidenceConfidence.HIGH),
    )
    assert result.verdict is ImpactVerdict.NOT_HIGH
