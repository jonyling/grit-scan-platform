from datetime import date

from grit.core.impact_scoring import (
    KeywordTier,
    LobKeyword,
    NullProjectRegistry,
    OpportunityCostStatus,
    Presence,
    RegistryEntry,
    RegistryKind,
    RiskObservations,
    Signal,
    TransmissionRegistry,
    ValueAtRiskBasis,
    assess_impact,
    classify_opportunity_cost,
)
from grit.core.impact_scoring import ImpactVerdict, SeverityBand, SeverityBandBracket, SeverityDefinition


_TAIWAN = RegistryEntry(
    entity_id="ENTITY-urs-taiwan",
    canonical_name="ST Engineering Urban Solutions Ltd. - Taiwan Branch",
    registry_kind=RegistryKind.ENTITY,
    ba="Urban Solutions",
    country="Taiwan",
)
_SCAN_DATE = date(2026, 9, 21)


def _registry() -> TransmissionRegistry:
    return TransmissionRegistry(
        entries=(_TAIWAN,),
        aliases=(),
        project_registry=NullProjectRegistry(),
        snapshot_hash="opportunity-test",
        lob_keywords=(LobKeyword(lob="Mobility Rail", keyword="rail", tier=KeywordTier.KEY_TIER_2),),
    )


def _signal(**overrides) -> Signal:
    values = dict(
        signal_id="SIG-VN-RAIL",
        scan_date=_SCAN_DATE,
        published_date=_SCAN_DATE,
        event_date=_SCAN_DATE,
        expected_impact_date=None,
        title="Vietnam opens a large new rail programme",
        body="The Vietnam rail market is expanding.",
        country="Vietnam",
        entity=None,
        lob="Mobility Rail",
        sources=(),
        observations=RiskObservations(None, None, None, None, None, None),
        value_at_risk=None,
        value_at_risk_basis=ValueAtRiskBasis.INSUFFICIENT_DATA,
        participation_question=True,
    )
    values.update(overrides)
    return Signal(**values)


def _definition() -> SeverityDefinition:
    return SeverityDefinition(
        version="test",
        preceding_year_ebit=100.0,
        brackets={
            SeverityBand.INSIGNIFICANT: SeverityBandBracket(0.0, None, 0.01, 1.0),
            SeverityBand.MINOR: SeverityBandBracket(0.01, 1.0, 0.05, 5.0),
            SeverityBand.MODERATE: SeverityBandBracket(0.05, 5.0, 0.20, 20.0),
            SeverityBand.MAJOR: SeverityBandBracket(0.20, 20.0, 0.50, 50.0),
            SeverityBand.SEVERE: SeverityBandBracket(0.50, 50.0, None, None),
        },
    )


def test_unmarked_market_news_is_not_assessed() -> None:
    result = classify_opportunity_cost(_signal(participation_question=None), _registry())
    assert result.status is OpportunityCostStatus.NOT_ASSESSED
    assert result.confirmation_required is False
    assert "never inferred" in result.rationale


def test_vietnam_rail_white_space_is_opportunity_cost_not_harm() -> None:
    signal = _signal()
    result = classify_opportunity_cost(signal, _registry())
    assert result.status is OpportunityCostStatus.OPPORTUNITY_COST
    assert result.presence is Presence.ABSENT
    assert result.confirmation_required is True
    assert result.matched_lob == "Mobility Rail"
    assert result.matched_keyword == "rail"
    assert result.financial_basis is ValueAtRiskBasis.INSUFFICIENT_DATA

    assessment = assess_impact(signal, _registry(), _definition())
    assert assessment.verdict.verdict is ImpactVerdict.NOT_HIGH
    assert assessment.opportunity_cost.status is OpportunityCostStatus.OPPORTUNITY_COST
    assert assessment.severity.max_band is not SeverityBand.SEVERE


def test_existing_country_footprint_is_present_not_white_space() -> None:
    result = classify_opportunity_cost(_signal(country="Taiwan", title="Taiwan rail expansion"), _registry())
    assert result.status is OpportunityCostStatus.PRESENT
    assert result.presence is Presence.PRESENT
    assert result.confirmation_required is False


def test_participation_question_without_lob_link_is_not_linked() -> None:
    registry = TransmissionRegistry(
        entries=(_TAIWAN,),
        aliases=(),
        project_registry=NullProjectRegistry(),
        snapshot_hash="no-keywords",
    )
    result = classify_opportunity_cost(_signal(), registry)
    assert result.status is OpportunityCostStatus.NOT_LINKED


def test_explicit_non_participation_question_is_not_applicable() -> None:
    result = classify_opportunity_cost(_signal(participation_question=False), _registry())
    assert result.status is OpportunityCostStatus.NOT_APPLICABLE


def test_missing_country_cannot_establish_white_space() -> None:
    result = classify_opportunity_cost(_signal(country=""), _registry())
    assert result.status is OpportunityCostStatus.NOT_ASSESSED
    assert result.presence is Presence.UNKNOWN
    assert result.matched_keyword == "rail"


def test_classify_opportunity_cost_is_deterministic() -> None:
    signal = _signal()
    registry = _registry()
    results = [classify_opportunity_cost(signal, registry) for _ in range(20)]
    assert all(result == results[0] for result in results)
