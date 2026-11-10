"""RED events added on 28 October 2026 (improvement point 3).

The patterns live here so a run can append any that `config/red-patterns.json` does not already have, without replacing a
rule a person has edited. Assessment merges this list in memory. A fact the page does not state stays unmatched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

PACKS = ["ste", "urs", "urs-tw", "dsc"]
LINKED = ["DIRECT", "COUNTERPARTY"]  # an STE entity, project or site, or a registered customer or supplier
STE_SITE = ["DIRECT"]  # an STE entity, project or site. A supplier's own site is a counterparty, not STE staff.
EXCLUDE = [
    "unlikely", "forecast", "assessment", "drill", "exercise", "scenario", "hypothetical", "could", "may be", "might be",
    "expected to", "predicts", "predicted", "estimate", "estimated", "fears", "fear of", "risk of", "warns", "warning",
]
# Magnitude 8.0 or greater. The request said "above 8.0". An 8.0 quake is the event that phrase names.
EARTHQUAKE_8 = (
    r"(?<![\d.])(?:magnitude[\s-]*(?:8(?:\.\d+)?|9(?:\.\d+)?)|(?:8(?:\.\d+)?|9(?:\.\d+)?)[\s-]*magnitude)"
    r"[\s-]*(?:earthquake|quake)\b"
)
_PLACES = (
    r"(?:Middle East|Israel|Israeli|Iran|Iranian|Lebanon|Lebanese|Gaza|Yemeni|Yemen|Saudi|UAE|United Arab Emirates|"
    r"Qatar|Qatari|Iraq|Iraqi|Syria|Syrian|Hormuz|Red Sea)"
)
_EVAC = r"(?:evacuat(?:e|es|ed|ion|ing)|ordered to leave)"
MIDEAST_EVAC = rf"(?:{_EVAC}).{{0,80}}(?:{_PLACES})|(?:{_PLACES}).{{0,80}}(?:{_EVAC})"
_TECH = r"(?:artificial intelligence|\bAI\b|quantum)"
_HIT = r"(?:cyber ?attacks?|ransomware|data breach(?:es)?)"
AI_QUANTUM_CYBER = rf"(?:{_TECH}).{{0,80}}(?:{_HIT})|(?:{_HIT}).{{0,80}}(?:{_TECH})"
SERVICE = ["power outage", "power blackout", "water supply cut", "water outage", "telecoms outage", "telecommunications outage", "internet blackout"]


def _event(
    rule_id: str, label: str, *, phrases: tuple[str, ...] = (), regex: str | None = None, rungs: tuple[str, ...] | list[str] = (), taiwan: bool = False,
    jurisdiction: bool = False,
) -> dict[str, Any]:
    rule: dict[str, Any] = {"ruleId": rule_id, "label": label, "packs": list(PACKS), "excludeInSentence": list(EXCLUDE)}
    if phrases:
        rule["phrases"] = list(phrases)
    if regex:
        rule["regex"] = regex
    if rungs:
        rule["requiresRung"] = list(rungs)
    if taiwan:
        rule["requiresTaiwan"] = True
    if jurisdiction:
        rule["requiresJurisdiction"] = True
    return rule


RED_EVENTS: tuple[dict[str, Any], ...] = (
    _event("RED-PANDEMIC", "Pandemic declared", phrases=("declares a pandemic", "declared a pandemic", "pandemic declared")),
    _event(
        "RED-CHINA-ATTACKS-TAIWAN", "China attacks Taiwan", taiwan=True,
        phrases=("China attacks Taiwan", "Chinese attack on Taiwan", "China invades Taiwan", "invasion of Taiwan", "missile strike on Taiwan", "missile attack on Taiwan", "blockade of Taiwan", "blockades Taiwan", "amphibious assault on Taiwan"),
    ),
    _event("RED-EARTHQUAKE-8", "Earthquake of magnitude 8.0 or greater", regex=EARTHQUAKE_8, jurisdiction=True),
    _event("RED-FINANCIAL-MELTDOWN", "Financial meltdown", phrases=("financial meltdown", "financial crisis", "banking crisis", "stock market crash", "global markets crash")),
    _event("RED-CORRUPTION", "Major corruption case", rungs=LINKED, phrases=("charged with corruption", "corruption charges", "convicted of corruption", "convicted of bribery", "bribery charges")),
    _event("RED-EHS-DEATH", "Death of STE staff", rungs=STE_SITE, phrases=("employee was killed", "employee died", "worker was killed", "workers were killed", "staff member died", "workplace death")),
    _event("RED-WORKPLACE-ACCIDENT", "Workplace accident", rungs=LINKED, phrases=("workplace accident", "industrial accident", "workplace fatality", "factory explosion", "plant explosion")),
    _event("RED-MIDEAST-EVACUATION", "Middle East evacuation", regex=MIDEAST_EVAC, jurisdiction=True),
    _event("RED-AI-QUANTUM-CYBER", "AI or quantum cyber attack", regex=AI_QUANTUM_CYBER, rungs=LINKED),
    _event("RED-SANCTIONS-LINKED", "Sanctions on a linked entity", rungs=LINKED, phrases=("sanctions imposed", "hit with sanctions", "subject to sanctions", "placed under sanctions", "new sanctions against")),
    _event("RED-BANKRUPTCY", "Bankruptcy of a customer, supplier or project", rungs=LINKED, phrases=("files for bankruptcy", "filed for bankruptcy", "files for insolvency", "declared bankrupt", "declares bankruptcy")),
    _event("RED-TRADE-BARRIER", "Trade barrier or tariff", rungs=LINKED, phrases=("tariffs imposed", "tariff hike", "trade barrier", "export ban imposed", "import ban imposed")),
    _event("RED-KEY-PERSONNEL", "Sudden loss of key personnel", rungs=LINKED, phrases=("died suddenly", "sudden death", "resigned with immediate effect", "stepped down with immediate effect", "unexpected death")),
    _event("RED-LABOUR", "Labour strike", rungs=LINKED, phrases=("on strike", "workers strike", "labour strike", "labor strike", "industrial action", "walkout")),
    _event("RED-BASIC-SERVICES-TAIWAN", "Disruption of basic services in Taiwan", phrases=tuple(SERVICE), taiwan=True),
    _event("RED-BASIC-SERVICES-LINKED", "Disruption of basic services at a linked entity", phrases=tuple(SERVICE), rungs=LINKED),
    _event("RED-LEGAL-DISPUTE", "Legal dispute affecting an STE entity", rungs=STE_SITE, phrases=("files a lawsuit", "filed a lawsuit", "legal dispute", "sued for", "arbitration against")),
    _event("RED-RIOT", "Major riot where an STE entity or project is", rungs=STE_SITE, phrases=("riots erupted", "riot erupted", "violent riots", "major riot")),
)


# RED triggers (URS methodology, section 14): what must have happened to the business for R&A to confirm RED. A pattern hit
# above is only a RED candidate, a prompt to check. R&A confirms RED by naming the trigger that happened.
RED_TRIGGERS: tuple[dict[str, str], ...] = (
    {"triggerId": "STAFF_HARMED", "label": "An employee or contractor injured, killed or unaccounted for"},
    {"triggerId": "STAFF_STRANDED", "label": "Staff stranded or unable to leave"},
    {"triggerId": "ASSET_DAMAGED", "label": "An office, site, asset or shipment damaged or hit"},
    {"triggerId": "PROJECT_SUSPENDED", "label": "A project formally suspended"},
    {"triggerId": "MILESTONE_DISRUPTED", "label": "A material project milestone disrupted"},
    {"triggerId": "FORCE_MAJEURE", "label": "A customer force-majeure notice"},
    {"triggerId": "SUPPLIER_FAILURE", "label": "A material supplier failure"},
    {"triggerId": "CASH_PAYMENT", "label": "A material cash or payment issue"},
    {"triggerId": "CYBER_INCIDENT", "label": "A severe cyber incident affecting the business"},
    {"triggerId": "AUTHORITY_RESTRICTION", "label": "Authorities close a project geography or restrict delivery directly"},
)


def red_triggers(document: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Trigger id -> label, from config/red-patterns.json when it lists triggers, else the methodology's list."""
    if document is None:
        from grit.settings import CONFIG_DIR

        path = CONFIG_DIR / "red-patterns.json"
        document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    rows = [row for row in document.get("triggers") or [] if isinstance(row, Mapping) and row.get("triggerId")] or list(RED_TRIGGERS)
    return {str(row["triggerId"]): str(row.get("label") or row["triggerId"]) for row in rows}


