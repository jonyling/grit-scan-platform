"""Deterministic checks on a Co-Pilot answer (spec 40, section 5). Pure: no model, no network.

The answer has a fixed shape: answer, evidence, implications for STE, what is uncertain, and an optional next step. Each
sentence of the answer, the evidence and the implications cites supplied source ids. Every number is in a cited source. A
forecast names the source that makes it. An inference about STE that no brief has validated reads as a possibility. No
governed score word and no URL appear anywhere.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from grit.agents.checks import BANNED, text_problems
from grit.agents.situation import FORECAST, sentences

MARK = re.compile(r"\[([SBRDGW]\d{1,3}(?:\s*,\s*[SBRDGW]\d{1,3})*)\]")
MARKUP = re.compile(r"\]\(|//|<\s*[a-zA-Z/!]|javascript:", re.I)  # a link, a protocol-relative URL, or an HTML tag
IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*\b")  # codes in capitals: STE-GC-01, H200, HBM3E; only those with a digit are removed
HEDGE = re.compile(r"\b(could|may|might|would|possibly|potentially|if)\b", re.I)
PARTS = ("answer", "evidence", "implications", "uncertain", "next_step")
LIMITS = {"short": {"answer": 2, "evidence": 4}, "standard": {"answer": 3, "evidence": 8}}


def marks(text: str) -> list[str]:
    found: list[str] = []
    for group in MARK.findall(text or ""):
        found += [part.strip() for part in group.split(",")]
    return found


def strip_marks(text: str) -> str:
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", MARK.sub("", text or ""))).strip()


def split_lines(value: Any) -> list[str] | None:
    """A part as lines. A string with line breaks is several lines; a leading bullet is dropped. None for a bad shape."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s+", "", line).strip() for item in value for line in item.splitlines() if line.strip()]


def _haystack(sources: Mapping[str, Any], ids: list[str]) -> str:
    return " ".join(sources[item].text for item in ids if item in sources)


def _plain(text: str, dates: Sequence[Any] = ()) -> str:
    """The text the number check reads: citation marks removed, codes in capitals that carry a digit (STE-GC-01, H200)
    removed, and phrases naming one of the given dates (19 October 2026, 2026-10-19) removed. Every other number stays."""
    text = IDENTIFIER.sub(lambda match: " " if any(char.isdigit() for char in match.group(0)) else match.group(0), strip_marks(text))
    for day in dates:
        month = day.strftime("%B")
        for pattern in (rf"\b{day.day}\s+{month}(?:\s+{day.year})?\b", rf"\b{month}\s+{day.day}(?:,?\s+{day.year})?\b", rf"\b{day.isoformat()}\b"):
            text = re.sub(pattern, " ", text, flags=re.I)
    return text


def answer_dates(today: Any) -> tuple[Any, ...]:
    """Today and yesterday: dates an answer may name without a source, such as "since 19 October"."""
    from datetime import timedelta

    return (today, today - timedelta(days=1))


def check_answer(draft: Any, sources: Mapping[str, Any], *, length: str = "standard", dates: Sequence[Any] = ()) -> list[str]:
    """sources: id -> Source (with text and names). dates: dates a sentence may name without a source (today and
    yesterday). An empty list means the answer may be shown."""
    if not isinstance(draft, Mapping):
        return ["BAD_SHAPE: the answer is a JSON object with answer, evidence, implications, uncertain and next_step."]
    problems: list[str] = []
    parts: dict[str, list[str]] = {}
    for key in PARTS:
        lines = split_lines(draft.get(key))
        if lines is None:
            problems.append(f"BAD_SHAPE: {key} is a string or a list of strings.")
            lines = []
        parts[key] = lines
    if not parts["answer"]:
        return problems + ["BAD_SHAPE: answer is empty. Answer the question directly in one to three sentences."]
    limits = LIMITS.get(length, LIMITS["standard"])
    answer_sentences = [sentence for line in parts["answer"] for sentence in sentences(line)]
    if len(answer_sentences) > limits["answer"]:
        problems.append(f"TOO_LONG: answer has {len(answer_sentences)} sentences; at most {limits['answer']}.")
    if len(parts["evidence"]) > limits["evidence"]:
        problems.append(f"TOO_LONG: evidence has {len(parts['evidence'])} lines; at most {limits['evidence']}.")
    nothing_found = draft.get("nothing_found") is True
    if sources and not parts["evidence"] and not nothing_found:
        problems.append("NO_EVIDENCE: list the facts behind the answer under evidence, each with its source ids.")
    for key, lines in parts.items():
        for number, line in enumerate(lines, start=1):
            if MARKUP.search(line):
                problems.append(f"MARKUP_IN_TEXT: {key} {number}: write plain sentences, with no link, URL or HTML.")
    for key in ("answer", "evidence", "implications"):
        for number, line in enumerate(parts[key], start=1):
            for sentence in sentences(line):
                where = f"{key} {number}"
                short = strip_marks(sentence)[:70]
                ids = marks(sentence)
                if not ids:
                    if nothing_found and key == "answer":
                        # An uncited "nothing found" answer still carries no number, forecast, score word or URL.
                        problems += text_problems(_plain(sentence, dates), "", where)
                        if FORECAST.search(sentence):
                            problems.append(f"UNATTRIBUTED_FORECAST: {where}: a nothing-found answer makes no forecast.")
                    else:
                        problems.append(f"UNCITED: {where}: '{short}' needs source ids such as [S1] or [B2].")
                    continue
                unknown = [item for item in ids if item not in sources]
                if unknown:
                    problems.append(f"UNKNOWN_SOURCE: {where}: {', '.join(unknown)} was not supplied.")
                problems += text_problems(_plain(sentence, dates), _haystack(sources, ids), where)
                forecast = FORECAST.search(strip_marks(sentence))
                if forecast and key != "implications":
                    names = [name for item in ids if item in sources for name in sources[item].names]
                    if "according to" not in sentence.casefold() and not any(name.casefold() in sentence.casefold() for name in names if len(name) > 2):
                        named = ", ".join(dict.fromkeys(name for item in ids if item in sources for name in sources[item].names[:1])) or "the source"
                        problems.append(f"UNATTRIBUTED_FORECAST: {where}: '{short}' uses '{forecast.group(0)}'. Begin the sentence 'According to {named}, ...', or state only what has happened.")
                if key == "implications" and not any(sources[item].kind == "STATE" for item in ids if item in sources) and not HEDGE.search(sentence):
                    problems.append(f"UNHEDGED_INFERENCE: {where}: '{short}' is an inference no brief has validated. Write it as a possibility (could, may, might) or cite the brief [B#].")
    everything = " ".join(_haystack(sources, list(sources)).split())
    for key in ("uncertain", "next_step"):
        for number, line in enumerate(parts[key], start=1):
            unknown = [item for item in marks(line) if item not in sources]
            if unknown:
                problems.append(f"UNKNOWN_SOURCE: {key} {number}: {', '.join(unknown)} was not supplied.")
            problems += text_problems(_plain(line, dates), everything, f"{key} {number}")
    if BANNED.search(str(draft.get("title") or "")):
        problems.append("BANNED_TERM: title")
    return problems
