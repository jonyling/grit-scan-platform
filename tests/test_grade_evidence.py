from datetime import date

from grit.core.impact_scoring import (
    EvidenceConfidence,
    MediaReach,
    RiskObservations,
    Signal,
    SourceEvidence,
    SourceTier,
    ValueAtRiskBasis,
    grade_evidence,
)


def _make_signal(sources: tuple[SourceEvidence, ...]) -> Signal:
    return Signal(
        signal_id="SIG-TEST-001",
        scan_date=date(2026, 9, 21),
        published_date=date(2026, 9, 20),
        event_date=date(2026, 9, 20),
        expected_impact_date=date(2026, 9, 20),
        title="Test signal",
        body="Test body",
        country="Taiwan",
        entity=None,
        lob=None,
        sources=sources,
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


def _source(tier: SourceTier, outlet: str, url_ok: bool = True, dates_ok: bool = True, figures_ok: bool = True) -> SourceEvidence:
    return SourceEvidence(
        url=f"https://example.test/{outlet}",
        source_tier=tier,
        independent_outlet=outlet,
        url_check_passed=url_ok,
        dates_cross_checked=dates_ok,
        figures_cross_checked=figures_ok,
    )


def test_high_requires_tier1_two_outlets_cross_checked_and_url_resolved() -> None:
    signal = _make_signal((
        _source(SourceTier.TIER_1, "Outlet A"),
        _source(SourceTier.TIER_1, "Outlet B"),
    ))
    result = grade_evidence(signal)
    assert result.confidence is EvidenceConfidence.HIGH
    assert result.corroborating_outlet_count == 2
    assert result.gating_urls_resolved is True
    assert result.weak_source_present is False
    assert result.caveat_required is False
    assert result.caveat_text is None


def test_failed_url_check_caps_at_low_regardless_of_source_tier() -> None:
    signal = _make_signal((
        _source(SourceTier.TIER_0, "Outlet A"),
        _source(SourceTier.TIER_0, "Outlet B", url_ok=False),
        _source(SourceTier.TIER_0, "Outlet C"),
    ))
    result = grade_evidence(signal)
    assert result.confidence is EvidenceConfidence.LOW
    assert result.gating_urls_resolved is False


def test_no_sources_is_low() -> None:
    result = grade_evidence(_make_signal(()))
    assert result.confidence is EvidenceConfidence.LOW
    assert result.corroborating_outlet_count == 0


def test_single_outlet_is_medium_not_high() -> None:
    signal = _make_signal((_source(SourceTier.TIER_0, "Only Outlet"),))
    result = grade_evidence(signal)
    assert result.confidence is EvidenceConfidence.MEDIUM
    assert "1 independent outlet" not in result.rationale or "outlet(s)" in result.rationale


def test_tier_two_source_is_medium_even_with_corroboration_and_cross_check() -> None:
    signal = _make_signal((
        _source(SourceTier.TIER_2, "Outlet A"),
        _source(SourceTier.TIER_2, "Outlet B"),
    ))
    result = grade_evidence(signal)
    assert result.confidence is EvidenceConfidence.MEDIUM
    assert "TIER_2" in result.rationale


def test_uncorroborated_dates_or_figures_prevents_high() -> None:
    signal = _make_signal((
        _source(SourceTier.TIER_1, "Outlet A"),
        _source(SourceTier.TIER_1, "Outlet B", figures_ok=False),
    ))
    result = grade_evidence(signal)
    assert result.confidence is EvidenceConfidence.MEDIUM


def test_grade_evidence_is_deterministic_across_repeated_calls() -> None:
    signal = _make_signal((
        _source(SourceTier.TIER_1, "Outlet A"),
        _source(SourceTier.TIER_1, "Outlet B"),
    ))
    results = [grade_evidence(signal) for _ in range(100)]
    assert len(set(results)) == 1


def test_appending_a_broken_weak_source_to_a_high_signal_does_not_reduce_confidence() -> None:
    """Monotonicity: a weak (non-primary) corroborating source that itself
    fails URL/cross-check must never drag a qualifying TIER_0/TIER_1 result
    down, per the fix to the original non-monotonic 'all sources' gate."""
    base_sources = (
        _source(SourceTier.TIER_1, "Outlet A"),
        _source(SourceTier.TIER_1, "Outlet B"),
    )
    baseline = grade_evidence(_make_signal(base_sources))
    assert baseline.confidence is EvidenceConfidence.HIGH

    weak_broken_source = _source(SourceTier.TIER_3, "Weak Outlet", url_ok=False, dates_ok=False, figures_ok=False)
    extended = grade_evidence(_make_signal(base_sources + (weak_broken_source,)))
    assert extended.confidence is EvidenceConfidence.HIGH
    assert extended.weak_source_present is True
    assert baseline.weak_source_present is False


def test_weak_source_present_reflects_any_non_primary_tier_source() -> None:
    signal = _make_signal((
        _source(SourceTier.TIER_1, "Outlet A"),
        _source(SourceTier.TIER_1, "Outlet B"),
        _source(SourceTier.TIER_2, "Outlet C"),
    ))
    result = grade_evidence(signal)
    assert result.weak_source_present is True


def test_a_broken_primary_source_still_caps_at_low() -> None:
    """The gate still applies fully within the TIER_0/TIER_1 set itself: this
    is not the case the monotonicity fix was meant to relax."""
    signal = _make_signal((
        _source(SourceTier.TIER_0, "Outlet A"),
        _source(SourceTier.TIER_0, "Outlet B", url_ok=False),
    ))
    result = grade_evidence(signal)
    assert result.confidence is EvidenceConfidence.LOW


def test_tier_three_best_source_requires_the_sop_caveat() -> None:
    signal = _make_signal((
        _source(SourceTier.TIER_3, "Outlet A"),
        _source(SourceTier.TIER_3, "Outlet B"),
    ))
    result = grade_evidence(signal)
    assert result.caveat_required is True
    assert result.caveat_text is not None
    assert "available reporting" in result.caveat_text.lower()


def test_tier_four_best_source_requires_the_sop_caveat() -> None:
    signal = _make_signal((_source(SourceTier.TIER_4, "Outlet A"),))
    result = grade_evidence(signal)
    assert result.caveat_required is True
    assert result.caveat_text is not None


def test_caveat_not_required_when_a_primary_tier_source_exists() -> None:
    signal = _make_signal((
        _source(SourceTier.TIER_1, "Outlet A"),
        _source(SourceTier.TIER_4, "Outlet B"),
    ))
    result = grade_evidence(signal)
    assert result.caveat_required is False
    assert result.caveat_text is None


def test_all_weak_sources_with_a_broken_url_still_caps_at_low() -> None:
    """When no TIER_0/TIER_1 source exists, the full source set becomes the
    gating set by necessity. The blanket URL-check rule still applies."""
    signal = _make_signal((
        _source(SourceTier.TIER_3, "Outlet A", url_ok=False),
        _source(SourceTier.TIER_3, "Outlet B"),
    ))
    result = grade_evidence(signal)
    assert result.confidence is EvidenceConfidence.LOW

