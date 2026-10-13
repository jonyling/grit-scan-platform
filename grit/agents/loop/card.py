"""The six-question finding card: its checks, its confidence rule, and its final form. Pure: no model, no network.

The model drafts these fields:
  what_happened   Q1, from the signal text only, with verbatim quotes.
  affected        Q2, entity ids chosen from the candidates the text or a named place supports, with a reason.
  connection      Q3, path ids chosen from the paths the ontology graph supplied, with a reason.
  evidence        Q4, evidence ids with a verbatim quote from each. Whether it is independent is computed here, not claimed.
  hypothesis      Q5, one conditional sentence tied to a theme or a path. It is a hypothesis, not a forecast.
Q6, the confidence in the link, is computed here from logged facts. The model does not report it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from grit.ontology.link import COMPETITOR, COUNTERPARTY, DIRECT, PERIMETER, THEME
from grit.core.verify_text import has_number, numbers_in
from grit.agents.loop.context import SELECTABLE, Context, best_status, normalise_text

HIGH, MEDIUM, LOW, NOT_ESTABLISHED = "HIGH", "MEDIUM", "LOW", "NOT_ESTABLISHED"
MAX_WHAT_WORDS = 60
MAX_WHY_WORDS = 50
MAX_HYPOTHESIS_WORDS = 60
MAX_QUERY_WORDS = 12
MIN_QUOTE = 20  # characters. A shorter "quote" is found in almost any text.
MIN_DOUBT_QUOTE = 4
_BANNED = re.compile(r"\b(severity|severe|likelihood|likely|unlikely|probabilit(?:y|ies)|probable|probably|risk score|risk rating|high negative impact)\b", re.I)
_CERTAIN = re.compile(r"\b(will|certainly|definitely|inevitabl\w+|guarantee\w*|undoubtedly)\b", re.I)
_MODAL = re.compile(r"\b(could|may|might|would|can|if)\b", re.I)
_IDS = re.compile(r"\b(?:[EPRF]\d{1,3}|[A-Z]{2,6}(?:-[A-Z0-9_]+)+-\d+)\b")  # evidence ids, and theme ids such as URS-KEY-04 or DSC-TIER_2-01
_STE_NAMES = ("ST Engineering", "STE")
_MINISTRY = re.compile(r"^(?:ministry|department|bureau)\b", re.I)  # a generic public-body name, used in many countries
_OPERATORS = re.compile(r"\b(?:site|filetype|inurl|intitle):|[()]|\bOR\b|\bAND\b|\bNOT\b")
SUPPORTS = frozenset({"EVENT", "CONNECTION"})
PARTS = ("what_happened", "event_quotes", "affected", "connection", "evidence", "hypothesis", "same_party")
VERDICTS = frozenset({"YES", "UNSURE", "NO"})


def _problems(text: str, haystack: str, part: str) -> list[str]:
    found: list[str] = []
    banned = _BANNED.search(text)
    if banned:
        found.append(f"BANNED_TERM: {part}: {banned.group(0).lower()}")
    if "http" in text.lower() or "www." in text.lower():
        found.append(f"URL_IN_TEXT: {part}")
    for token in numbers_in(_IDS.sub("", text)):
        if not has_number(token, haystack):
            found.append(f"UNGROUNDED_NUMBER: {part}: {token}")
    return found


def _known(value: Any, table: Any) -> bool:
    """A model value is a known id only when it is a string in the table. A list or a dict is unknown, and never raises."""
    return isinstance(value, str) and value in table


def _quote_problems(quote: Any, haystack: str, part: str, minimum: int) -> list[str]:
    if not isinstance(quote, str):
        return [f"BAD_SHAPE: {part}: a quote must be a string"]
    quote = quote.strip()
    if len(quote) < minimum:
        return [f"QUOTE_TOO_SHORT: {part}: a quote needs at least {minimum} characters: '{quote[:30]}'"]
    if normalise_text(quote) not in normalise_text(haystack):
        return [f"QUOTE_NOT_VERBATIM: {part}: {quote[:60]}"]
    return []


def _haystack(context: Context) -> str:
    themes = " ".join(f"{item['name']} {item.get('register_detail') or ''}" for item in context.themes.values())
    evidence = " ".join(item["text"] for item in context.evidence.values())
    counts = f"{context.entities_total} " + " ".join(str(path["targets_total"]) for path in context.paths.values())
    return f"{context.signal_text} {evidence} {themes} {counts}"


def check(card: Any, context: Context) -> list[str]:
    """The violations of a draft against the ontology context. An empty list means it may be finalised."""
    if not isinstance(card, dict):
        return ["BAD_SHAPE: shape: the card is not an object"]
    shape = {"what_happened": str, "event_quotes": list, "affected": dict, "connection": dict, "evidence": list, "hypothesis": dict}
    problems = [f"BAD_SHAPE: shape: {name}" for name, kind in shape.items() if not isinstance(card.get(name), kind)]
    if problems:
        return problems
    found: list[str] = []
    all_text = _haystack(context)

    party = card.get("same_party")
    if not isinstance(party, dict) or not isinstance(party.get("verdict"), str) or party["verdict"] not in VERDICTS or not isinstance(party.get("why"), str) or not isinstance(party.get("quote", ""), str):
        found.append("BAD_SHAPE: same_party: verdict must be YES, UNSURE, or NO, with why and quote as strings")
    else:
        quote = (party.get("quote") or "").strip()
        if party["verdict"] == "NO" and not quote:
            found.append("IDENTITY_NO_QUOTE: same_party: a NO needs the exact words of the text that point to another party")
        if quote:
            found += _quote_problems(quote, context.signal_text, "same_party", MIN_DOUBT_QUOTE)
        found += _problems(party["why"], all_text, "same_party")

    what = card["what_happened"].strip()
    if not what:
        found.append("EMPTY: what_happened")
    if len(what.split()) > MAX_WHAT_WORDS:
        found.append(f"TOO_LONG: what_happened: more than {MAX_WHAT_WORDS} words")
    found += _problems(what, context.signal_text, "what_happened")
    names = "|".join(re.escape(name) for name in sorted({*_STE_NAMES, *context.business_names}, key=len, reverse=True))
    for name in {match.group(0) for match in re.finditer(rf"(?<![\w&]){names}(?![\w&])", what)}:
        if name.casefold() not in normalise_text(context.signal_text):
            found.append(f"UNGROUNDED_NAME: what_happened: {name} is not in the signal text")
    quotes = [quote for quote in card["event_quotes"] if not (isinstance(quote, str) and not quote.strip())]
    if not quotes:
        found.append("NO_EVENT_QUOTE: event_quotes")
    for quote in quotes:
        found += _quote_problems(quote, context.signal_text, "event_quotes", MIN_QUOTE)

    affected = card["affected"]
    ids, reason = affected.get("entity_ids"), affected.get("why")
    if not isinstance(ids, list) or not isinstance(reason, str):
        found.append("BAD_SHAPE: affected: entity_ids must be a list and why a string")
    else:
        for entity_id in ids:
            if not _known(entity_id, context.entities):
                found.append(f"UNKNOWN_ENTITY: affected: {str(entity_id)[:40]}")
            elif context.entities[entity_id]["why"] not in SELECTABLE:
                found.append(f"ENTITY_NOT_SUPPORTED: affected: {entity_id} is a candidate by path only. The text names neither it nor its country")
        if not reason.strip():
            found.append("EMPTY: affected: why")
        elif len(reason.split()) > MAX_WHY_WORDS:
            found.append("TOO_LONG: affected: why")
        found += _problems(reason, all_text, "affected")

    connection = card["connection"]
    path_ids, reason = connection.get("path_ids"), connection.get("why")
    if not isinstance(path_ids, list) or not isinstance(reason, str):
        found.append("BAD_SHAPE: connection: path_ids must be a list and why a string")
    else:
        found += [f"UNKNOWN_PATH: connection: {str(path_id)[:40]}" for path_id in path_ids if not _known(path_id, context.paths)]
        if context.paths and not path_ids:
            found.append("NO_PATH_CITED: connection: the ontology graph supplied paths and none is cited")
        if not reason.strip():
            found.append("EMPTY: connection: why")
        elif len(reason.split()) > MAX_WHY_WORDS:
            found.append("TOO_LONG: connection: why")
        found += _problems(reason, all_text, "connection")

    cited_outlets = 0
    for item in card["evidence"]:
        if not isinstance(item, dict):
            found.append("BAD_SHAPE: evidence: an item is not an object")
            continue
        evidence = context.evidence.get(item.get("evidence_id")) if isinstance(item.get("evidence_id"), str) else None
        if evidence is None:
            found.append(f"UNKNOWN_EVIDENCE: evidence: {str(item.get('evidence_id'))[:40]}")
            continue
        if not isinstance(item.get("supports"), str) or item["supports"] not in SUPPORTS:
            found.append(f"BAD_SUPPORTS: evidence: {item.get('evidence_id')}")
        if evidence["kind"] != "REGISTER_FACT":
            cited_outlets += 1
            quote = item.get("quote")
            if not isinstance(quote, str) or not quote.strip():
                found.append(f"NO_QUOTE: evidence: {item.get('evidence_id')}")
            else:
                found += [problem.replace(": evidence:", f": evidence: {item.get('evidence_id')}:", 1) for problem in _quote_problems(quote, evidence["text"], "evidence", MIN_QUOTE)]
    if context.independent_outlets() and not cited_outlets:
        found.append("NO_EVIDENCE_CITED: evidence: the context holds independent outlets and none is cited")

    hypothesis = card["hypothesis"]
    text, theme_ids, hyp_paths = hypothesis.get("text"), hypothesis.get("theme_ids"), hypothesis.get("path_ids")
    if not isinstance(text, str) or not isinstance(theme_ids, list) or not isinstance(hyp_paths, list):
        found.append("BAD_SHAPE: hypothesis: text, theme_ids, and path_ids are required")
    elif text.strip():
        known_themes = {item["theme_id"] for item in context.view["all_themes"]}
        found += [f"UNKNOWN_THEME: hypothesis: {str(theme_id)[:40]}" for theme_id in theme_ids if not _known(theme_id, known_themes)]
        found += [f"UNKNOWN_PATH: hypothesis: {str(path_id)[:40]}" for path_id in hyp_paths if not _known(path_id, context.paths)]
        if not context.paths:
            found.append("HYPOTHESIS_WITHOUT_PATH: hypothesis: no ontology path connects this event to STE, so make no hypothesis")
        elif not hyp_paths:
            found.append("HYPOTHESIS_WITHOUT_PATH: hypothesis: cite the path the hypothesis relies on")
        if len(text.split()) > MAX_HYPOTHESIS_WORDS:
            found.append("TOO_LONG: hypothesis")
        if not _MODAL.search(text):
            found.append("NOT_CONDITIONAL: hypothesis: say what could happen, not what will")
        certain = _CERTAIN.search(text)
        if certain:
            found.append(f"CERTAINTY_CLAIM: hypothesis: {certain.group(0).lower()}")
        found += _problems(text, all_text, "hypothesis")
    elif theme_ids or hyp_paths:
        found.append("HYPOTHESIS_WITHOUT_TEXT: hypothesis: a basis is cited and no text is given")
    elif context.paths:
        found.append("EMPTY: hypothesis: the ontology gives a path to reason from")
    return found


def violation_parts(violations: list[str]) -> set[str]:
    parts: set[str] = set()
    for item in violations:
        pieces = item.split(": ")
        part = pieces[1] if len(pieces) > 1 else "shape"
        parts.add(part if part in PARTS or part == "shape" else "shape")
    return parts


def valid_follow_up(query: Any, context: Context) -> str | None:
    """A model-proposed search query, or None. It must be short, plain, and about this signal."""
    if not isinstance(query, str):
        return None
    cleaned = re.sub(r"\s+", " ", query).strip().strip('"')
    if not cleaned or len(cleaned) > 120 or len(cleaned.split()) > MAX_QUERY_WORDS or "http" in cleaned.lower() or _OPERATORS.search(cleaned):
        return None
    words = {word.casefold() for word in re.findall(r"[A-Za-z0-9&-]{4,}", cleaned)}
    own = {word.casefold() for word in re.findall(r"[A-Za-z0-9&-]{4,}", context.signal_text)}
    names = {word.casefold() for item in context.view["link"]["named"] for word in re.findall(r"[A-Za-z0-9&-]{4,}", item["name"])}
    return cleaned if len(words & (own | names)) >= 2 else None


def default_follow_up(context: Context) -> str:
    """The headline without its site suffix, cut to a short query. It finds other outlets that carry the same story."""
    title = re.split(r"\s+[|–—-]\s+", context.signal["title"])[0]
    title = re.sub(r"[\"“”]", "", title)
    return " ".join(title.split()[:MAX_QUERY_WORDS])


def confidence(context: Context, register_count: int | None = None, identity: Mapping[str, Any] | None = None, identity_failed: bool = False) -> dict[str, Any]:
    """Confidence in the link between the event and the STE register, by rule. It says nothing about outcomes.

    identity is the model's same_party answer after it passed the checks. It can only lower the result: a NO with a verified quote ends the link,
    an UNSURE with a verified quote caps the level at LOW, and an UNSURE without one, or an answer that failed its checks, keeps the level below HIGH.
    """
    link = context.signal["link"]
    rung = context.signal.get("rung")
    firm = [
        item for item in link["involves"]
        if item["strong"] and item["cls"] != "Place" and item["where"] in {"TITLE", "LEAD"} and item["alias_basis"] in {"REGISTER", "REVIEWED", "OVERLAY", "DERIVED"} and not item["ambiguous"]
    ]
    status = best_status(context)
    independent, support = context.independent_outlets(), context.connection_support()
    facts = context.register_facts() if register_count is None else register_count
    status_name = {3: "stated in the workbook", 2: "derived from stated facts", 1: "proposed, not validated", 0: "absent"}[status]
    reasons: list[str] = []
    if firm:
        reasons.append(f"Named in the {firm[0]['where'].lower()} as '{firm[0]['matched']}', a {firm[0]['alias_basis'].lower()} name.")
    named_weak = [item for item in link["involves"] if item["cls"] != "Place" and not (item["strong"] and item["where"] in {"TITLE", "LEAD"})]
    if named_weak and not firm:
        reasons.append("The name appears in the body only, or by a generated or ambiguous alias.")
    reasons.append(f"Best ontology path: {status_name}.")
    reasons.append(f"{independent} other outlet(s) carry the event, and {support} of them name the same party. {facts} register fact(s) support the path.")
    if status == 0:
        level = NOT_ESTABLISHED
        if rung == COMPETITOR:
            reasons.insert(0, "No path from this signal to an STE node exists in the register: the named competitor has no line of business there that matches an org chart LOB.")
        elif rung in {None, THEME}:
            reasons.insert(0, "No path from this signal to an STE node exists in the register. It is linked by a theme only.")
        else:
            reasons.insert(0, "No path from this signal to an STE node exists in the register.")
    elif rung in {DIRECT, COUNTERPARTY} and firm and status >= 3 and independent >= 1:
        level = HIGH
    elif (rung in {DIRECT, COUNTERPARTY, COMPETITOR} and (firm or independent >= 1) and status >= 2) or (rung == PERIMETER and independent >= 1 and status >= 2):
        level = MEDIUM
    else:
        level = LOW
    if level == HIGH and any(_MINISTRY.match(item["matched"]) for item in firm if item["cls"] in {"Customer", "Supplier"}):
        level = MEDIUM
        reasons.insert(0, "The name of a ministry is used in many countries, and the register does not state which one, so the level stays below high.")
    verdict = (identity or {}).get("verdict")
    quote = ((identity or {}).get("quote") or "").strip()
    if level != NOT_ESTABLISHED and verdict == "NO":
        level = NOT_ESTABLISHED
        reasons.insert(0, f"The model judges that the party in the text is not the register's, and quotes '{quote}'. The code checked that the quote is in the text.")
    elif level != NOT_ESTABLISHED and verdict == "UNSURE" and quote:
        level = LOW
        reasons.insert(0, f"The model could not confirm that the party in the text is the register's, and quotes '{quote}' as the reason. The code checked that the quote is in the text.")
    elif level == HIGH and (verdict == "UNSURE" or identity_failed):
        level = MEDIUM
        reasons.insert(0, "The model could not confirm that the party in the text is the register's, so the level stays below high." if verdict == "UNSURE" else "The identity check did not pass its rules, so the level stays below high.")
    return {
        "level": level, "reasons": reasons,
        "inputs": {
            "rung": rung, "firm_names": [item["node_id"] for item in firm], "best_path_status": status, "independent_outlets": independent, "outlets_naming_the_same_node": support,
            "register_facts": facts, "identity_verdict": verdict, "identity_check_failed": identity_failed,
        },
    }


def finalize(card: Mapping[str, Any] | None, context: Context, violations: list[str]) -> dict[str, Any]:
    """The card as it is shown. A part that still fails its checks is withheld, and the reason is recorded."""
    bad = violation_parts(violations) if violations else set()
    usable = isinstance(card, dict) and "shape" not in bad and "what_happened" not in bad and "event_quotes" not in bad
    withheld = sorted(bad)
    final: dict[str, Any] = {
        "finding_id": context.finding_id,
        "title": context.signal["title"], "publisher": context.signal.get("publisher"), "published_at": context.signal.get("published_at"), "url": context.signal["url"],
        "also_reported_by": [item["publisher"] for item in context.signal.get("also_reported_by") or [] if item.get("publisher")],
        "priority": context.signal.get("priority"), "rung": context.signal.get("rung"), "doc_type": context.signal.get("doc_type"),
        "lob": context.signal["link"].get("canonical_lob"),
        "status": "WITHHELD" if not usable else "PARTIAL" if withheld else "COMPLETE", "withheld": withheld, "violations": violations,
        "themes": [{key: item[key] for key in ("theme_id", "tier", "name", "where", "strong")} for item in context.themes.values()],
    }
    if not usable:
        final["q6"] = confidence(context, identity_failed=True)
        final["same_party"], final["namesake"], final["doubted"] = None, False, False
        return final
    affected = {} if "affected" in bad else card["affected"]
    chosen = [context.entities[entity_id] for entity_id in affected.get("entity_ids") or [] if entity_id in context.entities]
    cited = [] if "connection" in bad else [path_id for path_id in card["connection"].get("path_ids") or [] if path_id in context.paths]
    shown_paths = cited or list(context.paths)
    shown_edges = {(edge["src"], edge["rel"], edge["dst"]) for path_id in shown_paths for edge in context.paths[path_id]["edges"]}
    register = [item["text"] for item in context.evidence.values() if item["kind"] == "REGISTER_FACT" and tuple(item["edge"]) in shown_edges]
    party = None if "same_party" in bad else card.get("same_party")
    final["q6"] = confidence(context, len(register), party, identity_failed="same_party" in bad)
    final["same_party"] = {"verdict": party["verdict"], "why": party["why"].strip(), "quote": (party.get("quote") or "").strip() or None} if party else None
    final["namesake"] = bool(party and party["verdict"] == "NO")
    # A doubt counts when the model backs it with words from the text. The check already confirmed they are there.
    final["doubted"] = bool(party and (party["verdict"] == "NO" or (party["verdict"] == "UNSURE" and (party.get("quote") or "").strip())))
    items = []
    for item in [] if "evidence" in bad else card["evidence"]:
        evidence = context.evidence.get(item.get("evidence_id")) if isinstance(item, dict) else None
        if evidence is not None and evidence["kind"] != "REGISTER_FACT":
            items.append({
                "evidence_id": item["evidence_id"], "kind": evidence["kind"], "publisher": evidence["publisher"], "published_at": evidence["published_at"], "url": evidence["url"],
                "quote": item.get("quote") or None, "supports": item.get("supports"), "independent": evidence["independent"], "names_the_same_node": bool(evidence["names_node"]),
                "cosine": evidence["cosine"], "text": evidence["text"] if evidence["kind"] == "REGISTER_FACT" else None,
            })
    hypothesis = {} if "hypothesis" in bad else card["hypothesis"]
    final.update({
        "q1": {"what_happened": card["what_happened"].strip(), "quotes": [quote.strip() for quote in card["event_quotes"] if isinstance(quote, str) and len(quote.strip()) >= MIN_QUOTE]},
        "q2": {
            "register_scope": context.scope, "affected": [{key: entity[key] for key in ("entity_id", "name", "country", "lob_label", "why")} for entity in chosen],
            "why": None if "affected" in bad else affected.get("why", "").strip() or None, "candidates_total": context.entities_total,
            "candidates": [{key: entity[key] for key in ("entity_id", "name", "country", "lob_label", "why")} for entity in context.entities.values()], "gaps": context.gaps,
        },
        "q3": {"paths": [{key: context.paths[path_id][key] for key in ("path_id", "text", "status", "targets_total")} | {"basis": [edge["basis"] for edge in context.paths[path_id]["edges"]], "cited": path_id in cited} for path_id in shown_paths], "why": None if "connection" in bad else card["connection"].get("why", "").strip() or None},
        "q4": {
            "items": items,
            "register": register,
            "available": [
                {"evidence_id": key, "kind": item["kind"], "publisher": item["publisher"], "url": item["url"], "cosine": item["cosine"], "names_the_same_node": bool(item["names_node"]), "reason": item["reason"]}
                for key, item in context.evidence.items() if item["kind"] != "REGISTER_FACT"
            ],
            "independent_outlets": context.independent_outlets(),
            "outlets_naming_the_same_node": context.connection_support(),
            "register_facts": len(register),
        },
        "q5": {"hypothesis": (hypothesis.get("text") or "").strip() or None, "theme_ids": list(hypothesis.get("theme_ids") or []), "path_ids": list(hypothesis.get("path_ids") or [])},
    })
    return final
