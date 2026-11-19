"""Deterministic checks on agent drafts. Pure: no model, no network.

An agent sees sources by id (S1, S2, ...) and never a URL. Its draft must cite those ids, quote them verbatim, and use only
numbers its cited sources contain. Rendering adds outlet names, dates and links from the pool, never from the model.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from grit.core.verify_text import has_number, numbers_in

MIN_QUOTE = 20
BANNED = re.compile(r"\b(severity|likelihood|probabilit(?:y|ies)|risk score|risk rating|high negative impact)\b", re.I)
SOURCE_MARK = re.compile(r"\[(S\d{1,3}(?:\s*,\s*S\d{1,3})*)\]")
_SPACE = re.compile(r"\s+")
# A statement about the business's own state, or the escalation rule. A news page cannot attest to either, so it carries no
# citation: "No internal impact is confirmed", "would raise the status to RED".
INTERNAL_CLAIM = re.compile(
    r"\binternal impact\b|\bno (?:internal )?impact (?:is|was|has been) (?:yet )?confirmed\b"
    r"|\b(?:raise[sd]?|raising)\b[^.!?]{0,40}?\bto (?-i:RED)\b|\b(?:become|becomes|turn|turns|move|moves|go|goes) (?:to )?(?-i:RED)\b",
    re.IGNORECASE)


def normalise(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"').replace("\u2013", "-").replace("\u2014", "-")
    return _SPACE.sub(" ", text).strip().casefold()


def marked_ids(text: str) -> list[str]:
    ids: list[str] = []
    for group in SOURCE_MARK.findall(text or ""):
        ids.extend(part.strip() for part in group.split(","))
    return ids


def strip_marks(text: str) -> str:
    text = SOURCE_MARK.sub("", text or "")
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def text_problems(text: str, haystack: str, where: str) -> list[str]:
    """Banned terms, URLs, and numbers that the cited text does not contain."""
    found: list[str] = []
    banned = BANNED.search(text)
    if banned:
        found.append(f"BANNED_TERM: {where}: '{banned.group(0)}' is a governed score word. Describe the event instead.")
    if "http" in text.lower() or "www." in text.lower():
        found.append(f"URL_IN_TEXT: {where}: do not write a URL.")
    for token in numbers_in(strip_marks(text)):
        if not has_number(token, haystack):
            found.append(f"UNGROUNDED_NUMBER: {where}: {token} is not in the cited sources.")
    return found


# Local S# marks (reader drafts) and SIG- marks (weekly narrative).
_ANY_CITE = re.compile(
    r"\[((?:S\d{1,3}|SIG-[0-9a-f]{16})(?:\s*,\s*(?:S\d{1,3}|SIG-[0-9a-f]{16}))*)\]"
)
_DAY_TOKEN = re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b", re.I)


# Actors whose statements are claims, not established facts: armed groups and the people behind cyberattacks.
CLAIMANT = (r"houthis?|huthis?|ansar allah|(?:iran's |islamic )?revolutionary guards?(?: corps)?|irgc|hamas|hezbollah|hizbollah|"
            r"(?:[A-Z][\w-]+ )?ransomware (?:group|gang|operators?)|threat actors?|hacker (?:group|collective)s?|hacktivists?")
_CLAIMANT = re.compile(rf"\b(?:{CLAIMANT})\b", re.IGNORECASE)
# The claimant as the one acting: "Houthis targeted", "the ransomware group stole".
_CLAIMANT_ACTS = re.compile(
    rf"\b(?:{CLAIMANT})\b(?:\s+(?:forces|fighters|militants|rebels|group|gang|members|operators))?\s+(?:have\s+|has\s+)?"
    r"(?:fired|launched|targeted|struck|hit|attacked|seized|captured|downed|shot|destroyed|sank|breached|stole|exfiltrated|leaked|encrypted|"
    r"compromised|took|carried out)\b", re.IGNORECASE)
_CLAIM_WORD = re.compile(r"\b(?:claim(?:s|ed|ing)?|said|says|say|stated|states|according to|alleg(?:e|es|ed|edly|ing)|announced|"
                         r"purport(?:s|ed|edly)?|asserted)\b", re.IGNORECASE)
_CONFIRMS_CLAIM = re.compile(rf"\b(?:{CLAIMANT})\b\W+(?:\w+\W+){{0,3}}?confirm\w*", re.IGNORECASE)
# A deal the page says is near, written as done.
_NEAR_DEAL = re.compile(r"\b(?:nears?|nearing|close to (?:a |an |the )?(?:deal|agreement|contract|signing)|in (?:advanced |final )?talks|"
                        r"poised to|set to (?:sign|win|award|clinch)|expected to (?:sign|award|win)|edges? closer|on the verge of|"
                        r"in negotiations?|negotiating|shortlisted|preferred (?:bidder|supplier))\b", re.IGNORECASE)
_DONE_DEAL = re.compile(r"\b(signed|awarded|won|secured|clinched|sealed|finali[sz]ed|concluded|inked)\b", re.IGNORECASE)
_DONE_STEM = {"signed": "sign", "awarded": "award", "won": "win", "secured": "secur", "clinched": "clinch", "sealed": "seal",
              "finalized": "finali", "finalised": "finali", "concluded": "conclu", "inked": "ink"}


def claim_problems(claim: str, pages: str, where: str) -> list[str]:
    """A claimant's statement written as fact, or confirmed; a deal the cited page calls near, written as done."""
    found: list[str] = []
    acting = _CLAIMANT_ACTS.search(claim)
    if acting and not _CLAIM_WORD.search(claim):
        found.append(f"CLAIM_AS_FACT: {where}: '{acting.group(0)}' states as fact what an armed group or attacker says it did. "
                     "Label it as their claim: 'X claimed it ...', 'X said ...' or 'according to X'.")
    confirmed = _CONFIRMS_CLAIM.search(claim)
    if confirmed:
        found.append(f"CLAIM_CONFIRMED: {where}: '{confirmed.group(0)}' calls a claim confirmed. Write 'claimed' unless an "
                     "independent party confirms it, and name that party.")
    done = _DONE_DEAL.search(claim)
    if done and _NEAR_DEAL.search(pages) and _DONE_STEM[done.group(1).casefold()] not in pages.casefold():
        found.append(f"NEAR_DEAL_AS_DONE: {where}: '{done.group(0)}' writes as done a deal the cited page says is only near or in "
                     "talks. Write it as the page does: 'nears', 'in talks', 'expected to'.")
    return found


