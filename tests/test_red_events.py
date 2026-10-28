"""Each RED event added on 28 October 2026. The cases use the patterns in code, not config/red-patterns.json."""

from __future__ import annotations

import json

import pytest

from grit.core.red_alert import red_check
from grit.core.red_events import RED_EVENTS, merge_red_patterns, sync_red_patterns

BY_ID = {rule["ruleId"]: rule for rule in RED_EVENTS}

# rule id, record, expect red. Corroboration is two outlets unless the case says otherwise.
CASES = [
    ("RED-PANDEMIC", {"title": "WHO declares a pandemic", "text": "The agency declared a pandemic today."}, True),
    ("RED-PANDEMIC", {"title": "WHO declares a pandemic that could spread", "text": ""}, False),
    ("RED-CHINA-ATTACKS-TAIWAN", {"title": "China attacks Taiwan", "text": "Missile strike on Taiwan reported from Taipei."}, True),
    ("RED-CHINA-ATTACKS-TAIWAN", {"title": "US sees China invasion of Taiwan unlikely before 2028", "text": ""}, False),
    ("RED-EARTHQUAKE-8", {"title": "USGS: magnitude 8.1 earthquake, Taiwan region", "text": ""}, True),
    ("RED-EARTHQUAKE-8", {"title": "USGS: magnitude 8.0 earthquake, Taiwan region", "text": ""}, True),
    ("RED-EARTHQUAKE-8", {"title": "USGS: magnitude 7.9 earthquake, Taiwan region", "text": ""}, False),
    ("RED-EARTHQUAKE-8", {"title": "A magnitude 8.2 earthquake could strike Taiwan", "text": ""}, False),
    ("RED-EARTHQUAKE-8", {"title": "USGS: magnitude 18 earthquake count is a table", "text": ""}, False),
    ("RED-FINANCIAL-MELTDOWN", {"title": "Markets face a financial meltdown", "text": "A banking crisis shut lenders."}, True),
    ("RED-FINANCIAL-MELTDOWN", {"title": "Markets could face a financial crisis", "text": ""}, False),
    ("RED-CORRUPTION", {"title": "Supplier faces corruption charges", "text": "", "rung": "COUNTERPARTY"}, True),
    ("RED-CORRUPTION", {"title": "Supplier faces corruption charges", "text": ""}, False),
    ("RED-EHS-DEATH", {"title": "Employee was killed at the yard", "text": "", "rung": "DIRECT"}, True),
    ("RED-EHS-DEATH", {"title": "Employee was killed at the yard", "text": "", "rung": "COUNTERPARTY"}, False),
    ("RED-WORKPLACE-ACCIDENT", {"title": "Factory explosion injures crew", "text": "", "rung": "COUNTERPARTY"}, True),
    ("RED-WORKPLACE-ACCIDENT", {"title": "Factory explosion injures crew", "text": ""}, False),
    ("RED-MIDEAST-EVACUATION", {"title": "Staff ordered to evacuate from Israel", "text": "The order followed missile strikes."}, True),
    ("RED-MIDEAST-EVACUATION", {"title": "Staff ordered to evacuate from Singapore", "text": ""}, False),
    ("RED-MIDEAST-EVACUATION", {"title": "Israel could order an evacuation", "text": ""}, False),
    ("RED-AI-QUANTUM-CYBER", {"title": "Hackers used ransomware against a quantum laboratory", "text": ""}, True),
    ("RED-AI-QUANTUM-CYBER", {"title": "Researchers say AI could face a cyber attack", "text": ""}, False),
    ("RED-SANCTIONS-LINKED", {"title": "US hits the supplier with sanctions", "text": "Sanctions imposed overnight.", "rung": "DIRECT"}, True),
    ("RED-SANCTIONS-LINKED", {"title": "US adds firm to Entity List", "text": "", "rung": "COUNTERPARTY"}, False),
    ("RED-BANKRUPTCY", {"title": "Customer files for bankruptcy", "text": "", "rung": "COUNTERPARTY"}, True),
    ("RED-BANKRUPTCY", {"title": "Customer files for bankruptcy", "text": ""}, False),
    ("RED-TRADE-BARRIER", {"title": "New tariffs imposed on the supplier", "text": "", "rung": "COUNTERPARTY"}, True),
    ("RED-TRADE-BARRIER", {"title": "New tariffs imposed on the supplier", "text": ""}, False),
    ("RED-KEY-PERSONNEL", {"title": "Chief executive died suddenly", "text": "", "rung": "DIRECT"}, True),
    ("RED-KEY-PERSONNEL", {"title": "Chief executive died suddenly", "text": ""}, False),
    ("RED-LABOUR", {"title": "Workers on strike at the yard", "text": "", "rung": "DIRECT"}, True),
    ("RED-LABOUR", {"title": "Workers on strike at the yard", "text": ""}, False),
    ("RED-BASIC-SERVICES-TAIWAN", {"title": "Power outage hits Taipei", "text": ""}, True),
    ("RED-BASIC-SERVICES-TAIWAN", {"title": "Power outage hits London", "text": ""}, False),
    ("RED-BASIC-SERVICES-LINKED", {"title": "Power outage at the plant", "text": "", "rung": "DIRECT"}, True),
    ("RED-BASIC-SERVICES-LINKED", {"title": "Power outage at the plant", "text": ""}, False),
    ("RED-LEGAL-DISPUTE", {"title": "Partner files a lawsuit over the contract", "text": "", "rung": "DIRECT"}, True),
    ("RED-LEGAL-DISPUTE", {"title": "Partner files a lawsuit over the contract", "text": "", "rung": "COUNTERPARTY"}, False),
    ("RED-RIOT", {"title": "Riots erupted outside the project site", "text": "", "rung": "DIRECT"}, True),
    ("RED-RIOT", {"title": "Riots erupted outside the project site", "text": ""}, False),
]


