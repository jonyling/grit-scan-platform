from datetime import date

from grit_scan.impact_scoring import (
    AliasRecord,
    KeywordTier,
    LobKeyword,
    MediaReach,
    NullProjectRegistry,
    RegistryEntry,
    RegistryKind,
    RiskObservations,
    Signal,
    TransmissionRegistry,
    TransmissionRung,
    TransmissionSource,
    ValueAtRiskBasis,
    resolve_transmission,
)


_ENTITY = RegistryEntry(
    entity_id="ENTITY-st-engineering-urban-solutions-ltd-taiwan-branch",
    canonical_name="ST Engineering Urban Solutions Ltd. - Taiwan Branch",
    registry_kind=RegistryKind.ENTITY,
    ba="Urban Solutions",
    country="Taiwan",
)
_SUPPLIER = RegistryEntry(
    entity_id="SUPPLIER-leonardo-s-p-a",
    canonical_name="Leonardo S.p.A.",
    registry_kind=RegistryKind.SUPPLIER,
    ba="Mobility Rail",
    country=None,
)


def _registry() -> TransmissionRegistry:
    return TransmissionRegistry(
        entries=(_ENTITY, _SUPPLIER),
        aliases=(
            AliasRecord(entity_id=_ENTITY.entity_id, alias="STE Urban Solutions Taiwan"),
            AliasRecord(entity_id=_ENTITY.entity_id, alias="ST Engineering Urban Solutions Taiwan"),
            AliasRecord(entity_id=_ENTITY.entity_id, alias="ST Engineering Urban Solutions Ltd Taiwan Branch"),
        ),
        project_registry=NullProjectRegistry(),
        snapshot_hash="test",
        lob_keywords=(
            LobKeyword(lob="Mobility Rail", keyword="Smart Metro Systems", tier=KeywordTier.KEY_TIER_2),
            LobKeyword(lob="Mobility Rail", keyword="Rail Signalling / SCADA Vulnerabilities", tier=KeywordTier.EMERGING),
        ),
    )


def _make_signal(body: str, country: str, lob: str | None = None) -> Signal:
    return Signal(
        signal_id="SIG-TRANSMISSION-001",
        scan_date=date(2026, 9, 21),
        published_date=date(2026, 9, 20),
        event_date=date(2026, 9, 20),
        expected_impact_date=date(2026, 9, 20),
        title="Test signal",
        body=body,
        country=country,
        entity=None,
        lob=lob,
        sources=(),
        observations=RiskObservations(
            fatalities=None,
            permanent_incapacity=None,
            debarment_months=None,
            work_suspension_days=None,
            criminal_offence_or_censure=None,
            media_reach=MediaReach.NATIONAL,
        ),
        value_at_risk=None,
        value_at_risk_basis=ValueAtRiskBasis.INSUFFICIENT_DATA,
    )


def test_transmission_precedence_direct_over_counterparty() -> None:
    signal = _make_signal(
        body="Leonardo S.p.A. confirmed a delay affecting ST Engineering Urban Solutions Ltd. - Taiwan Branch.",
        country="Taiwan",
    )
    result = resolve_transmission(signal, _registry())
    assert result.rung is TransmissionRung.DIRECT
    assert result.matched_entity == _ENTITY.entity_id
    assert result.source is TransmissionSource.AUTO


def test_ambient_rejection_country_level_only_is_not_sector() -> None:
    signal = _make_signal(
        body="Reports indicate rising geopolitical tension across the region this week.",
        country="Taiwan",
    )
    result = resolve_transmission(signal, _registry())
    assert result.rung is TransmissionRung.AMBIENT
    assert result.rung is not TransmissionRung.SECTOR


def test_alias_normalisation_resolves_all_variants_to_the_same_entity() -> None:
    for alias_text in (
        "STE Urban Solutions Taiwan",
        "ST Engineering Urban Solutions Taiwan",
        "ST Engineering Urban Solutions Ltd Taiwan Branch",
    ):
        signal = _make_signal(body=f"{alias_text} confirmed the incident.", country="Taiwan")
        result = resolve_transmission(signal, _registry())
        assert result.rung is TransmissionRung.DIRECT
        assert result.matched_entity == _ENTITY.entity_id
        assert result.matched_alias == alias_text


def test_sector_when_keyword_matches_without_country_or_name() -> None:
    signal = _make_signal(body="Industry commentary on Smart Metro Systems providers.", country="Vietnam")
    result = resolve_transmission(signal, _registry())
    assert result.rung is TransmissionRung.SECTOR
    assert result.source is TransmissionSource.PROPOSED
    assert result.matched_keyword == "Smart Metro Systems"


def test_perimeter_when_keyword_and_country_both_match_without_name() -> None:
    signal = _make_signal(body="Industry commentary on Smart Metro Systems providers.", country="Taiwan")
    result = resolve_transmission(signal, _registry())
    assert result.rung is TransmissionRung.PERIMETER
    assert result.source is TransmissionSource.PROPOSED
    assert result.matched_keyword == "Smart Metro Systems"
