from grit.core.relevance_scoring import (
    classify_risk_level,
    classify_trend,
    compose_business_relevance_basis,
)


def test_trend_maps_movement_directly_and_falls_back_safely() -> None:
    assert classify_trend("ESCALATING") == "ESCALATING"
    assert classify_trend("EASING") == "EASING"
    assert classify_trend("NO_MATERIAL_CHANGE") == "NO_MATERIAL_CHANGE"
    assert classify_trend("something unrecognised") == "NO_MATERIAL_CHANGE"
    assert classify_trend(None) == "NO_MATERIAL_CHANGE"


def test_risk_level_reuses_the_existing_priority_label() -> None:
    assert classify_risk_level({"priorityLabel": "HIGH"}) == "HIGH"
    assert classify_risk_level({"priorityLabel": "MEDIUM"}) == "MEDIUM"
    assert classify_risk_level({"priorityLabel": "WATCHPOINT"}) == "LOW"
    assert classify_risk_level({}) == "LOW"


def test_basis_prefers_an_evidenced_descent_over_a_theme_only_fallback() -> None:
    item = {
        "domain": "Security",
        "steThemes": ["Geopolitical volatility"],
        "descentStatus": "SUPPORTED",
        "descent": {
            "businessArea": "Urban Solutions",
            "lineOfBusiness": "Mobility Rail",
            "entityOrUnit": "URS Taiwan",
            "transmissionPath": "Bound exposure: URS Taiwan",
        },
    }
    basis = compose_business_relevance_basis(item)
    assert "Urban Solutions / Mobility Rail (URS Taiwan)" in basis
    assert "Bound exposure: URS Taiwan" in basis
    assert "Geopolitical volatility" in basis


def test_basis_names_bound_entities_when_descent_is_not_supported() -> None:
    item = {
        "domain": "Security",
        "steThemes": ["Geopolitical volatility"],
        "descentStatus": "INSUFFICIENT_EVIDENCE",
        "descent": None,
        "exposureEntities": [{"entityId": "ENT-1", "entityName": "URS Taiwan"}],
    }
    basis = compose_business_relevance_basis(item)
    assert "URS Taiwan" in basis
    assert "monitoring-only" in basis


def test_basis_falls_back_to_domain_and_themes_with_no_entity_or_descent() -> None:
    item = {"domain": "Economic", "steThemes": [], "descentStatus": "NOT_JUSTIFIED", "descent": None, "exposureEntities": []}
    basis = compose_business_relevance_basis(item)
    assert "Economic condition" in basis
    assert "no STE theme matched this cycle" in basis
    assert "monitoring only" in basis


def test_basis_is_not_keyed_to_a_condition_name_or_reader_pack() -> None:
    # Two different reader packs with the same domain/theme shape must produce the same
    # kind of sentence. Nothing here is a lookup table of per-condition prose.
    urs_tw_item = {"domain": "Security", "steThemes": ["Geopolitical volatility"], "descentStatus": "NOT_JUSTIFIED", "descent": None}
    urs_item = {"domain": "Security", "steThemes": ["Geopolitical volatility"], "descentStatus": "NOT_JUSTIFIED", "descent": None}
    assert compose_business_relevance_basis(urs_tw_item) == compose_business_relevance_basis(urs_item)
