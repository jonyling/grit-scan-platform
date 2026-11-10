"""Render the priority finding cards as Markdown. Pure: it reads findings.json and writes text.

Every sentence that comes from the model sits under one of the six questions. Its label says it is the model's. Everything else is
computed: the ranking reasons, the paths and their sources, the evidence flags, and the confidence.
"""

from __future__ import annotations

from typing import Any

QUESTIONS = (
    "What happened?",
    "Which STE entity does it affect?",
    "Which ontology relationships connect it to other entities?",
    "What independent evidence supports the connection?",
    "What could happen to STE because of that connection?",
    "How confident are we?",
)
STATUS_NAMES = {"SOURCE_STATED": "stated in the workbook", "VALIDATED": "validated", "DERIVED": "derived from stated facts", "PROPOSED": "proposed, not validated"}
WITHHELD = "Withheld: the draft did not pass the checks."
LEVEL_NAMES = {"HIGH": "High", "MEDIUM": "Medium", "LOW": "Low", "NOT_ESTABLISHED": "Not established"}


def _date(value: str | None) -> str:
    return value[:10] if value else "date not stated"


def _tier_label(finding: dict[str, Any]) -> str:
    priority = finding.get("priority") or {}
    tier = priority.get("tier")
    parts = []
    if priority.get("big"):
        parts.append("big event")
    if priority.get("direct"):
        parts.append("direct link")
    return f"Tier {tier}" + (f": {' and '.join(parts)}" if parts else "")


def _scope_sentence(q2: dict[str, Any], lob: str | None) -> str:
    scope, gaps = q2["register_scope"], " ".join(q2["gaps"])
    selectable = [item for item in q2["candidates"] if item.get("why") in {"NAMED", "PLACE_AND_LOB"}]
    shown = ", ".join(f"{item['name']} ({item['country'] or 'country not stated'})" for item in (selectable or q2["candidates"])[:3])
    more = f", and {len(selectable or q2['candidates']) - 3} more" if len(selectable or q2["candidates"]) > 3 else ""
    if scope == "NONE":
        return "Not established. No register entity, customer, supplier, or line of business is named, and no ontology path leads from this signal to STE."
    if scope == "LOB":
        return f"Not established at entity level. The register ties this story to the {lob or 'matched'} line of business only. {q2['candidates_total']} entities operate in it or in its grouping. {gaps}".strip()
    if scope in {"UNIT", "BUSINESS_AREA", "GROUP"}:
        level = {"UNIT": "a unit", "BUSINESS_AREA": "the business area", "GROUP": "the group"}[scope]
        return f"Not established at entity level. The register ties this story to {level} only. {gaps}".strip()
    if selectable:
        return f"Candidates the text supports: {shown}{more}. The model chose none of them."
    return f"Not narrowed. {q2['candidates_total']} candidate entities fit the register paths, such as {shown}{more}. The text names none of them."


def _answer_one(card: dict[str, Any]) -> list[str]:
    if "q1" not in card:
        return [f"   {WITHHELD}"]
    lines = [f"   {card['q1']['what_happened']}"]
    lines += [f'   > "{quote}"' for quote in card["q1"]["quotes"][:2]]
    return lines


def _answer_two(card: dict[str, Any]) -> list[str]:
    if "q2" not in card:
        return [f"   {WITHHELD}"]
    q2 = card["q2"]
    if "affected" in card.get("withheld", []):
        return ["   Withheld: the model's choice of entity did not pass the checks.", f"   {_scope_sentence(q2, card.get('lob'))}"]
    if q2["affected"]:
        names = "; ".join(f"{item['name']} ({item['country'] or 'country not stated'}; LOB label: {item['lob_label'] or 'not stated'})" for item in q2["affected"])
        basis = {"NAMED": "named in the text", "PLACE_AND_LOB": "located in a country the text names and operating in its line of business"}
        why = ", ".join(sorted({basis[item["why"]] for item in q2["affected"]}))
        return [f"   {names}. Supported by the register: {why}." + (f" Model: {q2['why']}" if q2["why"] else "")]
    return [f"   {_scope_sentence(q2, card.get('lob'))}"]


