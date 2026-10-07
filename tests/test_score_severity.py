from datetime import date

from grit.core.impact_scoring import (
    DimensionAssessment,
    MediaReach,
    RiskObservations,
    SeverityBand,
    SeverityBandBracket,
    SeverityDefinition,
    SeverityDimension,
    Signal,
    ValueAtRiskBasis,
    score_severity,
)


_EBIT = 100.0
_SCAN_DATE = date(2026, 9, 21)

_NO_OBSERVATIONS = RiskObservations(
    fatalities=None,
    permanent_incapacity=None,
    debarment_months=None,
    work_suspension_days=None,
    criminal_offence_or_censure=None,
    media_reach=None,
)


def _definition() -> SeverityDefinition:
    return SeverityDefinition(
        version="test-fixture",
        preceding_year_ebit=_EBIT,
        brackets={
            SeverityBand.INSIGNIFICANT: SeverityBandBracket(lower_percent=0.0, lower_amount=None, upper_percent=0.01, upper_amount=1.0),
            SeverityBand.MINOR: SeverityBandBracket(lower_percent=0.01, lower_amount=1.0, upper_percent=0.05, upper_amount=5.0),
            SeverityBand.MODERATE: SeverityBandBracket(lower_percent=0.05, lower_amount=5.0, upper_percent=0.20, upper_amount=20.0),
            SeverityBand.MAJOR: SeverityBandBracket(lower_percent=0.20, lower_amount=20.0, upper_percent=0.50, upper_amount=50.0),
            SeverityBand.SEVERE: SeverityBandBracket(lower_percent=0.50, lower_amount=50.0, upper_percent=None, upper_amount=None),
        },
    )


def _make_signal(
    value_at_risk: float | None = None,
    value_at_risk_basis: ValueAtRiskBasis = ValueAtRiskBasis.INSUFFICIENT_DATA,
    observations: RiskObservations = _NO_OBSERVATIONS,
) -> Signal:
    return Signal(
        signal_id="SIG-SEVERITY-001",
        scan_date=_SCAN_DATE,
        published_date=_SCAN_DATE,
        event_date=_SCAN_DATE,
        expected_impact_date=_SCAN_DATE,
        title="Test signal",
        body="Test body",
        country="Taiwan",
        entity=None,
        lob=None,
        sources=(),
        observations=observations,
        value_at_risk=value_at_risk,
        value_at_risk_basis=value_at_risk_basis,
    )


def test_thsrc_forced_braking_scores_major_on_health_safety_with_no_value_at_risk() -> None:
    # Forced emergency braking of live passenger trains: a passenger suffers
    # permanent incapacity, but no financial value-at-risk figure exists.
    observations = RiskObservations(
        fatalities=None,
        permanent_incapacity=True,
        debarment_months=None,
        work_suspension_days=None,
        criminal_offence_or_censure=None,
        media_reach=None,
    )
    signal = _make_signal(value_at_risk=None, value_at_risk_basis=ValueAtRiskBasis.INSUFFICIENT_DATA, observations=observations)
    result = score_severity(signal, _definition())
    assert result.max_band >= SeverityBand.MAJOR
    assert result.driving_dimension is SeverityDimension.HEALTH_SAFETY
    assert result.dimensions[SeverityDimension.FINANCIAL].band is ValueAtRiskBasis.INSUFFICIENT_DATA


def test_large_contract_value_with_no_margin_at_risk_is_insufficient_data_not_severe() -> None:
    # There is no contract-value field on Signal; only value_at_risk feeds
    # FINANCIAL. Its absence must never be read as a severe financial event.
    signal = _make_signal(value_at_risk=None, value_at_risk_basis=ValueAtRiskBasis.INSUFFICIENT_DATA)
    result = score_severity(signal, _definition())
    assert result.dimensions[SeverityDimension.FINANCIAL].band is ValueAtRiskBasis.INSUFFICIENT_DATA
    assert result.dimensions[SeverityDimension.FINANCIAL].band is not SeverityBand.SEVERE


