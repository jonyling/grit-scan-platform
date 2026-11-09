"""Spec 36: the key-risk evidence order, the implication check, and the Proposer's checks. No network, no model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grit.agents.reader import anchor_hits, assess_condition, check_implication, check_options, propose_options
from grit.pipeline.reader_scan import is_material, materiality_order, stub_client

SOURCES = {
    "S1": {"title": "Chip maker warns of memory shortage", "outlet": "a.com", "date": "1 Oct 2026",
           "text": "DRAM prices rose 30 percent as memory makers cut output. Defence electronics makers face longer lead times."},
    "S2": {"title": "Unrelated story", "outlet": "b.com", "date": "2 Oct 2026", "text": "A football club signed a new coach."},
}
ANCHORS = ["Advanced Networks & Sensors", "Micron Technology", "ST Engineering Satellite Systems"]
THEME = {
    "id": "DSC-KEY-06", "name": "Major Supply Chain Disruption", "currentAssessment": "Geopolitical tensions and trade restrictions.",
    "transmission": "Incorporate contractual safeguards including pricing mechanisms where possible.",
    "actionPlans": "Review residual risk and adequacy of existing controls for memory chips.",
}
OWNERS = ["Head, Advanced Networks & Sensors", "Group Procurement", "R&A business partner"]


def _options(**changes):
    options = [
        {"kind": "STRENGTHEN_CONTROL", "option": "Consider adding memory price clauses to new supply contracts.", "owner": "Group Procurement",
         "builds_on": "contractual safeguards including pricing mechanisms", "trigger": "", "source_ids": ["S1"]},
        {"kind": "ACCELERATE_ACTION_PLAN", "option": "Consider bringing forward the memory chip control review.", "owner": "Head, Advanced Networks & Sensors",
         "builds_on": "Review residual risk and adequacy of existing controls", "trigger": "", "source_ids": ["S1"]},
        {"kind": "MONITOR", "option": "Consider continuing to monitor memory prices.", "owner": "R&A business partner", "builds_on": "",
         "trigger": "A supplier announces allocation of memory parts.", "source_ids": []},
    ]
    for path, value in changes.items():
        number, field = path.split("__")
        options[int(number)][field] = value
    return {"reasoning": "Prices rose, so the pricing safeguard is tested.", "options": options}


def test_sources_are_ordered_by_materiality_not_by_date() -> None:
    rows = [
        {"signal_id": "new-sector", "rung": "SECTOR", "tier": 3, "published_at": "2026-10-06T00:00:00Z", "link": {}},
        {"signal_id": "old-direct", "rung": "DIRECT", "tier": 3, "published_at": "2026-10-01T00:00:00Z", "link": {}},
        {"signal_id": "request", "rung": "SECTOR", "tier": 3, "published_at": "2026-10-02T00:00:00Z", "link": {"reader_hits": [{"item_type": "REQUEST"}]}},
        {"signal_id": "red", "rung": None, "tier": 4, "red": 1, "published_at": "2026-09-30T00:00:00Z", "link": {}},
        {"signal_id": "supplier", "rung": "COUNTERPARTY", "tier": 3, "published_at": "2026-10-03T00:00:00Z", "link": {}},
    ]
    assert [row["signal_id"] for row in materiality_order(rows)] == ["red", "old-direct", "supplier", "request", "new-sector"]
    assert [is_material(row) for row in rows] == [False, True, True, True, True]


def test_an_implication_names_the_business_area_reads_as_a_possibility_and_rests_on_its_sources() -> None:
    good = "Longer lead times could delay Advanced Networks & Sensors deliveries. [S1]"
    assert check_implication(good, ["S1"], SOURCES, ANCHORS) == []
    assert anchor_hits("Supplies from MICRON TECHNOLOGY may tighten", ANCHORS) == ["Micron Technology"]

    def problems(text: str) -> str:
        return " | ".join(check_implication(text, ["S1"], SOURCES, ANCHORS))

    assert "NO_ANCHOR" in problems("Longer lead times could delay the business area's deliveries. [S1]")
    assert "NOT_CONDITIONAL" in problems("Longer lead times delay Advanced Networks & Sensors deliveries. [S1]")
    assert "CERTAINTY_CLAIM" in problems("Lead times will delay Advanced Networks & Sensors and could hurt margins. [S1]")
    assert "IMPLICATION_UNCITED" in problems("Lead times could delay Advanced Networks & Sensors deliveries.")
    assert "UNLISTED_SOURCE" in problems("Lead times could delay Advanced Networks & Sensors deliveries. [S2]")
    assert "UNGROUNDED_NUMBER" in problems("A 45 percent rise could squeeze Advanced Networks & Sensors margins. [S1]")
    assert "BANNED_TERM" in problems("The likelihood of delay to Advanced Networks & Sensors could rise. [S1]")
    assert "IMPLICATION_MISSING" in problems("")


def test_an_implication_that_fails_twice_is_dropped_and_the_assessment_stands() -> None:
    draft = {"movement": "ESCALATING", "summary": "DRAM prices rose 30 percent as memory makers cut output. [S1]", "source_ids": ["S1"],
             "quotes": [{"source_id": "S1", "quote": "DRAM prices rose 30 percent as memory makers cut output"}], "change_from_prior": "First assessment.",
             "sequence": [
                 {"date": "1 Oct", "text": "DRAM prices rose 30 percent as memory makers cut output.", "source_id": "S1"},
                 {"date": "1 Oct", "text": "Defence electronics makers face longer lead times.", "source_id": "S1"},
             ],
             "implication": "Prices will hurt the business area. [S1]"}
    calls = []

    def model(system: str, payload: dict) -> dict:
        calls.append(payload)
        return draft

    area = {"name": "Digital Systems & Cyber", "lines_of_business": ["Advanced Networks & Sensors"], "anchors": ANCHORS}
    result, log = assess_condition(stub_client(model), "stub", condition={"id": "DSC-KEY-06", "name": "Supply chain", "kind": "THEME", "tier": "KEY"},
                                   prior=None, sources=SOURCES, business_area=area)
    assert result["status"] == "VALIDATED" and result["movement"] == "ESCALATING"
    assert result["implication"] is None and any("NO_ANCHOR" in item for item in result["implication_violations"])
    assert len(calls) == 2 and "violations" in calls[1], "the Assessor gets one retry with the implication's violations"
    assert "anchors" not in calls[0]["business_area"], "the model sees the short names, not the full alias list"
    assert log.status == "VALIDATED"

    result, _ = assess_condition(stub_client(lambda system, payload: {**draft, "implication": None}), "stub",
                                 condition={"id": "DSC-TIER_2-01", "name": "Competition", "kind": "THEME", "tier": "TIER_2"}, prior=None, sources=SOURCES)
    assert result["status"] == "VALIDATED" and result["implication"] is None and result["implication_violations"] == [], "only a key risk needs an implication"


def test_each_option_check_names_its_violation() -> None:
    assert check_options(_options(), SOURCES, THEME, OWNERS) == []

    def problems(**changes) -> str:
        return " | ".join(check_options(_options(**changes), SOURCES, THEME, OWNERS))

    assert "NOT_FOR_CONSIDERATION" in problems(**{"0__option": "Add memory price clauses to new supply contracts."})
    assert "DIRECTIVE" in problems(**{"0__option": "Consider whether procurement must add memory price clauses."})
    assert "UNKNOWN_OWNER" in problems(**{"0__owner": "Jane Tan"})
    assert "NOT_VERBATIM" in problems(**{"0__builds_on": "a pricing safeguard that the controls never mention"})
    assert "NOT_VERBATIM" in problems(**{"1__builds_on": "contractual safeguards including pricing mechanisms"}), "an action plan quotes the action plans"
    assert "UNCITED" in problems(**{"0__source_ids": []})
    assert "UNKNOWN_SOURCE" in problems(**{"0__source_ids": ["S9"]})
    assert "NO_TRIGGER" in problems(**{"2__trigger": ""})
    assert "MONITOR_COUNT" in problems(**{"2__kind": "ADDRESS_GAP"})
    assert "UNGROUNDED_NUMBER" in problems(**{"0__option": "Consider capping memory price rises at 12 percent."})
    assert "BAD_VALUE" in problems(**{"0__kind": "FIX_IT"})
    assert "NO_REGISTER_TEXT" in " | ".join(check_options(_options(), SOURCES, {**THEME, "actionPlans": None}, OWNERS))
    assert "NO_REASONING" in " | ".join(check_options({**_options(), "reasoning": ""}, SOURCES, THEME, OWNERS))
    assert "BAD_COUNT" in " | ".join(check_options({"reasoning": "x", "options": _options()["options"][:1]}, SOURCES, THEME, OWNERS))


def test_the_proposer_keeps_its_reasoning_in_the_log_only_and_withholds_a_failing_draft() -> None:
    result, log = propose_options(stub_client(lambda system, payload: _options()), "stub", theme=THEME,
                                  assessment={"movement": "ESCALATING", "summary": "x", "implication": None}, sources=SOURCES, owners=OWNERS)
    assert result["status"] == "VALIDATED" and len(result["options"]) == 3
    assert "reasoning" not in json.dumps(result) and log.as_record()["detail"]["reasoning"].startswith("Prices rose")

    calls = []

    def bad(system: str, payload: dict) -> dict:
        calls.append(payload)
        return _options(**{"0__owner": "Jane Tan"})

    result, log = propose_options(stub_client(bad), "stub", theme=THEME, assessment={}, sources=SOURCES, owners=OWNERS)
    assert result["status"] == "WITHHELD" and result["options"] == [] and log.status == "WITHHELD"
    assert len(calls) == 2 and "reasoning" not in calls[1]["previous_draft"], "the retry sees the violations, not its own earlier reasoning"


_WORKBOOK = Path(__file__).resolve().parent.parent / "secrets" / "04_DSC_Entity_Info.xlsx"


@pytest.mark.skipif(not _WORKBOOK.is_file(), reason="the DS&C workbook is confidential and not in the repository")
def test_action_plans_are_kept_only_when_the_pack_asks(tmp_path: Path) -> None:
    from grit.ontology.build_pack import _themes

    plain = _themes(_WORKBOOK, "DSC", counts={"EMERGING": 5})
    assert not any("sheetActionPlans" in theme for theme in plain["themes"]), "a pack without the flag keeps its register unchanged"
    kept = {theme["themeId"]: theme for theme in _themes(_WORKBOOK, "DSC", counts={"EMERGING": 5}, action_plans=True)["themes"]}
    assert kept["DSC-KEY-05"]["sheetActionPlans"] and kept["DSC-KEY-01"]["sheetActionPlans"] is None
    assert "sheetActionPlans" not in kept["DSC-TIER_2-01"], "only key risks have the column"


def test_a_retry_for_the_implication_never_replaces_the_validated_assessment() -> None:
    sequence = [
        {"date": "1 Oct", "text": "DRAM prices rose 30 percent as memory makers cut output.", "source_id": "S1"},
        {"date": "1 Oct", "text": "Defence electronics makers face longer lead times.", "source_id": "S1"},
    ]
    first = {"movement": "ESCALATING", "summary": "DRAM prices rose 30 percent as memory makers cut output. [S1]", "source_ids": ["S1"],
             "quotes": [{"source_id": "S1", "quote": "DRAM prices rose 30 percent as memory makers cut output"}], "change_from_prior": "First assessment.",
             "sequence": sequence, "implication": "This will hurt us. [S1]"}
    nothing = {"movement": "NO_MATERIAL_CHANGE", "summary": "No new development was found this week in the checked sources.", "source_ids": [], "quotes": []}
    fixed_implication = "Longer lead times could delay Advanced Networks & Sensors deliveries. [S1]"
    fixed = {**nothing, "sequence": sequence, "implication": fixed_implication}
    area = {"name": "Digital Systems & Cyber", "anchors": ANCHORS}
    item = {"id": "DSC-KEY-06", "name": "Supply chain", "kind": "THEME", "tier": "KEY"}
    from grit.agents.reader import format_timeline

    for retry, implication in ((nothing, None), (fixed, format_timeline(sequence, fixed_implication))):
        replies = iter([first, retry])
        result, _ = assess_condition(stub_client(lambda system, payload: next(replies)), "stub", condition=item, prior=None, sources=SOURCES, business_area=area)
        assert (result["status"], result["movement"], result["source_ids"]) == ("VALIDATED", "ESCALATING", ["S1"]), "the first validated assessment stands"
        assert result["summary"] == first["summary"] and result["implication"] == implication
        assert bool(result["implication_violations"]) == (implication is None)


def test_only_a_risk_theme_reads_its_sources_by_materiality() -> None:
    from grit.pipeline.reader_scan import condition_sources

    rows = [
        {"signal_id": "new", "rung": "SECTOR", "tier": 2, "published_at": "2026-10-06T00:00:00Z", "condition_ids": ["C1"], "link": {}},
        {"signal_id": "direct", "rung": "DIRECT", "tier": 3, "published_at": "2026-10-01T00:00:00Z", "condition_ids": ["C1"], "link": {}},
    ]
    assert [row["signal_id"] for row in condition_sources(rows, "C1", theme=True)] == ["direct", "new"]
    assert [row["signal_id"] for row in condition_sources(rows, "C1")] == ["new", "direct"], "an entity pack's conditions keep tier, then recency"
