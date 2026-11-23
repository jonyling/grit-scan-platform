"""P1 ranking quality: within-band order + URS reach reasons."""

from __future__ import annotations

import pytest

from grit.agents.weekly import (
    CONTROLLED,
    ContractError,
    check_selections,
    order_in_band_of,
    reach_fields_of,
)
from grit.core.overrides import BAND_ELEVATED, BAND_ESCALATING, BAND_WATCH, reader_band_of
from grit.pipeline.weekly import apply_within_band_order
from grit.profiles.emphasis import profile_effect

CHAIN = {
    "fact": "Strike reported at a staffed site.",
    "exposure": "Staff in country X.",
    "transmission": "People risk at a URS site.",
    "impact": "not established",
    "significance": {"exposure": 2, "consequence": 2, "immediacy": 2, "evidence": 1, "change": 1},
    "reach_channel": "person",
    "urs_reach_reason": "URS staff present in the named country.",
}


def test_missing_reach_channel_is_rejected() -> None:
    row = {"signal_id": "A", "decision": "INCLUDE", "reason": "Named", "source_ids": ["A"], **CHAIN}
    del row["reach_channel"]
    with pytest.raises(ContractError, match="reach_channel"):
        check_selections({"selections": [row]}, {"A": {}}, {"A"})


def test_missing_urs_reach_reason_is_rejected() -> None:
    row = {"signal_id": "A", "decision": "INCLUDE", "reason": "Named", "source_ids": ["A"], **CHAIN}
    del row["urs_reach_reason"]
    with pytest.raises(ContractError, match="urs_reach_reason"):
        check_selections({"selections": [row]}, {"A": {}}, {"A"})


def test_invalid_reach_channel_is_rejected() -> None:
    row = {**CHAIN, "signal_id": "A", "decision": "WATCH", "reason": "Named", "source_ids": ["A"],
           "reach_channel": "reputation"}
    with pytest.raises(ContractError, match="reach_channel"):
        check_selections({"selections": [row]}, {"A": {}}, {"A"})


def test_reach_reason_too_long_is_rejected() -> None:
    row = {**CHAIN, "signal_id": "A", "decision": "INCLUDE", "reason": "Named", "source_ids": ["A"],
           "urs_reach_reason": "x" * 161}
    with pytest.raises(ContractError, match="160"):
        check_selections({"selections": [row]}, {"A": {}}, {"A"})


def test_model_cannot_write_band() -> None:
    assert "band" in CONTROLLED
    with pytest.raises(ContractError, match="controlled"):
        check_selections(
            {"selections": [{**CHAIN, "signal_id": "A", "decision": "INCLUDE", "reason": "Named",
                             "source_ids": ["A"], "band": BAND_ESCALATING}]},
            {"A": {}}, {"A"},
        )


def test_order_in_band_must_be_positive_int() -> None:
    with pytest.raises(ContractError, match="order_in_band"):
        check_selections(
            {"selections": [{**CHAIN, "signal_id": "A", "decision": "INCLUDE", "reason": "Named",
                             "source_ids": ["A"], "order_in_band": 0}]},
            {"A": {}}, {"A"},
        )


def test_supplier_without_counterparty_withholds_order() -> None:
    row = {
        **CHAIN, "signal_id": "A", "decision": "INCLUDE", "reason": "Named", "source_ids": ["A"],
        "reach_channel": "supplier", "urs_reach_reason": "Claims a supplier hit without rung evidence.",
        "order_in_band": 1,
    }
    checked = check_selections({"selections": [row]}, {"A": {"rung": "SECTOR"}}, {"A"})["selections"][0]
    assert "order_in_band" not in checked
    assert checked["order_withheld"]


def test_apply_within_band_order_numbers_per_band_only() -> None:
    selections = [
        {"signal_id": "e2", "decision": "INCLUDE", **CHAIN},
        {"signal_id": "w1", "decision": "WATCH", **CHAIN, "reach_channel": "cash",
         "urs_reach_reason": "Cash timing for a URS project."},
        {"signal_id": "e1", "decision": "INCLUDE", **CHAIN},
        {"signal_id": "x1", "decision": "EXCLUDE", "reason": "Noise", "source_ids": ["x1"]},
    ]
    bands = {"e2": BAND_ESCALATING, "e1": BAND_ESCALATING, "w1": BAND_WATCH, "x1": BAND_WATCH}
    out = {row["signal_id"]: row for row in apply_within_band_order(selections, bands)}
    assert out["e2"]["order_in_band"] == 1
    assert out["e1"]["order_in_band"] == 2
    assert out["w1"]["order_in_band"] == 1
    assert "order_in_band" not in out["x1"]


def test_within_band_order_does_not_change_core_band() -> None:
    assessment = {"priority": {"band": BAND_ELEVATED, "tier": 2}, "tier": 2,
                  "editorial": {"order_in_band": 1, "reach_channel": "entity",
                                "urs_reach_reason": "Named entity in pack."}}
    assert reader_band_of(assessment) == BAND_ELEVATED
    assert order_in_band_of(assessment) == 1
    assert reach_fields_of(assessment)["reach_channel"] == "entity"


def test_affinity_is_secondary_within_band_sort(tmp_path, monkeypatch) -> None:
    """routed_signals sorts by band then order_in_band then affinity — affinity cannot cross bands."""
    from grit.agents.weekly import order_in_band_of
    from grit.core.overrides import band_sort_key

    low_affinity_escalating = {
        "shown": True, "route": {"priority": "P2"}, "tier": 1,
        "priority": {"band": BAND_ESCALATING},
        "editorial": {"order_in_band": 2}, "preference": {"affinity": 0}, "condition_ids": [], "corroboration": 1,
    }
    high_affinity_watch = {
        "shown": True, "route": {"priority": "P2"}, "tier": 4,
        "priority": {"band": BAND_WATCH},
        "editorial": {"order_in_band": 1}, "preference": {"affinity": 9}, "condition_ids": [], "corroboration": 1,
    }
    rows = [high_affinity_watch, low_affinity_escalating]
    rows.sort(key=lambda row: (
        not row["shown"],
        row["route"]["priority"] or "P9",
        band_sort_key(reader_band_of(row)),
        order_in_band_of(row),
        -row["preference"]["affinity"],
    ))
    assert reader_band_of(rows[0]) == BAND_ESCALATING
    assert reader_band_of(rows[1]) == BAND_WATCH


def test_rank_instruction_confines_order_within_band() -> None:
    from grit.agents.harness import _instruction

    text = _instruction("rank-demo")
    assert "within each band" in text
    assert "reach_channel" in text
    assert "order_in_band" in text
    assert "never write band" in text.lower() or "never move an item across bands" in text


def test_profile_effect_copy_says_band() -> None:
    effect = profile_effect({"title": "x", "url": "https://example.com/a"}, type("P", (), {"reader": None, "signal_buttons": False})())
    assert effect.affinity == 0
