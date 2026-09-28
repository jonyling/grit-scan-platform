from grit_scan.wild_scan_relevance import (
    classify_wild_scan_risk_level,
    compose_wild_scan_basis,
    match_wild_scan_themes,
)

_REGISTER = {
    "themes": [
        {"themeId": "URS-KEY-04", "themeName": "Cybersecurity risks", "tier": "KEY", "keywords": ["cybersecurity"]},
        {"themeId": "URS-TIER_2-03", "themeName": "Regulatory compliance", "tier": "TIER_2", "keywords": ["regulatory compliance"]},
        {"themeId": "URS-EMERGING-03", "themeName": "Talent scarcity", "tier": "EMERGING", "keywords": ["talent scarcity"]},
    ]
}


def test_match_wild_scan_themes_returns_every_hit() -> None:
    matched = match_wild_scan_themes("Cybersecurity incident triggers regulatory compliance review", _REGISTER)
    ids = {theme["themeId"] for theme in matched}
    assert ids == {"URS-KEY-04", "URS-TIER_2-03"}


def test_classify_wild_scan_risk_level_takes_the_highest_tier() -> None:
    key_and_emerging = [{"tier": "EMERGING"}, {"tier": "KEY"}]
    assert classify_wild_scan_risk_level(key_and_emerging) == "HIGH"
    assert classify_wild_scan_risk_level([{"tier": "TIER_2"}]) == "MEDIUM"
    assert classify_wild_scan_risk_level([{"tier": "EMERGING"}]) == "LOW"
    assert classify_wild_scan_risk_level([]) == "LOW"


def test_compose_wild_scan_basis_names_matched_themes_and_flags_unreviewed() -> None:
    basis = compose_wild_scan_basis([{"themeName": "Cybersecurity risks"}], publisher="reuters.com")
    assert "Cybersecurity risks" in basis
    assert "unreviewed" in basis
    assert "reuters.com" in basis


def test_compose_wild_scan_basis_with_no_match_still_says_monitoring_only() -> None:
    basis = compose_wild_scan_basis([])
    assert "did not match any governed STE" in basis
    assert "monitoring only" in basis
