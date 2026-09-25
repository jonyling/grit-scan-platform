from datetime import date, timedelta

from grit_scan.impact_scoring import (
    Horizon,
    MediaReach,
    RiskObservations,
    Signal,
    ValueAtRiskBasis,
    classify_horizon,
)


_SCAN_DATE = date(2026, 9, 21)


def _make_signal(expected_impact_date: date | None, event_date: date | None = None) -> Signal:
    return Signal(
        signal_id="SIG-HORIZON-001",
        scan_date=_SCAN_DATE,
        published_date=_SCAN_DATE,
        event_date=event_date,
        expected_impact_date=expected_impact_date,
        title="Test signal",
        body="Test body",
        country="Taiwan",
        entity=None,
        lob=None,
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


def test_no_event_date_is_latent() -> None:
    assert classify_horizon(_make_signal(None)) is Horizon.LATENT


def test_event_date_equal_to_scan_date_is_materialised() -> None:
    assert classify_horizon(_make_signal(_SCAN_DATE)) is Horizon.MATERIALISED


def test_event_date_in_the_past_is_materialised() -> None:
    assert classify_horizon(_make_signal(_SCAN_DATE - timedelta(days=30))) is Horizon.MATERIALISED


def test_event_date_one_day_out_is_imminent() -> None:
    assert classify_horizon(_make_signal(_SCAN_DATE + timedelta(days=1))) is Horizon.IMMINENT


def test_event_date_eighty_nine_days_out_is_imminent() -> None:
    assert classify_horizon(_make_signal(_SCAN_DATE + timedelta(days=89))) is Horizon.IMMINENT


def test_event_date_exactly_ninety_days_out_is_developing() -> None:
    assert classify_horizon(_make_signal(_SCAN_DATE + timedelta(days=90))) is Horizon.DEVELOPING


def test_event_date_far_out_is_developing() -> None:
    assert classify_horizon(_make_signal(_SCAN_DATE + timedelta(days=200))) is Horizon.DEVELOPING


def test_classify_horizon_is_deterministic_across_repeated_calls() -> None:
    signal = _make_signal(_SCAN_DATE + timedelta(days=45))
    results = {classify_horizon(signal) for _ in range(100)}
    assert results == {Horizon.IMMINENT}


def test_past_event_date_with_future_expected_impact_date_is_never_materialised() -> None:
    # A debarment announced yesterday (event_date) taking effect in six months
    # (expected_impact_date) is a materialised event but a developing impact.
    signal = _make_signal(
        expected_impact_date=_SCAN_DATE + timedelta(days=180),
        event_date=_SCAN_DATE - timedelta(days=1),
    )
    assert classify_horizon(signal) is Horizon.DEVELOPING


def test_past_event_date_with_near_term_expected_impact_date_is_imminent() -> None:
    signal = _make_signal(
        expected_impact_date=_SCAN_DATE + timedelta(days=10),
        event_date=_SCAN_DATE - timedelta(days=1),
    )
    assert classify_horizon(signal) is Horizon.IMMINENT