def sentence_cite_problems(text: str, sources: Mapping[str, Mapping[str, Any]], where: str) -> list[str]:
    """Each cited sentence must be supported by the page(s) it cites: dates, numbers, and distinctive names."""
    from grit.core.headlines import distinctive_terms, identifiers

    if not isinstance(text, str) or not text.strip() or not isinstance(sources, Mapping):
        return []
    found: list[str] = []
    last = 0
    for mark in _ANY_CITE.finditer(text):
        claim = text[last:mark.start()].strip(" .!?\n")
        last = mark.end()
        if not claim:
            continue
        refs = [part.strip() for part in mark.group(1).split(",")]
        pages = " ".join(
            f"{(sources.get(ref) or {}).get('title') or ''} {(sources.get(ref) or {}).get('text') or ''} "
            f"{(sources.get(ref) or {}).get('date') or ''}"
            for ref in refs
        )
        if INTERNAL_CLAIM.search(_last_sentence(claim)):
            found.append(f"INTERNAL_CLAIM_CITED: {where}: '{claim[:80]}' states the business's own status or the RED rule, which no news "
                         "page can confirm. Write it without a citation, or cite only the news fact it rests on.")
            continue
        found += claim_problems(claim, pages, where)
        folded = normalise(pages)
        if not folded:
            found.append(f"UNSUPPORTED_CITE: {where}: '{claim[:80]}' cites {', '.join(refs)}, which have no stored text.")
            continue
        for match in _DAY_TOKEN.finditer(claim):
            day = f"{match.group(1)} {match.group(2)[:3]}"
            if normalise(day) not in folded and normalise(match.group(0)) not in folded:
                found.append(f"UNSUPPORTED_DATE: {where}: '{match.group(0)}' is not on the cited page(s) {', '.join(refs)}.")
        for token in numbers_in(claim):
            if not has_number(token, pages):
                found.append(f"UNSUPPORTED_NUMBER: {where}: {token} is not on the cited page(s) {', '.join(refs)}.")
        names = distinctive_terms(claim) | identifiers(claim)
        missing = sorted(name for name in names if len(name) >= 5 and name not in distinctive_terms(pages) and name not in identifiers(pages)
                         and normalise(name) not in folded)
        # Require at least one distinctive claim word to appear when the claim asserts several; reject when none do.
        if names and len(missing) == len(names):
            sample = ", ".join(missing[:4])
            found.append(f"UNSUPPORTED_NAME: {where}: '{claim[:80]}' cites {', '.join(refs)}, which do not state {sample}.")
    return found


def _last_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s+", text.strip())[-1]


def quote_problems(quotes: Any, sources: Mapping[str, Mapping[str, Any]], where: str, allowed: Iterable[str] | None = None) -> list[str]:
    found: list[str] = []
    if not isinstance(quotes, list):
        return [f"BAD_SHAPE: {where}: quotes must be a list"]
    allowed_ids = set(allowed) if allowed is not None else set(sources)
    for item in quotes:
        if not isinstance(item, Mapping):
            found.append(f"BAD_SHAPE: {where}: each quote is an object with source_id and quote")
            continue
        source_id, quote = item.get("source_id"), item.get("quote")
        if source_id not in sources or source_id not in allowed_ids:
            found.append(f"UNKNOWN_SOURCE: {where}: quote cites {source_id}, which is not a cited source.")
            continue
        if not isinstance(quote, str) or len(quote.strip()) < MIN_QUOTE:
            found.append(f"QUOTE_TOO_SHORT: {where}: a quote needs {MIN_QUOTE} or more characters.")
            continue
        if normalise(quote) not in normalise(f"{sources[source_id].get('title') or ''} {sources[source_id].get('text') or ''}"):
            found.append(f"QUOTE_NOT_VERBATIM: {where}: '{quote[:60]}' is not in {source_id}.")
    return found


def source_problems(source_ids: Any, sources: Mapping[str, Any], where: str) -> list[str]:
    if not isinstance(source_ids, list) or not all(isinstance(item, str) for item in source_ids):
        return [f"BAD_SHAPE: {where}: source_ids must be a list of ids"]
    return [f"UNKNOWN_SOURCE: {where}: {item} was not supplied." for item in source_ids if item not in sources]


def haystack(sources: Mapping[str, Mapping[str, Any]], ids: Sequence[str]) -> str:
    return " ".join(f"{sources[item].get('title') or ''} {sources[item].get('text') or ''}" for item in ids if item in sources)
