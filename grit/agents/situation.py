"""The Situation Analyst: drafts one section of a State of the Situation document from report passages (spec 38).

The analyst sees passages by id (P1, P2, ...), each with its report, publisher, edition and page. It proposes text. The checks
decide what is valid: every sentence cites a supplied passage, every number appears in the passages that sentence cites, a
forecast names the report that makes it, and at least one quote is verbatim. A section that fails three times is withheld.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from grit.agents.checks import MIN_QUOTE, normalise, text_problems
from grit.agents.loop.llm import LoopError, call_json
from grit.agents.reader import CallLog
from grit.core.canonical import canonical_hash

ANALYST_VERSION = "situation-analyst-1"
MARK = re.compile(r"\[(P\d{1,3}(?:\s*,\s*P\d{1,3})*)\]")
FORECAST = re.compile(r"\b(will|projected|projects|projection|projections|forecasts?|forecasted|expected to|expects|is set to|are set to|anticipated|anticipates)\b", re.I)
NARRATIVE, UNCERTAINTIES, INDICATORS, IN_BRIEF = "NARRATIVE", "UNCERTAINTIES", "INDICATORS", "IN_BRIEF"
MIN_WORDS = {NARRATIVE: 150, IN_BRIEF: 50}
ATTEMPTS = 3  # a section is long, and every sentence is checked: it gets two redrafts with the violations


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    kind: str
    ask: str  # what the section must explain
    angle: str  # added to the topic question to retrieve passages for this section


SECTIONS = (
    Section("HISTORY", "History and how we got here", NARRATIVE,
            "How the situation developed: the longer history, the turning points, and the decisions and events that led to the present. "
            "Go back as far as the passages allow, keep the order of events clear, and name the report that states each turning point.",
            "history background origins turning points official record how it developed over past decades"),
    Section("DRIVERS", "Structural drivers", NARRATIVE,
            "The long-running forces behind the situation: economic, fiscal, political, security and technological. Explain how each works, not just that it exists.",
            "structural drivers underlying causes long-term trends forces shaping"),
    Section("ACTORS", "Key actors and their positions", NARRATIVE,
            "The governments, institutions, companies and alliances that shape the situation, what each wants, and what each is doing.",
            "key governments institutions companies actors strategies positions policies"),
    Section("STATE", "Where things stand", NARRATIVE,
            "The present position, as the reports describe it, with the key figures and the dates they refer to.",
            "current situation latest figures data outlook as of 2025 2026"),
    Section("UNCERTAINTIES", "Key uncertainties", UNCERTAINTIES,
            "The three to five open questions whose answers would most change the outlook. These seed the scenarios.",
            "uncertainties risks downside upside scenarios what could change the outlook"),
    Section("INDICATORS", "Indicators to watch", INDICATORS,
            "Four to eight observable indicators that would show the situation moving, with their latest reading where a report gives one. These seed the monthly monitoring.",
            "indicators metrics data to monitor signals early warning"),
)

SYSTEM = """You are the Situation Analyst in GRIT-Scan, a risk tracker for ST Engineering (STE). You draft one section of a
State of the Situation document: a deep, contextual account of a strategic topic for the CEO and the Chairman. It explains
history, structure and context, not this week's events. It is built only from the report passages supplied.

You receive: the topic and its question, the section to write and what it must explain, and passages (P1, P2, ...). Each
passage names its report, publisher, edition and page. Some passages will be off topic; ignore them.

Rules that always apply:
- Use only the supplied passages. Do not use your own knowledge. If the passages do not say it, do not write it.
- End every sentence with the ids of the passages that support it, for example [P2] or [P1, P4].
- Every number you write must appear in a passage that sentence cites.
- A forecast or projection belongs to the report that makes it. Name the publisher in that sentence: "The IMF projects ...",
  "According to PwC, ...". Never state a forecast as fact.
- Prefer the most recent edition when reports disagree, and say that they disagree.
- Do not write a URL, a page number, or the words severity, likelihood, probability, risk score, or risk rating.
- Write in plain, direct English for a board reader: short sentences, concrete nouns, no filler.
- Where the passages do not cover something the section needs, say so in "not_covered" instead of filling the gap.
- Copy at least one verbatim quote of 20 or more characters from a passage you cite into "quotes".
- Return only one JSON object. If you receive violations, fix exactly those and keep everything else.

For a NARRATIVE section, return:
{"paragraphs": ["two to five paragraphs, 250 to 450 words in all, each sentence ending with [P#]"],
 "quotes": [{"passage_id": "P1", "quote": "verbatim span"}],
 "not_covered": null or "one sentence on what this section needs that the passages do not cover"}