def _answer_three(card: dict[str, Any]) -> list[str]:
    if "q3" not in card:
        return [f"   {WITHHELD}"]
    paths = card["q3"]["paths"]
    withheld = "connection" in card.get("withheld", [])
    if not paths:
        return ["   Not established. No ontology path connects this signal to STE."]
    lines = []
    for path in paths:
        mark = "" if path["cited"] or withheld else " (not cited by the model)"
        basis = "; ".join(dict.fromkeys(path["basis"]))
        lines.append(f"   - `{path['path_id']}` {path['text']}. {STATUS_NAMES.get(path['status'], path['status']).capitalize()}: {basis}.{mark}")
    if withheld:
        lines.append("   Withheld: the model's citation of paths did not pass the checks.")
    elif card["q3"]["why"]:
        lines.append(f"   Model: {card['q3']['why']}")
    party = card.get("same_party")
    if party and card.get("rung") not in {None, "THEME"}:
        lines.append(f"   Same party as the register's? {party['verdict'].capitalize()}. Model: {party['why']}" + (f' Quote: "{party["quote"]}"' if party.get("quote") else ""))
    return lines


def _outlet_line(item: dict[str, Any]) -> str:
    link = f" [source]({item['url']})" if item.get("url") else ""
    flags = ["independent of the signal"] if item["independent"] else []
    if item["names_the_same_node"]:
        flags.append("names the same party")
    quote = f' "{item["quote"]}"' if item.get("quote") else ""
    return f"   - {item['publisher']}, {_date(item['published_at'])}:{quote} ({'; '.join(flags)}).{link}"


def _answer_four(card: dict[str, Any]) -> list[str]:
    if "q4" not in card:
        return [f"   {WITHHELD}"]
    q4 = card["q4"]
    connected = bool(card.get("q3", {}).get("paths"))
    withheld = "evidence" in card.get("withheld", [])
    # An outlet that carries the same event and names the same party supports the connection. Only the logged flag decides.
    for_connection = [item for item in q4["items"] if item["names_the_same_node"]]
    for_event = [item for item in q4["items"] if item not in for_connection]
    lines: list[str] = []
    if connected:
        lines += [f"   - Register: {text}" for text in q4["register"]] + [_outlet_line(item) for item in for_connection]
        if withheld:
            lines.append("   Withheld: the model's evidence citations did not pass the checks.")
        elif not q4["register"] and not for_connection:
            lines.append("   - None found for the connection.")
    else:
        lines.append("   No connection is established, so there is none to support.")
        for_event = [*for_connection, *for_event]
        if withheld:
            lines.append("   Withheld: the model's evidence citations did not pass the checks.")
    if for_event:
        lines += ["   Other outlets that report the event without naming the party:", *(_outlet_line(item) for item in for_event)]
    elif not q4["available"]:
        lines.append("   No other outlet in this run carries the event.")
    lines.append(f"   Other outlets that carry the event: {q4['independent_outlets']}, of which {q4['outlets_naming_the_same_node']} name the same party. Register facts: {q4['register_facts']}.")
    return lines


def _answer_five(card: dict[str, Any]) -> list[str]:
    if "q5" not in card:
        return [f"   {WITHHELD}"]
    q5 = card["q5"]
    if q5["hypothesis"]:
        basis = ", ".join([*(f"path `{item}`" for item in q5["path_ids"]), *(f"theme `{item}`" for item in q5["theme_ids"])])
        return [f"   {q5['hypothesis']}", f"   This is a hypothesis, not a forecast. Basis: {basis}."]
    if not card.get("q3", {}).get("paths"):
        themes = ", ".join(f"`{item['theme_id']}` ({item['name']})" for item in card.get("themes") or [] if item["strong"])
        return ["   Not stated. No ontology path connects this event to STE, so no hypothesis is made." + (f" It hits the KEY or other theme {themes} at theme level only." if themes else "")]
    return ["   Not stated. The draft did not pass the checks."]


def _answer_six(card: dict[str, Any]) -> list[str]:
    q6 = card["q6"]
    return [f"   {LEVEL_NAMES[q6['level']]}. This is confidence in the link between the event and the STE register, by rule. It says nothing about outcomes.", *(f"   - {reason}" for reason in q6["reasons"])]


def _loop_line(card: dict[str, Any]) -> str:
    loop = card["loop"]
    searches = loop["follow_up_searches"]
    tool = next((item["tool"] for item in loop["iterations"] if item.get("tool")), None)
    extra = f" Follow-up search: '{tool['query']}' returned {tool['results']} pages and added {tool['added']}." if tool else ""
    withheld = f" Withheld: {', '.join(card['withheld'])}." if card["withheld"] else ""
    open_issues = f" Open issues from the model's review: {len(loop['open_issues'])}." if loop["open_issues"] else ""
    return f"*Loop: {len(loop['iterations'])} iteration(s), stopped on {loop['stop'].lower().replace('_', ' ')}, {loop['model_calls']} model calls, {searches} follow-up search(es).{extra}{withheld}{open_issues}*"


