from grit.core.wild_scan_relevance import (
    NO_CONDITION_HEADLINE,
    classify_wild_scan_risk_level,
    compose_wild_scan_basis,
    match_wild_scan_condition,
    match_wild_scan_themes,
    wild_scan_headline,
)

_REGISTER = {
    "themes": [
        {"themeId": "KEY-04", "themeName": "Cybersecurity risks leading to major IT outage", "tier": "KEY_RISK", "keywords": ["cybersecurity"]},
        {"themeId": "T2-03", "themeName": "Regulatory compliance", "tier": "TIER2_RISK", "keywords": ["regulatory compliance"]},
        {"themeId": "EMG-03", "themeName": "Talent scarcity", "tier": "EMERGING_RISK", "keywords": ["talent scarcity"]},
    ]
}

_BASELINE = {
    "conditions": [
        {
            "id": "TW-GLOBAL-02",
            "name": "China political trajectory and Taiwan objective",
            "steThemes": ["Cybersecurity risks", "Fraud, bribery and corruption"],
            "taiwanTransmission": "Calibrated political and economic pressure.",
        },
        {
            "id": "TW-GLOBAL-04",
            "name": "US political, alliance and Taiwan policy trajectory",
            "steThemes": ["Regulatory compliance"],
            "taiwanTransmission": "Deterrence and policy posture shape the operating environment.",
        },
    ]
}


def test_match_wild_scan_themes_returns_every_hit() -> None:
    matched = match_wild_scan_themes("Cybersecurity incident triggers regulatory compliance review", _REGISTER)
    ids = {theme["themeId"] for theme in matched}
    assert ids == {"KEY-04", "T2-03"}


def test_match_wild_scan_condition_uses_normalised_word_overlap_not_exact_string() -> None:
    matched = match_wild_scan_themes("Cybersecurity incident this week", _REGISTER)
    condition = match_wild_scan_condition(matched, _BASELINE)
    assert condition is not None
    assert condition["name"] == "China political trajectory and Taiwan objective"


def test_match_wild_scan_condition_returns_none_without_a_theme_match() -> None:
    assert match_wild_scan_condition([], _BASELINE) is None


def test_classify_wild_scan_risk_level_takes_the_highest_tier() -> None:
    key_and_emerging = [{"tier": "EMERGING_RISK"}, {"tier": "KEY_RISK"}]
    assert classify_wild_scan_risk_level(key_and_emerging) == "HIGH"
    assert classify_wild_scan_risk_level([{"tier": "TIER2_RISK"}]) == "MEDIUM"
    assert classify_wild_scan_risk_level([{"tier": "EMERGING_RISK"}]) == "LOW"
    assert classify_wild_scan_risk_level([]) == "LOW"


def test_wild_scan_headline_uses_condition_name_never_the_title() -> None:
    condition = _BASELINE["conditions"][0]
    assert wild_scan_headline(condition) == "China political trajectory and Taiwan objective"
    assert wild_scan_headline(None) == NO_CONDITION_HEADLINE


def test_compose_wild_scan_basis_uses_the_condition_own_governed_text() -> None:
    condition = _BASELINE["conditions"][1]
    basis = compose_wild_scan_basis(condition, publisher="reuters.com")
    assert "Deterrence and policy posture" in basis
    assert "not yet confirmed by an analyst" in basis
    assert "reuters.com" in basis


def test_compose_wild_scan_basis_with_no_condition_still_says_monitoring_only() -> None:
    basis = compose_wild_scan_basis(None)
    assert "did not match any governed URS-TW condition" in basis
    assert "monitoring only" in basis
