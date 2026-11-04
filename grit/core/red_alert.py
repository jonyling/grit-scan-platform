"""RED alert rule. Pure: no I/O, no model.

A signal is a RED candidate when a governed pattern hits its title or lead and the event is corroborated, or when the harm
scorer returns a configured verdict for it. A RED candidate is a draft. A daily run sends that draft to the R&A duty team
when mail is configured. A named person forwards or dismisses it. A weekly run does not send it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

LEAD_CHARS = 600
TAIWAN = re.compile(r"(?<![\w])(Taiwan|Taiwanese|Taipei|Kaohsiung|Taichung|Taoyuan|Tainan|Hualien)(?![\w])")


@dataclass(frozen=True)
class RedResult:
    red: bool
    rule_id: str | None
    label: str | None
    matched: str | None
    reasons: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


def _phrase(phrase: str, text: str) -> re.Match[str] | None:
    body = r"\s+".join(re.escape(part) for part in phrase.split())
    flags = 0 if phrase.isupper() else re.IGNORECASE
    return re.search(rf"(?<![\w]){body}(?![\w])", text, flags)


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(mark, 0, start) for mark in (". ", "\n", "? ", "! ", " - ", " | "))
    rights = [index for index in (text.find(mark, end) for mark in (". ", "\n", "? ", "! ", " - ", " | ")) if index >= 0]
    return text[left + 1 : min(rights) if rights else len(text)]


def _speculative(sentence: str, words: Sequence[str]) -> str | None:
    for word in words:
        if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", sentence, re.IGNORECASE):
            return word
    return None


# Common short forms of the register's country names, so "Saudi forces" counts as Saudi Arabia.
COUNTRY_ALIASES = {
    "Saudi Arabia": ("Saudi",), "USA": ("United States", "U.S.", "US"), "Abu Dhabi": ("UAE", "United Arab Emirates"),
    "Dubai": ("UAE", "United Arab Emirates"), "Hong Kong": ("HK",),
}


def names_jurisdiction(text: str, jurisdictions: Sequence[str]) -> str | None:
    """The first jurisdiction where the pack has entities that the text names, or None."""
    for country in jurisdictions:
        for name in (country, *COUNTRY_ALIASES.get(country, ())):
            if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text):
                return country
    return None


def red_check(
    record: Mapping[str, Any],
    *,
    pack: str,
    patterns: Mapping[str, Any],
    corroboration: int,
    high_rated_source: bool,
    jurisdictions: Sequence[str] = (),
) -> RedResult:
    """jurisdictions: the countries where the pack has entities. A rule with requiresJurisdiction needs one of them named in
    the title or lead, so an event elsewhere is not a RED candidate for this business."""
    title = str(record.get("title") or "")
    lead = f"{title}\n{str(record.get('text') or '')[:LEAD_CHARS]}"
    whole = f"{title}\n{record.get('text') or ''}"
    verdict = record.get("harm_verdict")
    if verdict and verdict in set(patterns.get("harmVerdicts") or ()):
        return RedResult(True, "RED-HARM-VERDICT", "Harm scorer verdict", verdict, (f"The harm scorer returned {verdict}.",))
    corroborated = corroboration >= 2 or high_rated_source
    for rule in patterns.get("patterns") or []:
        if pack not in (rule.get("packs") or [pack]):
            continue
        if rule.get("requiresRung") and record.get("rung") not in rule["requiresRung"]:
            continue
        if rule.get("requiresTaiwan") and not TAIWAN.search(whole):
            continue
        if rule.get("requiresJurisdiction") and not names_jurisdiction(lead, jurisdictions):
            continue
        hit: str | None = None
        guarded: str | None = None
        matches = [(phrase, _phrase(phrase, lead)) for phrase in rule.get("phrases") or []]
        if rule.get("regex"):
            matches.append((None, re.search(rule["regex"], lead, re.IGNORECASE)))
        for phrase, match in matches:
            if match is None:
                continue
            word = _speculative(_sentence(lead, match.start(), match.end()), rule.get("excludeInSentence") or [])
            if word:
                guarded = guarded or f"'{match.group(0)}' sits in a sentence with '{word}'"
                continue
            hit = phrase or match.group(0)
            break
        if hit is None:
            if guarded:
                return RedResult(False, rule["ruleId"], rule["label"], None, (f"Pattern {rule['ruleId']} matched, but {guarded}: a forecast or assessment, not the event. Not RED.",))
            continue
        if not corroborated:
            return RedResult(False, rule["ruleId"], rule["label"], hit, (f"Pattern {rule['ruleId']} matched '{hit}', but one unrated outlet carries it. Not RED until corroborated.",))
        return RedResult(True, rule["ruleId"], rule["label"], hit, (f"Pattern {rule['ruleId']} matched '{hit}' in the title or lead.", f"Corroboration: {corroboration} outlet(s); HIGH-rated source: {'yes' if high_rated_source else 'no'}."))
    return RedResult(False, None, None, None, ())


def red_recipients(profiles: Sequence[Any], pack: str) -> list[str]:
    """Profile ids that receive a RED draft for this pack."""
    return [profile.profile_id for profile in profiles if profile.receives_red and pack in (profile.red_alerts.get("packs") or profile.packs)]
