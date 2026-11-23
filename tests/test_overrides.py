"""P0 ranking overrides + reader bands (Escalating / Elevated / Watch)."""

from __future__ import annotations

from grit.core.overrides import (
    BAND_ELEVATED,
    BAND_ESCALATING,
    BAND_WATCH,
    OVERRIDE_REG_IN_FORCE,
    OVERRIDE_STAFF_THREAT,
    apply_reader_band,
    band_from_significance,
    band_from_tier,
    first_override,
    reader_band_of,
    regulation_in_force_override,
    staff_threat_override,
)
from grit.core.people import StaffGrounding


_INSTRUMENTS = {
    "inForcePhrases": ["in force", "takes effect", "took effect", "already in force"],
    "speculativePhrases": ["may regulate", "may impose", "proposes", "proposed", "plans to", "considering"],
    "instruments": [
        {
            "id": "EXPORT_CONTROL_CHIPS",
            "label": "Semiconductor / chip export controls",
            "phrases": ["export controls", "chip ban", "semiconductor ban", "entity list"],
        }
    ],
}


def test_tier_and_significance_map_to_three_bands() -> None:
    assert band_from_tier(1) == BAND_ESCALATING
    assert band_from_tier(2) == BAND_ELEVATED
    assert band_from_tier(3) == BAND_ELEVATED
    assert band_from_tier(4) == BAND_WATCH
    assert band_from_tier(None) == BAND_WATCH
    assert band_from_significance("TOP") == BAND_ESCALATING
    assert band_from_significance("SIGNIFICANT") == BAND_ELEVATED
    assert band_from_significance("SECONDARY") == BAND_WATCH
    assert band_from_significance("LOW") == BAND_WATCH


def test_staffed_strike_override_escalating_even_with_low_score() -> None:
    grounding = StaffGrounding(("Qatar",), ("Qatar: 52 local staff.",), ("missile*",))
    hit = staff_threat_override(
        "Missiles intercepted over Doha",
        "Air defences engaged near Al Udeid.",
        grounding=grounding,
        jurisdictions=["Singapore", "Qatar"],
        terms=["missile*"],
    )
    assert hit is not None
    assert hit.rule_id == OVERRIDE_STAFF_THREAT
    assert hit.band == BAND_ESCALATING

    priority = apply_reader_band(
        {"tier": 4, "reasons": ["Unlinked."], "big": False},
        title="Missiles intercepted over Doha",
        text="Air defences engaged.",
        grounding=grounding,
        jurisdictions=["Qatar"],
        terms=["missile*"],
    )
    assert priority["band"] == BAND_ESCALATING
    assert priority["override_rule_id"] == OVERRIDE_STAFF_THREAT
    assert priority["tier"] == 1
    assert priority.get("score_tier") == 4


def test_unstaffed_country_without_pack_entities_does_not_override() -> None:
    assert staff_threat_override(
        "Missiles intercepted over Kyiv",
        "",
        grounding=None,
        jurisdictions=["Singapore", "Qatar"],
        terms=["missile*"],
    ) is None
    priority = apply_reader_band(
        {"tier": 4},
        title="Missiles intercepted over Kyiv",
        text="",
        grounding=None,
        jurisdictions=["Singapore", "Qatar"],
        terms=["missile*"],
    )
    assert priority["band"] == BAND_WATCH
    assert "override_rule_id" not in priority


def test_people_risk_in_pack_entity_jurisdiction_overrides() -> None:
    """Entity presence (no staff log) still forces Escalating when people-risk hits."""
    hit = staff_threat_override(
        "Evacuation ordered in Singapore after unrest",
        "Authorities urged residents to shelter.",
        grounding=None,
        jurisdictions=["Singapore", "Malaysia"],
        terms=["evacuat*", "unrest"],
    )
    assert hit is not None and hit.rule_id == OVERRIDE_STAFF_THREAT
    assert "Singapore" in hit.reason