@pytest.mark.parametrize(("rule_id", "record", "expect"), CASES, ids=[f"{number}-{case[0]}-{'red' if case[2] else 'not'}" for number, case in enumerate(CASES)])
def test_each_red_event(rule_id: str, record: dict, expect: bool) -> None:
    result = red_check(record, pack="urs-tw", patterns={"patterns": [BY_ID[rule_id]]}, corroboration=2, high_rated_source=False)
    assert result.red is expect
    if expect:
        assert result.rule_id == rule_id


def test_a_new_pattern_still_needs_corroboration_or_a_high_rated_source() -> None:
    record = {"title": "WHO declares a pandemic", "text": ""}
    patterns = {"patterns": [BY_ID["RED-PANDEMIC"]]}
    assert not red_check(record, pack="ste", patterns=patterns, corroboration=1, high_rated_source=False).red
    assert red_check(record, pack="ste", patterns=patterns, corroboration=1, high_rated_source=True).red


def test_new_patterns_do_not_cover_an_unlisted_pack() -> None:
    record = {"title": "WHO declares a pandemic", "text": ""}
    assert not red_check(record, pack="other", patterns={"patterns": [BY_ID["RED-PANDEMIC"]]}, corroboration=3, high_rated_source=True).red


def test_every_listed_event_has_a_pattern() -> None:
    assert len(BY_ID) == len(RED_EVENTS) == 18


def test_merge_keeps_an_edited_rule_and_appends_the_rest() -> None:
    merged = merge_red_patterns({"harmVerdicts": ["X"], "patterns": [{"ruleId": "RED-PANDEMIC", "label": "kept"}]})
    labels = {rule["ruleId"]: rule.get("label") for rule in merged["patterns"]}
    assert labels["RED-PANDEMIC"] == "kept" and merged["harmVerdicts"] == ["X"]
    assert "RED-EARTHQUAKE-8" in labels and labels["RED-EARTHQUAKE-8"] != "kept"


def test_sync_appends_missing_ids_and_does_not_replace_an_existing_rule(tmp_path) -> None:
    path = tmp_path / "red-patterns.json"
    path.write_text(json.dumps({"harmVerdicts": ["HIGH"], "patterns": [{"ruleId": "RED-PANDEMIC", "label": "kept", "phrases": ["custom phrase"]}]}), encoding="utf-8")
    added = sync_red_patterns(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    pandemic = next(rule for rule in document["patterns"] if rule["ruleId"] == "RED-PANDEMIC")
    assert pandemic["label"] == "kept" and pandemic["phrases"] == ["custom phrase"]
    assert document["harmVerdicts"] == ["HIGH"]
    assert "RED-PANDEMIC" not in added
    assert set(added) == {rule["ruleId"] for rule in RED_EVENTS if rule["ruleId"] != "RED-PANDEMIC"}
    assert sync_red_patterns(path) == []