For an UNCERTAINTIES section, return:
{"items": [{"uncertainty": "an open question, without citation", "why_it_matters": "one or two sentences, each ending with [P#]"}],
 "quotes": [...], "not_covered": null or "..."}
Give three to five items.

For an INDICATORS section, return:
{"items": [{"indicator": "a short, observable measure", "reading": null or "its latest reading in the passages, ending with [P#]"}],
 "quotes": [...], "not_covered": null or "..."}
Give four to eight items. A reading is null when no passage gives one.

For an IN_BRIEF section, you receive the drafted sections instead of new analysis. Return:
{"paragraphs": ["one paragraph of three to five sentences that states the essential picture, each sentence ending with [P#]"],
 "quotes": [...], "not_covered": null}
Use only facts that appear in the drafted sections, with the same passage ids.
"""


_ABBREVIATION = re.compile(r"(?:\b(?:[A-Za-z]\.){2,}|\b(?:e\.g|i\.e|Dr|Mr|Ms|Mrs|No|Nos|vs|etc|Inc|Ltd|Co|Corp|St|approx|Fig)\.)$")


def sentences(text: str) -> list[str]:
    """Sentences, split before a capital, a digit, a currency sign, a quote or a bracket. An abbreviation such as 'U.S.' or
    'e.g.' does not end a sentence."""
    parts = [part for part in re.split(r"(?<=[.!?\]])\s+(?=[A-Z0-9$“\"(])", text.strip()) if part.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and _ABBREVIATION.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return [part.strip() for part in merged]


def marks(text: str) -> list[str]:
    found: list[str] = []
    for group in MARK.findall(text or ""):
        found += [part.strip() for part in group.split(",")]
    return found


def strip_marks(text: str) -> str:
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", MARK.sub("", text or ""))).strip()


def _haystack(passages: Mapping[str, Mapping[str, Any]], ids: Sequence[str]) -> str:
    return " ".join(str(passages[item]["text"]) for item in ids if item in passages)


def cited_problems(text: str, passages: Mapping[str, Mapping[str, Any]], where: str) -> list[str]:
    """Each sentence cites supplied passages, its numbers are in them, and a forecast names the report that makes it."""
    found: list[str] = []
    for sentence in sentences(text):
        ids = marks(sentence)
        short = strip_marks(sentence)[:70]
        if not ids:
            found.append(f"UNCITED: {where}: '{short}' needs [P#] ids.")
            continue
        unknown = [item for item in ids if item not in passages]
        if unknown:
            found.append(f"UNKNOWN_PASSAGE: {where}: {', '.join(unknown)} was not supplied.")
        found += text_problems(strip_marks(sentence), _haystack(passages, ids), where)
        forecast = FORECAST.search(strip_marks(sentence))
        if forecast:
            names = [alias for item in ids if item in passages for alias in passages[item].get("aliases") or []]
            if "according to" not in sentence.casefold() and not any(alias.casefold() in sentence.casefold() for alias in names):
                found.append(f"UNATTRIBUTED_FORECAST: {where}: '{short}' uses '{forecast.group(0)}'. Name the report's publisher in the sentence.")
    return found


def quote_problems(quotes: Any, passages: Mapping[str, Mapping[str, Any]], cited: set[str]) -> list[str]:
    if not isinstance(quotes, list) or not quotes:
        return ["NO_QUOTE: quotes: copy at least one verbatim quote from a passage you cite."]
    found: list[str] = []
    for item in quotes:
        if not isinstance(item, Mapping) or item.get("passage_id") not in passages:
            found.append("UNKNOWN_PASSAGE: quotes: each quote names a supplied passage_id.")
            continue
        quote = str(item.get("quote") or "")
        if len(quote.strip()) < MIN_QUOTE:
            found.append(f"QUOTE_TOO_SHORT: quotes: a quote needs {MIN_QUOTE} or more characters.")
        elif normalise(quote) not in normalise(str(passages[item["passage_id"]]["text"])):
            found.append(f"QUOTE_NOT_VERBATIM: quotes: '{quote[:60]}' is not in {item['passage_id']}.")
        elif item["passage_id"] not in cited:
            found.append(f"QUOTE_UNCITED: quotes: {item['passage_id']} is quoted but no sentence cites it.")
    return found


def check_section(draft: Any, kind: str, passages: Mapping[str, Mapping[str, Any]]) -> list[str]:
    if not isinstance(draft, Mapping):
        return ["BAD_SHAPE: the draft is not an object"]
    found: list[str] = []
    not_covered = draft.get("not_covered")
    if not_covered not in (None, "") and not isinstance(not_covered, str):
        found.append("BAD_SHAPE: not_covered is a sentence or null")
    elif not_covered:
        found += text_problems(strip_marks(str(not_covered)), "", "not_covered")
    texts: list[str] = []
    if kind in (NARRATIVE, IN_BRIEF):
        paragraphs = draft.get("paragraphs")
        if not isinstance(paragraphs, list) or not all(isinstance(item, str) for item in paragraphs):
            return found + ["BAD_SHAPE: paragraphs must be a list of strings"]
        limit = 1 if kind == IN_BRIEF else 5
        if len(paragraphs) > limit:
            found.append(f"BAD_COUNT: paragraphs: at most {limit}.")
        texts = [item for item in paragraphs if item.strip()]
        words = sum(len(strip_marks(item).split()) for item in texts)
        if texts and words < MIN_WORDS[kind] and not not_covered:
            found.append(f"TOO_THIN: paragraphs: {words} words. Write at least {MIN_WORDS[kind]}, or say in not_covered what the passages lack.")
        if not texts and not not_covered:
            found.append("EMPTY: write the section, or say in not_covered why the passages cannot support it.")
        for number, text in enumerate(texts, start=1):
            found += cited_problems(text, passages, f"paragraph {number}")
    else:
        items = draft.get("items")
        low, high = (3, 5) if kind == UNCERTAINTIES else (4, 8)
        if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
            return found + ["BAD_SHAPE: items must be a list of objects"]
        if not low <= len(items) <= high and not (not items and not_covered):
            found.append(f"BAD_COUNT: items: {low} to {high}.")
        for number, item in enumerate(items, start=1):
            where = f"item {number}"
            head = item.get("uncertainty" if kind == UNCERTAINTIES else "indicator")
            if not isinstance(head, str) or not head.strip():
                found.append(f"BAD_SHAPE: {where}: needs its {'uncertainty' if kind == UNCERTAINTIES else 'indicator'}.")
                continue
            body = item.get("why_it_matters" if kind == UNCERTAINTIES else "reading")
            # A heading carries no citation, so its numbers must be in the passages its own text cites.
            support = _haystack(passages, marks(body)) if isinstance(body, str) else ""
            found += text_problems(head, support, where) if not marks(head) else [f"MARK_IN_TEXT: {where}: keep [P#] out of the heading."]
            if kind == UNCERTAINTIES and (not isinstance(body, str) or not body.strip()):
                found.append(f"BAD_SHAPE: {where}: needs why_it_matters.")
            elif body is not None and not isinstance(body, str):
                found.append(f"BAD_SHAPE: {where}: reading is a sentence or null.")
            elif isinstance(body, str) and body.strip():
                texts.append(body)
                found += cited_problems(body, passages, where)
    cited = {item for text in texts for item in marks(text)}
    if texts:
        found += quote_problems(draft.get("quotes"), passages, cited)
    return found


def draft_section(
    client: Any, model: str, *, topic: Mapping[str, Any], section: Section, passages: Mapping[str, Mapping[str, Any]], drafted: Sequence[Mapping[str, Any]] = ()
) -> tuple[dict[str, Any], CallLog]:
    """Returns {status, draft, violations}. status is DRAFTED, NOT_COVERED, or WITHHELD. drafted feeds the IN_BRIEF section."""
    payload: dict[str, Any] = {
        "topic": dict(topic), "section": {"title": section.title, "kind": section.kind, "must_explain": section.ask},
        "passages": {key: {field: value[field] for field in ("report", "publisher", "edition", "text")} for key, value in passages.items()},
    }
    if drafted:
        payload["drafted_sections"] = [dict(item) for item in drafted]
    log = CallLog("SituationAnalyst", ANALYST_VERSION, model, f"{topic.get('id')}:{section.key}", canonical_hash(payload))
    if not passages:
        log.status = "SKIPPED_NO_SOURCES"
        return {"status": "NOT_COVERED", "draft": {"paragraphs": [], "items": [], "quotes": [], "not_covered": "The library holds no report for this topic."}, "violations": []}, log
    violations: list[str] = []
    for _attempt in range(ATTEMPTS):
        log.attempts += 1
        try:
            draft = call_json(client, model=model, system=SYSTEM, payload=payload, retries=1, pause=2.0)
        except LoopError as exc:
            log.status = "LLM_ERROR"
            log.violations.append(f"LLM_ERROR: {exc}")
            break
        log.output_hash = canonical_hash(draft)
        violations = check_section(draft, section.kind, passages)
        if not violations:
            log.status = "VALIDATED"
            empty = not (draft.get("paragraphs") or draft.get("items"))
            return {"status": "NOT_COVERED" if empty else "DRAFTED", "draft": dict(draft), "violations": []}, log
        log.violations.extend(violations)
        payload = payload | {"previous_draft": draft, "violations": violations}
    if log.status != "LLM_ERROR":
        log.status = "WITHHELD"
    return {"status": "WITHHELD", "draft": None, "violations": violations or log.violations}, log
