"""Rules for the adverse news scan (spec 46, section 6). Pure: no model, no network, no I/O.

The Assessor proposes each field from the prompt's lists. These functions check the proposal against the sources and the
register, and decide the final severity, action, escalation and summaries. A rule never invents a fact: it can only
weaken a label, move an item, or withhold it.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from grit.agents.checks import MIN_QUOTE, normalise, text_problems

CATEGORIES_A = {
    "A1": "Financial Impact", "A2": "Project Delivery Impact", "A3": "Regulatory and Compliance Impact", "A4": "Cyber and Security Impact",
    "A5": "Safety Impact", "A6": "Reputation Impact", "A7": "Strategic Impact",
}
RELATIONSHIPS = (
    "Customer", "Supplier", "Joint venture partner", "Consortium partner", "Government stakeholder", "Major subcontractor",
    "Critical technology provider", "Competitor", "Other relevant stakeholder",
)
DIRECT_EXPOSURE, INDIRECT_EXPOSURE = "Direct exposure", "Indirect exposure"
NOT_CONFIRMED = "Relationship identified, but material dependency not confirmed"
EXPOSURE_UNKNOWN, NO_EXPOSURE = "Exposure unknown", "No confirmed ST Engineering exposure"
EXPOSURES = (DIRECT_EXPOSURE, INDIRECT_EXPOSURE, NOT_CONFIRMED, EXPOSURE_UNKNOWN, NO_EXPOSURE)
IMPACT_AREAS = ("Revenue", "Profitability", "Cash flow", "Project delivery", "Operations", "Compliance", "Cybersecurity", "Safety",
                "Reputation", "Strategic objectives")
IMPACT_STATUS = {"CONFIRMED": "Confirmed impact", "POTENTIAL": "Potential impact supported by evidence", "NOT_ESTABLISHED": "Impact not yet established"}
SEVERITIES = ("Critical", "High", "Medium", "Low")
MONITOR, FOLLOW_BA, FOLLOW_GF, EXCO = "Monitor", "Follow-up with Business Area", "Follow-up with Group Function", "Escalate to EXCO"
ACTIONS = (MONITOR, FOLLOW_BA, FOLLOW_GF, EXCO)
OBTAIN = "Obtain further information"
RESPONSES_C = (MONITOR, OBTAIN, FOLLOW_BA, FOLLOW_GF, EXCO)
HORIZONS = ("Immediate", "Near term", "Medium term", "Longer term", "Unable to determine")
RISK_CATEGORIES_B = (
    "Fraud and Integrity", "Cybersecurity", "Export Controls and Sanctions", "Procurement", "Third-Party Risk", "Project Management",
    "Artificial Intelligence Governance", "Data Protection", "Safety", "Product Quality", "Financial Controls", "Regulatory Compliance", "Other",
)
COVERAGE = ("Yes", "Partially", "No", "Unable to determine")
INTERNAL_REVIEWS = (
    "Risk Radar or Control Tips content", "A new or enhanced CSA question", "Policy or procedure review", "Targeted thematic review",
    "Business Area communication", "Group Function communication", "No further action beyond monitoring",
)
EXPOSURE_B = (
    "A similar business activity or exposure may exist", "Similar controls are likely to be relevant", "A similar vulnerability may reasonably exist",
    "No direct comparison can be established from available information",
)
TRIGGERS_ESCALATION = (
    "ST Engineering", "An ST Engineering subsidiary or joint venture", "A major customer", "A critical supplier or partner", "A major defence programme",
    "Major contract or project cancellation", "Funding withdrawal", "Customer insolvency", "Supplier insolvency affecting delivery",
    "Corruption or bribery allegation", "Significant fraud or whistleblowing matter", "Significant cyber incident",
    "Sensitive, classified or personal information compromise", "Significant safety incident or fatality", "Sanctions", "Export control violations",
    "Major regulatory or government investigation", "Material project delay", "Significant adverse media scrutiny",
)
TRIGGERS_EXCO = (
    "Major contract or programme cancellation", "Customer funding withdrawal materially affecting a programme",
    "Major government budget reduction affecting key programmes", "Corruption or bribery allegations involving ST Engineering or its entities",
    "Significant fraud or whistleblowing allegations", "Export control or sanctions violations",
    "Significant cyber incident involving sensitive or classified information", "Significant safety fatality or major safety event",
    "Material project delay or cost overrun", "Critical supplier insolvency affecting project delivery",
    "Material adverse development involving a major customer", "Material adverse development involving an ST Engineering subsidiary or joint venture",
)
GROUP_FUNCTIONS = (
    "Group Risk & Assurance", "Group Legal", "Group Compliance", "Group Finance", "Group Treasury", "Group IT and Cybersecurity",
    "Group Health, Safety and Environment", "Group Corporate Communications", "Group Procurement", "Group Strategy", "Group Export Control",
    "Group Human Resources",
)
UPDATE_STATUS = ("NEW", "MATERIAL_UPDATE", "NO_MATERIAL_UPDATE")
CRITIC_RULES = ("NOT_ADVERSE", "NOT_MATERIAL", "UNSUPPORTED_FACT", "ASSUMED_EXPOSURE", "SPECULATIVE", "DUPLICATE", "OUT_OF_PERIOD", "OVERSTATED")

NO_CONTROL_WEAKNESS = "The publicly available information does not yet establish the underlying control weakness."
NO_INTERNAL_EXPOSURE = "Existing ST Engineering exposure and control coverage cannot be confirmed from publicly available information."
NO_IMPACT = "No material adverse impact identified based on currently available information."
NO_DEVELOPMENTS = "No material adverse developments identified during this reporting period."
NO_ESCALATION = "No matters requiring immediate escalation were identified during this reporting period."
NO_UPDATE = "No material update from the previous reporting period."
NO_IMMEDIATE = "No matters requiring immediate management attention were identified."
NO_MONITORING = "No developments requiring enhanced monitoring were identified."
NO_LESSONS = "No significant competitor lessons learnt requiring internal review were identified."
OVERALL = ("No Material Concern", "Elevated Monitoring Required", "Management Action Recommended", "Immediate Escalation Recommended")

# Section B lessons must not say ST Engineering has the weakness (prompt: "Do not state or imply ...").
_STE = r"(?:ST\s*Engineering|STE|ST\s*Engg)"
_DEFICIENCY = re.compile(
    rf"\b{_STE}(?:'s)?\s+(?:\w+\s+){{0,3}}(?:has|have|had|lacks?|lacked|is|are|was|were|suffers?|does\s+not|doesn't|did\s+not|failed|fails)\s+"
    r"(?:\w+\s+){0,3}(?:weak|weakness|deficien\w*|gaps?|inadequate|lacking|failed|failing|vulnerable|exposed\s+to\s+the\s+same|no\s+control|not\s+compliant|non-?compliant)",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _pick(value: Any, allowed: Iterable[str]) -> str | None:
    return value if isinstance(value, str) and value in tuple(allowed) else None


def _quoted(quote: Any, text: str) -> bool:
    return isinstance(quote, str) and len(quote.strip()) >= MIN_QUOTE and normalise(quote) in normalise(text)


def facts_problems(facts: Any, sources: Mapping[str, Mapping[str, Any]], where: str = "facts") -> list[str]:
    """Each fact names a supplied, verified source and quotes it verbatim."""
    if not isinstance(facts, list) or not facts:
        return [f"NO_EVIDENCE: {where}: give at least one fact with a verbatim quote from a verified source."]
    found: list[str] = []
    for index, fact in enumerate(facts, start=1):
        if not isinstance(fact, Mapping) or not isinstance(fact.get("statement"), str) or not fact["statement"].strip():
            found.append(f"BAD_SHAPE: {where}[{index}]: each fact has statement, source_id and quote.")
            continue
        source = sources.get(str(fact.get("source_id")))
        if source is None:
            found.append(f"UNKNOWN_SOURCE: {where}[{index}]: {fact.get('source_id')} was not supplied.")
            continue
        if not _quoted(fact.get("quote"), f"{source.get('title') or ''} {source.get('text') or ''}"):
            found.append(f"QUOTE_NOT_VERBATIM: {where}[{index}]: the quote must be {MIN_QUOTE} or more characters copied exactly from {fact.get('source_id')}.")
    return found


def _all_text(sources: Mapping[str, Mapping[str, Any]]) -> str:
    return " ".join(f"{s.get('title') or ''} {s.get('text') or ''}" for s in sources.values())


def _prose(draft: Mapping[str, Any], keys: Sequence[str]) -> str:
    parts: list[str] = []
    for key in keys:
        value = draft.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if isinstance(item, str))
        elif isinstance(value, Mapping):
            parts.extend(str(item) for item in value.values() if isinstance(item, str))
    return " ".join(parts)


def _date_problems(draft: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]], *, stated_publication: bool) -> list[str]:
    found: list[str] = []
    text = _all_text(sources)
    event_date = draft.get("event_date")
    if event_date not in (None, "not stated"):
        if not (isinstance(event_date, str) and _DATE.match(event_date)):
            found.append("BAD_VALUE: event_date is YYYY-MM-DD or 'not stated'.")
        elif not _quoted(draft.get("event_date_quote"), text):
            found.append("DATE_NOT_QUOTED: an event date needs event_date_quote, copied exactly from a source, that states it.")
    if not stated_publication and draft.get("publication_date") not in (None, "not stated"):
        quote = draft.get("publication_date_quote")
        if not (isinstance(draft.get("publication_date"), str) and _DATE.match(draft["publication_date"])):
            found.append("BAD_VALUE: publication_date is YYYY-MM-DD or 'not stated'.")
        elif not (_quoted(quote, text) and _YEAR.search(str(quote))):
            found.append("DATE_NOT_QUOTED: a publication date needs publication_date_quote, copied exactly from the page, that contains the year.")
    return found


def check_draft(draft: Any, section: str, sources: Mapping[str, Mapping[str, Any]], *, stated_publication: bool = True,
                previous: Mapping[str, Any] | None = None, anchors: Sequence[str] = ()) -> list[str]:
    """Shape, values and grounding of one Assessor draft. An empty list passes."""
    if not isinstance(draft, Mapping):
        return ["BAD_SHAPE: the draft is not an object"]
    if draft.get("include") is False:
        return [] if isinstance(draft.get("exclusion_reason"), str) and draft["exclusion_reason"].strip() else ["BAD_SHAPE: give exclusion_reason when include is false."]
    found: list[str] = []
    for key in ("headline", "event", "affected_entity"):
        if not isinstance(draft.get(key), str) or not draft[key].strip():
            found.append(f"BAD_SHAPE: {key} is empty.")
    tests = draft.get("tests")
    if not isinstance(tests, Mapping) or any(not isinstance(tests.get(k), str) or not tests[k].strip() for k in
                                             ("adverse_consequence", "affected_party", "management_relevance", "impact_pathway", "materiality")):
        found.append("BAD_SHAPE: tests needs adverse_consequence, affected_party, management_relevance, impact_pathway and materiality, each answered.")
    found += facts_problems(draft.get("facts"), sources)
    found += _date_problems(draft, sources, stated_publication=stated_publication)
    if _pick(draft.get("severity"), SEVERITIES) is None:
        found.append(f"BAD_VALUE: severity is one of {list(SEVERITIES)}.")
    if section == "A":
        prose_keys = ("headline", "event", "why_adverse", "assessment", "action_rationale")
        if _pick(draft.get("category"), CATEGORIES_A) is None:
            found.append(f"BAD_VALUE: category is one of {list(CATEGORIES_A)}.")
        if _pick(draft.get("relationship"), RELATIONSHIPS) is None:
            found.append(f"BAD_VALUE: relationship is one of {list(RELATIONSHIPS)}.")
        if _pick(draft.get("exposure"), EXPOSURES) is None:
            found.append(f"BAD_VALUE: exposure is one of {list(EXPOSURES)}.")
        if _pick(draft.get("action"), ACTIONS) is None:
            found.append(f"BAD_VALUE: action is one of {list(ACTIONS)}.")
        impacts = draft.get("impacts")
        if not isinstance(impacts, list) or any(not isinstance(i, Mapping) or i.get("area") not in IMPACT_AREAS or i.get("status") not in IMPACT_STATUS
                                                for i in impacts):
            found.append(f"BAD_VALUE: impacts is a list of {{area, status, basis}}; area from {list(IMPACT_AREAS)}, status from {list(IMPACT_STATUS)}.")
        for name, allowed in (("escalation_triggers", TRIGGERS_ESCALATION), ("exco_triggers", TRIGGERS_EXCO)):
            value = draft.get(name, [])
            if not isinstance(value, list) or any(item not in allowed for item in value):
                found.append(f"BAD_VALUE: {name} lists only values from {list(allowed)}.")
        if not isinstance(draft.get("why_adverse"), str) or not draft["why_adverse"].strip():
            found.append("BAD_SHAPE: why_adverse is empty.")
    elif section == "B":
        prose_keys = ("headline", "event", "why_material", "control_weakness", "lessons")
        categories = draft.get("risk_categories")
        if not isinstance(categories, list) or not categories or any(item not in RISK_CATEGORIES_B for item in categories):
            found.append(f"BAD_VALUE: risk_categories lists values from {list(RISK_CATEGORIES_B)}.")
        lessons = draft.get("lessons")
        if not isinstance(lessons, list) or not lessons or not all(isinstance(item, str) and item.strip() for item in lessons):
            found.append("BAD_SHAPE: lessons is a JSON list of plain strings, each a specific, practical lesson.")
        else:
            for lesson in lessons:
                if _DEFICIENCY.search(lesson):
                    found.append(f"STE_DEFICIENCY_IMPLIED: lessons: '{lesson[:80]}' states or implies that ST Engineering has the weakness. Say what it could review instead.")
        weakness = draft.get("control_weakness")
        if weakness != NO_CONTROL_WEAKNESS and not _quoted(draft.get("control_weakness_quote"), _all_text(sources)):
            found.append(f"WEAKNESS_NOT_QUOTED: control_weakness needs control_weakness_quote, copied exactly from a source, or the sentence '{NO_CONTROL_WEAKNESS}'")
        if _pick(draft.get("existing_exposure"), EXPOSURE_B) is None:
            found.append(f"BAD_VALUE: existing_exposure is one of {list(EXPOSURE_B)}.")
        reviews = draft.get("internal_review", [])
        if not isinstance(reviews, list) or any(item not in INTERNAL_REVIEWS for item in reviews):
            found.append(f"BAD_VALUE: internal_review lists values from {list(INTERNAL_REVIEWS)}.")
        if not isinstance(draft.get("why_material"), str) or not draft["why_material"].strip():
            found.append("BAD_SHAPE: why_material is empty.")
    else:
        prose_keys = ("headline", "event", "why_it_matters", "how_it_could_evolve", "connection")
        if _pick(draft.get("time_horizon"), HORIZONS) is None:
            found.append(f"BAD_VALUE: time_horizon is one of {list(HORIZONS)}.")
        elif draft["time_horizon"] != "Unable to determine" and not _quoted(draft.get("horizon_quote"), _all_text(sources)):
            found.append("HORIZON_NOT_QUOTED: a time horizon other than 'Unable to determine' needs horizon_quote, copied exactly from a source, that supports it.")
        if _pick(draft.get("response"), RESPONSES_C) is None:
            found.append(f"BAD_VALUE: response is one of {list(RESPONSES_C)}.")
        triggers = draft.get("trigger_points")
        if not isinstance(triggers, list) or not triggers or not all(isinstance(item, str) and item.strip() for item in triggers):
            found.append("BAD_SHAPE: trigger_points is a JSON list of plain strings, each an observable development, such as [\"Formal policy announcement\"].")
        connection = str(draft.get("connection") or "")
        if anchors and not any(anchor.casefold() in connection.casefold() for anchor in anchors):
            found.append("NO_CONNECTION: connection must name ST Engineering, a business area or a line of business from the supplied anchors.")
    found += text_problems(_prose(draft, prose_keys), _all_text(sources), "prose")
    status = draft.get("update_status", "NEW")
    if status not in UPDATE_STATUS:
        found.append(f"BAD_VALUE: update_status is one of {list(UPDATE_STATUS)}.")
    elif previous and status == "MATERIAL_UPDATE":
        quote = draft.get("new_development_quote")
        if not _quoted(quote, _all_text(sources)):
            found.append("UPDATE_NOT_QUOTED: a material update needs new_development_quote, copied exactly from a source.")
        elif normalise(str(quote)) in normalise(str(previous.get("evidence") or "")):
            found.append("UPDATE_NOT_NEW: new_development_quote was already in the earlier report.")
    return found


# Decisions ---------------------------------------------------------------------------------------------------------------


def _lower(severity: str) -> str:
    return SEVERITIES[min(len(SEVERITIES) - 1, SEVERITIES.index(severity) + 1)]


def _cap(severity: str, ceiling: str) -> str:
    return severity if SEVERITIES.index(severity) >= SEVERITIES.index(ceiling) else ceiling


def allowed_relationships(classes: Iterable[str], kinds: Iterable[str] = ()) -> set[str]:
    allowed: set[str] = set()
    kinds = set(kinds)
    for cls in classes:
        if cls in {"STE", "Site"}:
            allowed |= {"Joint venture partner", "Consortium partner", "Other relevant stakeholder"} if "JOINT_VENTURE" in kinds else {"Other relevant stakeholder"}
        elif cls == "Customer":
            allowed |= {"Customer", "Government stakeholder"}
        elif cls == "Supplier":
            allowed |= {"Joint venture partner", "Consortium partner"} if "JV_PARTNER" in kinds else {"Supplier", "Major subcontractor", "Critical technology provider"}
        elif cls == "Org":
            allowed |= {"Competitor"}
    return allowed


def decide_a(draft: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Final Section A fields. facts carries what the rules know: classes and kinds of the linked names, whether a verified page
    names ST Engineering, whether the publication date is stated, whether the item is corroborated, and the linked business areas."""
    item = dict(draft)
    notes: list[str] = []
    classes, kinds = set(facts.get("classes") or ()), set(facts.get("kinds") or ())
    relationship = item["relationship"]
    allowed = allowed_relationships(classes, kinds)
    if classes & {"STE", "Site"} and not classes - {"STE", "Site"}:
        item["relationship"] = "Joint venture partner" if "JOINT_VENTURE" in kinds else "ST Engineering entity"
    elif allowed and relationship not in allowed:
        item["relationship"] = sorted(allowed)[0] if len(allowed) == 1 else next(r for r in RELATIONSHIPS if r in allowed)
        notes.append(f"Relationship '{relationship}' does not match the register; set to '{item['relationship']}'.")
    exposure = item["exposure"]
    if exposure in {DIRECT_EXPOSURE, INDIRECT_EXPOSURE} and not facts.get("ste_named"):
        item["exposure"] = NOT_CONFIRMED if classes else EXPOSURE_UNKNOWN
        notes.append(f"'{exposure}' needs ST Engineering named in a verified source; set to '{item['exposure']}'.")
    severity = item["severity"]
    if severity == "Critical" and item["exposure"] not in {DIRECT_EXPOSURE, INDIRECT_EXPOSURE}:
        severity = "High"
        notes.append("Critical needs direct or indirect exposure; capped at High.")
    if severity in {"Critical", "High"} and not facts.get("publication_stated"):
        severity = "Medium"
        notes.append("High or Critical needs a stated publication date; capped at Medium.")
    item["severity"] = severity
    item["corroborated"] = bool(facts.get("corroborated"))
    action = item["action"]
    if severity == "Critical":
        action = EXCO
    elif action == EXCO and not (severity == "High" and item["exposure"] in {DIRECT_EXPOSURE, INDIRECT_EXPOSURE} and item.get("exco_triggers")):
        action = FOLLOW_GF
        notes.append("Escalate to EXCO needs High or Critical, direct or indirect exposure, and a mandatory EXCO trigger; set to Follow-up with Group Function.")
    item["action"] = action
    owner = item.get("owner")
    areas = list(facts.get("business_areas") or [])
    if action == FOLLOW_BA:
        if owner not in areas:
            item["owner"] = areas[0] if len(areas) == 1 else None
            if owner:
                notes.append(f"Owner '{owner}' is not the linked entity's business area.")
    elif action in {FOLLOW_GF, EXCO}:
        item["owner"] = owner if owner in GROUP_FUNCTIONS else None
    else:
        item["owner"] = None
    triggers = [t for t in item.get("escalation_triggers") or [] if t in TRIGGERS_ESCALATION]
    item["escalate"] = severity in {"Critical", "High"} and bool(triggers)
    if not item.get("impacts"):
        item["impacts"] = []
    return item, notes