def merge_red_patterns(patterns: Mapping[str, Any]) -> dict[str, Any]:
    """The file's rules first, then any event here whose ruleId is absent. An edited rule is left as it stands."""
    current = [rule for rule in patterns.get("patterns") or [] if isinstance(rule, dict)]
    have = {rule.get("ruleId") for rule in current}
    extra = [dict(rule) for rule in RED_EVENTS if rule["ruleId"] not in have]
    if not extra:
        return dict(patterns)
    return {**patterns, "patterns": [*current, *extra]}


def sync_red_patterns(path: Path) -> list[str]:
    """Append missing events to the register. Returns the rule ids added. Does not print the file."""
    if path.is_file():
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("patterns", []), list):
            raise ValueError("red-patterns.json has no patterns list")
        document.setdefault("patterns", [])
    else:
        document = {"harmVerdicts": [], "patterns": []}
        path.parent.mkdir(parents=True, exist_ok=True)
    have = {rule.get("ruleId") for rule in document["patterns"] if isinstance(rule, dict)}
    added = []
    for rule in RED_EVENTS:
        if rule["ruleId"] in have:
            continue
        document["patterns"].append(dict(rule))
        added.append(rule["ruleId"])
    if not document.get("triggers"):
        document["triggers"] = [dict(row) for row in RED_TRIGGERS]
        added.append("triggers")
    if added or not path.is_file():
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return added


def report_synced(path: Path, log: Any = print) -> list[str]:
    added = sync_red_patterns(path)
    if added:
        log("RED patterns added: " + ", ".join(added))
    return added