def test_chip_rule_in_force_in_urs_jurisdiction_overrides() -> None:
    hit = regulation_in_force_override(
        "US advanced chip export controls already in force for shipments via Singapore",
        "The rules took effect this week for regional hubs.",
        jurisdictions=["Singapore", "USA"],
        instruments=_INSTRUMENTS,
    )
    assert hit is not None
    assert hit.rule_id == OVERRIDE_REG_IN_FORCE
    assert hit.band == BAND_ESCALATING

    priority = apply_reader_band(
        {"tier": 3, "reasons": ["Theme hit."]},
        title="US advanced chip export controls already in force for shipments via Singapore",
        text="The rules took effect this week.",
        jurisdictions=["Singapore"],
        instruments=_INSTRUMENTS,
    )
    assert priority["band"] == BAND_ESCALATING
    assert priority["override_rule_id"] == OVERRIDE_REG_IN_FORCE
    assert priority["tier"] == 1


def test_speculative_may_regulate_does_not_fire_in_force_override() -> None:
    assert regulation_in_force_override(
        "Washington may regulate advanced chips and proposes export controls for Singapore hubs",
        "Officials are considering new rules.",
        jurisdictions=["Singapore"],
        instruments=_INSTRUMENTS,
    ) is None
    # In-force phrase under a speculative modal in the same sentence.
    assert regulation_in_force_override(
        "Chip export controls may take effect in Singapore next year",
        "",
        jurisdictions=["Singapore"],
        instruments=_INSTRUMENTS,
    ) is None


def test_override_wins_over_low_score_and_red_maps_escalating() -> None:
    grounding = StaffGrounding(("Qatar",), ("Qatar: staff present.",), ("evacuat*",))
    priority = apply_reader_band(
        {"tier": 4, "significance_band": "LOW"},
        title="Evacuation ordered in Doha",
        text="",
        grounding=grounding,
        jurisdictions=["Qatar"],
        terms=["evacuat*"],
    )
    assert priority["band"] == BAND_ESCALATING
    assert first_override(
        "Evacuation ordered in Doha", "", grounding=grounding, jurisdictions=["Qatar"], terms=["evacuat*"]
    ) is not None

    no_override = apply_reader_band({"tier": 4}, title="Quiet market note", text="", red=True)
    assert no_override["band"] == BAND_ESCALATING
    assert "override_rule_id" not in no_override

    mid = apply_reader_band({"tier": 2}, title="Quiet market note", text="")
    assert mid["band"] == BAND_ELEVATED


def test_reader_band_of_falls_back_to_tier() -> None:
    assert reader_band_of({"priority": {"band": BAND_ESCALATING, "tier": 4}}) == BAND_ESCALATING
    assert reader_band_of({"priority_json": {"tier": 2}}) == BAND_ELEVATED
    assert reader_band_of({"tier": 1}) == BAND_ESCALATING
    assert reader_band_of({"tier": 4}) == BAND_WATCH


def test_fixture_replay_staffed_threat_lands_in_escalating() -> None:
    """Replay of the miss class: people-risk in a staffed country with a weak score → Escalating via override."""
    # Frozen shape of the broken-week miss: low tier, staff grounding present, no big∧direct score.
    grounding = StaffGrounding(
        ("Qatar",),
        ("Qatar: 52 local staff (URS); 21 travellers and assignees.",),
        ("missile*", "intercepted"),
    )
    weak = {
        "tier": 3,
        "big": False,
        "direct": False,
        "significant": False,
        "gate": [],
        "cues": [],
        "cue_count": 0,
        "theme_rank": 3,
        "outlets": 1,
        "outlet_names": ["example.com"],
        "reasons": ["Linked on THEME only."],
        "staff_countries": ["Qatar"],
        "staff_priority": True,
    }
    result = apply_reader_band(
        weak,
        title="Missiles intercepted over Doha",
        text="Air defences engaged near the capital.",
        grounding=grounding,
        jurisdictions=["Qatar", "United Arab Emirates", "Singapore"],
        terms=["missile*", "evacuat*", "intercepted"],
    )
    assert result["band"] == BAND_ESCALATING
    assert result["override_rule_id"] == OVERRIDE_STAFF_THREAT
    assert result["tier"] == 1