def test_moderate_financial_and_major_health_safety_returns_major_not_an_average() -> None:
    observations = RiskObservations(
        fatalities=1,
        permanent_incapacity=None,
        debarment_months=None,
        work_suspension_days=None,
        criminal_offence_or_censure=None,
        media_reach=None,
    )
    signal = _make_signal(value_at_risk=10.0, value_at_risk_basis=ValueAtRiskBasis.MARGIN_AT_RISK, observations=observations)
    result = score_severity(signal, _definition())
    assert result.dimensions[SeverityDimension.FINANCIAL].band is SeverityBand.MODERATE
    assert result.dimensions[SeverityDimension.HEALTH_SAFETY].band is SeverityBand.MAJOR
    assert result.max_band is SeverityBand.MAJOR
    assert result.driving_dimension is SeverityDimension.HEALTH_SAFETY


def test_exact_boundary_values_are_lower_bound_inclusive() -> None:
    definition = _definition()
    at_moderate_lower_bound = _make_signal(value_at_risk=5.0, value_at_risk_basis=ValueAtRiskBasis.MARGIN_AT_RISK)
    at_major_lower_bound = _make_signal(value_at_risk=20.0, value_at_risk_basis=ValueAtRiskBasis.MARGIN_AT_RISK)
    at_severe_lower_bound = _make_signal(value_at_risk=50.0, value_at_risk_basis=ValueAtRiskBasis.MARGIN_AT_RISK)
    assert score_severity(at_moderate_lower_bound, definition).dimensions[SeverityDimension.FINANCIAL].band is SeverityBand.MODERATE
    assert score_severity(at_major_lower_bound, definition).dimensions[SeverityDimension.FINANCIAL].band is SeverityBand.MAJOR
    assert score_severity(at_severe_lower_bound, definition).dimensions[SeverityDimension.FINANCIAL].band is SeverityBand.SEVERE


def test_all_none_observations_and_no_value_at_risk_is_not_assessed_not_insignificant() -> None:
    signal = _make_signal(value_at_risk=None, value_at_risk_basis=ValueAtRiskBasis.INSUFFICIENT_DATA)
    result = score_severity(signal, _definition())
    assert result.max_band is DimensionAssessment.NOT_ASSESSED
    assert result.driving_dimension is None
    assert result.max_band is not SeverityBand.INSIGNIFICANT


def test_tie_is_broken_by_fixed_priority_and_stated_in_the_rationale() -> None:
    # HEALTH_SAFETY (fatalities=1) and REGULATORY_COMPLIANCE (criminal
    # offence) both score MAJOR/SEVERE-adjacent bands; force an exact tie at
    # MAJOR via debarment_months, and confirm HEALTH_SAFETY wins the tie.
    observations = RiskObservations(
        fatalities=1,
        permanent_incapacity=None,
        debarment_months=6,
        work_suspension_days=None,
        criminal_offence_or_censure=None,
        media_reach=None,
    )
    signal = _make_signal(value_at_risk=None, value_at_risk_basis=ValueAtRiskBasis.INSUFFICIENT_DATA, observations=observations)
    result = score_severity(signal, _definition())
    assert result.dimensions[SeverityDimension.HEALTH_SAFETY].band is SeverityBand.MAJOR
    assert result.dimensions[SeverityDimension.REGULATORY_COMPLIANCE].band is SeverityBand.MAJOR
    assert result.max_band is SeverityBand.MAJOR
    assert result.driving_dimension is SeverityDimension.HEALTH_SAFETY
    assert "tie" in result.rationale.lower()


def test_score_severity_is_deterministic_across_repeated_calls() -> None:
    observations = RiskObservations(
        fatalities=1,
        permanent_incapacity=None,
        debarment_months=None,
        work_suspension_days=None,
        criminal_offence_or_censure=None,
        media_reach=MediaReach.NATIONAL,
    )
    signal = _make_signal(value_at_risk=10.0, value_at_risk_basis=ValueAtRiskBasis.MARGIN_AT_RISK, observations=observations)
    definition = _definition()
    results = {
        (
            result.max_band,
            result.driving_dimension,
            result.rationale,
            tuple((dimension, res.band, res.basis) for dimension, res in sorted(result.dimensions.items(), key=lambda item: item[0].value)),
        )
        for result in (score_severity(signal, definition) for _ in range(100))
    }
    assert len(results) == 1