def render_card(card: dict[str, Any]) -> list[str]:
    outlets = [name for name in card["also_reported_by"] if name != card["publisher"]]
    reasons = " ".join((card.get("priority") or {}).get("reasons") or [])
    lines = [
        f"### {card['finding_id']}. {card['title']}",
        "",
        f"{card['publisher']}, {_date(card['published_at'])}. {_tier_label(card)}. [Source]({card['url']})" + (f". Also carried by: {', '.join(dict.fromkeys(outlets))}." if outlets else "."),
        "",
        f"Ranks here because: {reasons or 'it is linked to the register.'}",
        "",
    ]
    answers = (_answer_one, _answer_two, _answer_three, _answer_four, _answer_five, _answer_six)
    for number, (question, answer) in enumerate(zip(QUESTIONS, answers), start=1):
        lines += [f"{number}. **{question}**", *answer(card), ""]
    lines += [_loop_line(card), ""]
    return lines


def _doubted(card: dict[str, Any]) -> list[str]:
    party = card["same_party"] or {}
    link = (card.get("q6") or {}).get("inputs") or {}
    return [
        f"- **{card['finding_id']}. {card['title']}** ({card['publisher']}, {_date(card['published_at'])}). [Source]({card['url']})",
        f"  Linked by name to the register, then doubted by the model ({'it judged the story to be about another party' if party.get('verdict') == 'NO' else 'it could not confirm that the party is the register party'}): {party.get('why')}"
        + (f' Quote: "{party["quote"]}".' if party.get("quote") else ""),
        f"  Confidence in the link: {LEVEL_NAMES[card['q6']['level']]}. Rung {link.get('rung')}.",
    ]


def render_findings(doc: dict[str, Any]) -> list[str]:
    kept = [card for card in doc["findings"] if not card.get("doubted")]
    doubted = [card for card in doc["findings"] if card.get("doubted")]
    lines = [
        "## Priority findings",
        "",
        "These are ordered by briefing priority, not by severity. A tier 1 finding is a big event that also concerns an STE entity, a customer, or a supplier. A tier 2 finding is a big event, "
        "or a direct link that hits a theme or meets a scale cue. Each card answers six questions. The model wrote the sentences marked as the model's and checked its own draft against the ontology. "
        "Rules checked every answer, and the sixth is computed by rule.",
        "",
    ]
    for card in kept:
        lines += render_card(card)
    if not kept:
        lines += ["Every finding was doubted by the model. They are listed below." if doubted else "No signal reached the priority tiers this run.", ""]
    if doubted:
        lines += [
            "### Links the model doubted",
            "",
            "The register name matched, and the model doubted that the story is about the register party. It had to quote the words of the text that raise the doubt, and the code checked the quote. These left the priority list.",
            "",
        ]
        for card in doubted:
            lines += [*_doubted(card), ""]
    return lines


def method_lines(doc: dict[str, Any], priority_config: dict[str, Any]) -> list[str]:
    summary, config = doc["summary"], doc["config"]
    return [
        f"- Priority findings: {summary['findings']} cards. Status {summary['status']}. Confidence {summary['confidence']}.",
        f"- Each card ran in a loop: draft, check against the ontology, the model's review of its own draft, one follow-up search when fewer than 2 other outlets carry the event, and refinement. "
        f"At most {config['max_iterations']} iterations. Stops {summary['stops']}. Model calls {summary['model_calls']}. Follow-up searches {summary['follow_up_searches']} of {config['follow_up_budget']} allowed, at most {config['followups_per_finding']} per finding.",
        f"- A big event hits a KEY STE theme in its title or lead, and meets at least {priority_config['min_cues']} of 3 cues: {priority_config['outlets_needed']} or more outlets (a wire service counts once), a regulator or government action, a war or a pandemic, or a stated figure of at least 1 million in a comparable currency (read as stated, not converted).",
        "- The model first judged whether the party in each story is the register's party. A doubt needs a quote that the code finds in the text. It can only lower the confidence, and a doubted finding leaves the priority list.",
        "- A priority finding has no severity, likelihood, or probability. Question 5 is a hypothesis tied to an ontology path.",
    ]