def decide_b(draft: Mapping[str, Any], facts: Mapping[str, Any], *, internal_coverage: bool = False) -> tuple[dict[str, Any], list[str]]:
    item = dict(draft)
    notes: list[str] = []
    if not internal_coverage and item.get("coverage") != "Unable to determine":
        notes.append("Coverage needs internal Risk Radar, CSA or policy material; set to 'Unable to determine'.")
        item["coverage"] = "Unable to determine"
    item.setdefault("coverage", "Unable to determine")
    if not internal_coverage:
        item["existing_exposure_note"] = NO_INTERNAL_EXPOSURE
    if item["severity"] in {"Critical", "High"} and not facts.get("publication_stated"):
        item["severity"] = "Medium"
        notes.append("High or Critical needs a stated publication date; capped at Medium.")
    item["corroborated"] = bool(facts.get("corroborated"))
    return item, notes


def decide_c(draft: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    item = dict(draft)
    notes: list[str] = []
    if item.get("response") == EXCO and item.get("severity") not in {"Critical", "High"}:
        item["response"] = FOLLOW_GF
        notes.append("An emerging signal goes to EXCO only when rated High or Critical; set to Follow-up with Group Function.")
    return item, notes


def apply_critic(item: dict[str, Any], verdict: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """A withdrawal removes the item; a downgrade lowers the severity one step and weakens an overstated exposure."""
    kind = verdict.get("verdict")
    rule = verdict.get("rule")
    if kind not in {"WITHDRAW", "DOWNGRADE"} or rule not in CRITIC_RULES:
        return item, None
    reason = f"{rule}: {str(verdict.get('reason') or '').strip()}"
    if kind == "WITHDRAW":
        return None, reason
    changed = dict(item)
    changed["severity"] = _lower(str(item.get("severity") or "Low"))
    if rule == "ASSUMED_EXPOSURE" and changed.get("exposure") in {DIRECT_EXPOSURE, INDIRECT_EXPOSURE}:
        changed["exposure"] = NOT_CONFIRMED
    if changed.get("action") == EXCO and changed["severity"] not in {"Critical", "High"}:
        changed["action"] = FOLLOW_GF
    if changed["severity"] not in {"Critical", "High"}:
        changed["escalate"] = False
    return changed, reason


def overall(items: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[str, str]:
    a, b, c = items.get("A", []), items.get("B", []), items.get("C", [])
    exco = [i for i in a if i.get("action") == EXCO] + [i for i in c if i.get("response") == EXCO]
    follow = [i for i in a if i.get("action") in {FOLLOW_BA, FOLLOW_GF}] + [i for i in c if i.get("response") in {FOLLOW_BA, FOLLOW_GF}]
    watch = [i for i in a if i.get("action") == MONITOR] + [i for i in c if i.get("response") in {MONITOR, OBTAIN}]
    counts = f"{len(a)} Section A, {len(b)} Section B and {len(c)} Section C items"
    if exco:
        return OVERALL[3], f"{len(exco)} item(s) recommended for EXCO escalation, among {counts}."
    if follow:
        return OVERALL[2], f"{len(follow)} item(s) recommended for follow-up with a Business Area or Group Function, among {counts}."
    if watch:
        return OVERALL[1], f"{len(watch)} item(s) to monitor, among {counts}; none needs follow-up."
    return OVERALL[0], f"{counts} after the materiality, evidence and accuracy tests; none needs follow-up or monitoring."


def attention(items: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, list[str]]:
    a, b, c = items.get("A", []), items.get("B", []), items.get("C", [])

    def name(item: Mapping[str, Any]) -> str:
        return f"{item['item_id']} {str(item['headline']).rstrip('. ')}"

    immediate = [f"{name(i)} ({i['action']})" for i in a if i.get("action") != MONITOR]
    immediate += [f"{name(i)} ({i['response']})" for i in c if i.get("response") in {FOLLOW_BA, FOLLOW_GF, EXCO}]
    monitoring = [name(i) for i in a if i.get("action") == MONITOR]
    monitoring += [f"{name(i)} ({i['response']})" for i in c if i.get("response") in {MONITOR, OBTAIN}]
    ranked = sorted(b, key=lambda i: SEVERITIES.index(str(i.get("severity") or "Low")))
    lessons = [f"{name(i)}: consider " + ", ".join(r for r in i.get("internal_review") or [] if r != INTERNAL_REVIEWS[-1]).lower()
               for i in ranked if SEVERITIES.index(str(i.get("severity") or "Low")) <= 2 and any(r != INTERNAL_REVIEWS[-1] for r in i.get("internal_review") or [])]
    return {"immediate": immediate, "monitoring": monitoring, "lessons": lessons}


def _rank(entry: Mapping[str, Any]) -> tuple[int, int]:
    return SEVERITIES.index(str(entry.get("severity") or "Low")), -len(entry.get("facts") or [])


_ENTITY_SPLIT = re.compile(r";\s*|,\s+(?:and\s+)?(?=[A-Z])(?!(?:Ltd|Inc|LLC|Limited|Co|Corp|Corporation|PLC|Plc|AG|SA|S\.A|GmbH|Bhd|Pte)\b)|\s+and\s+(?=[A-Z])")


def entity_names(text: str) -> list[str]:
    """The names in an affected-entity list. A legal suffix after a comma stays with its name: 'Japan Airlines Co., Ltd.'."""
    return [part.strip(" .") for part in _ENTITY_SPLIT.split(text or "") if part and part.strip(" .")]


def merge_into(home: dict[str, Any], entry: Mapping[str, Any]) -> None:
    """home keeps its own text and gains entry's verified sources (renumbered) and quoted facts."""
    renumber: dict[str, str] = {}
    known = {ref["url"]: ref["source_id"] for ref in home["sources"]}
    for ref in entry.get("sources") or []:
        if ref["url"] in known:
            renumber[ref["source_id"]] = known[ref["url"]]
            continue
        new_id = f"S{len(home['sources']) + 1}"
        renumber[ref["source_id"]] = new_id
        home["sources"].append({**ref, "source_id": new_id})
        known[ref["url"]] = new_id
    quotes = {normalise(str(f.get("quote") or "")) for f in home.get("facts") or []}
    for fact in entry.get("facts") or []:
        if normalise(str(fact.get("quote") or "")) not in quotes:
            home.setdefault("facts", []).append({**fact, "source_id": renumber.get(str(fact.get("source_id")), fact.get("source_id"))})
    names = entity_names(str(home.get("affected_entity") or ""))
    for name in entity_names(str(entry.get("affected_entity") or "")):
        if not any(name.casefold() in known.casefold() or known.casefold() in name.casefold() for known in names):
            names.append(name)
    home["affected_entity"] = "; ".join(names)
    for key in ("signal_ids", "event_ids", "entity_ids", "outlets"):
        home[key] = list(dict.fromkeys([*(home.get(key) or []), *(entry.get(key) or [])]))
    home["corroborated"] = bool(home.get("corroborated") or entry.get("corroborated") or len(home.get("outlets") or []) >= 2)


def merge_duplicates(entries: list[dict[str, Any]], *, threshold: float = 0.3) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Items in one section that report the same event (similar headlines) become one: the higher-rated item keeps its text,
    and gains the other's verified sources and quoted facts. Returns the kept items and (merged, into) pairs."""
    from grit.adverse.screen import similar

    kept: list[dict[str, Any]] = []
    merged: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for entry in sorted(entries, key=_rank):
        home = next((k for k in kept if similar(str(k["headline"]), str(entry["headline"])) >= threshold), None)
        if home is None:
            kept.append(entry)
            continue
        merge_into(home, entry)
        merged.append((entry, home))
    return kept, merged


def merge_groups(entries: list[dict[str, Any]], groups: Sequence[Sequence[str]]) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Merge the Critic's same-event groups. A group spanning two sections is ignored: Section B stays separate from A."""
    by_id = {entry["item_id"]: entry for entry in entries}
    gone: set[str] = set()
    merged: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for group in groups:
        members = [by_id[i] for i in group if i in by_id and i not in gone]
        if len(members) < 2 or len({m["section"] for m in members}) > 1:
            continue
        home, *rest = sorted(members, key=_rank)
        for entry in rest:
            merge_into(home, entry)
            gone.add(entry["item_id"])
            merged.append((entry, home))
    return [entry for entry in entries if entry["item_id"] not in gone], merged
