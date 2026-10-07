"""Event-scale cues for one candidate. Pure: phrase rules and a number parser, no model, no score.

A cue is a fact the text states. It is not a severity, a likelihood, or a forecast.

  FIGURE_1M   the text states an amount of at least one million in USD, SGD, EUR, GBP, AUD, CAD, or CHF. The amount is read as stated.
              No exchange rate is applied. An amount in another currency is listed and not counted, because a million of it
              is not a comparable size. An amount next to a market projection is listed and not counted.
  GOV_ACTION  a regulator or government instrument or act: sanctions, tariffs, export controls, a ban, a fine, an order, a mandate, a probe.
  WAR         armed conflict: war with a named party, invasion, airstrike, missile strike, blockade, ceasefire.
  PANDEMIC    a pandemic, epidemic, outbreak, or lockdown that is current, not recalled ("post-pandemic" does not count).

The action cues read the title and the lead only. A phrase deep in the body is background.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

FIGURE_MIN = 1_000_000
LEAD_CHARS = 600
COUNTED_CURRENCIES = frozenset({"USD", "SGD", "EUR", "GBP", "AUD", "CAD", "CHF"})
_UNITS = {"thousand": 1e3, "k": 1e3, "million": 1e6, "mn": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12, "tn": 1e12}
_PREFIX = {
    "US$": "USD", "USD": "USD", "$": "USD", "S$": "SGD", "SGD": "SGD", "A$": "AUD", "AUD": "AUD", "C$": "CAD", "CAD": "CAD",
    "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP", "CHF": "CHF", "NT$": "TWD", "TWD": "TWD", "HK$": "HKD", "HKD": "HKD",
    "RMB": "CNY", "CNY": "CNY", "¥": "JPY", "JPY": "JPY", "INR": "INR", "₹": "INR", "RS": "INR", "MYR": "MYR", "RM": "MYR", "THB": "THB",
    "฿": "THB", "KRW": "KRW", "₩": "KRW",
}
_SUFFIX = {"us dollars": "USD", "us dollar": "USD", "dollars": "USD", "dollar": "USD", "euros": "EUR", "euro": "EUR", "pounds": "GBP", "pound": "GBP"}
_PREFIX_PATTERN = "|".join(sorted((re.escape(key) for key in _PREFIX), key=len, reverse=True))
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_UNIT_PATTERN = r"million|billion|trillion|thousand|mn|bn|tn|m|b|k"
_FIGURE = re.compile(
    rf"(?<![\w$])(?P<cur>{_PREFIX_PATTERN})\s?(?P<num>{_NUMBER})(?:\s?(?:-|–|to)\s?(?P<num2>{_NUMBER}))?(?:\s?(?P<unit>{_UNIT_PATTERN})\b)?"
    rf"|(?<![\w$])(?P<snum>{_NUMBER})\s?(?P<sunit>million|billion|trillion)\s(?P<scur>US dollars?|dollars?|euros?|pounds?)\b",
    re.IGNORECASE,
)
_PROJECTION = re.compile(r"\bmarket\b|\bCAGR\b|\bforecast|\bprojected\b|\bprojection|\bby 20[2-3]\d\b|\bcompound annual\b", re.IGNORECASE)

_PAST = r"(?<!post-)(?<!post )(?<!pre-)(?<!pre )(?<!since the )(?<!during the )(?<!after the )(?<!before the )"
# An agency acronym matches in capitals only: "Doe" and "mom" are ordinary words.
_ACTOR = (
    r"(?:regulators?|regulatory (?:authority|authorities|body|bodies|agency)|governments?|ministry|minister|parliament|congress|senate|white house|"
    r"treasury|commission|courts?|authorit(?:y|ies)|agency|watchdog|"
    r"(?-i:EU|FTC|FCC|SEC|DOJ|DOE|BIS|OFAC|CISA|MAS|LTA|MOH|MOM|IMDA|CSA|NHTSA|FAA|FRA|FTA|TSA|NRC|FERC|ACCC|Ofgem|ORR|Ofcom))"
)
_ACTION = (
    r"(?:imposes?|imposed|imposing|bans?|banned|banning|fines?|fined|orders?|ordered|mandates?|mandated|approves?|approved|blocks?|blocked|halts?|halted|"
    r"suspends?|suspended|revokes?|revoked|investigat(?:es|ed|ing)|probes?|probed|sanctions?|sanctioned|penali[sz](?:es|ed)|announces?|announced|enacts?|enacted|"
    r"introduces?|introduced|passes|passed|issues?|issued|rules?|ruled|raids?|raided|restricts?|restricted|tightens?|tightened|caps?|capped|unveils?|unveiled|launches?)"
)
_CUES: dict[str, tuple[re.Pattern[str], ...]] = {
    "GOV_ACTION": (
        re.compile(r"\b(?:sanctions?|sanctioned|export controls?|export bans?|export restrictions?|entity list|embargo(?:es)?|executive order|antitrust|expropriat\w+)\b", re.I),
        # A tariff is trade policy unless the word before it makes it a price plan.
        re.compile(r"(?<!energy )(?<!electricity )(?<!power )(?<!gas )(?<!water )(?<!feed-in )(?<!fixed )(?<!variable )(?<!standard )(?<!domestic )(?<!network )\btariffs?\b(?! plans?)", re.I),
        re.compile(rf"\b{_ACTOR}\b(?:\W+\w+){{0,6}}?\W+{_ACTION}\b|\b{_ACTION}\b(?:\W+\w+){{0,6}}?\W+(?:by|from|under)\W+(?:the\W+)?{_ACTOR}\b", re.I),
    ),
    "WAR": (
        re.compile(
            r"\b(?:war (?:in|on|with|between|against)|civil war|at war|invasion|invades?|invaded|air ?strikes?|missile (?:strikes?|attacks?)|military (?:strikes?|offensive|operation|action)|"
            r"armed conflict|cease-?fire|shelling|bombardment|blockade|hostilities|drone (?:strikes?|attacks?)|terror(?:ist)? attacks?|coup)\b",
            re.I,
        ),
    ),
    "PANDEMIC": (re.compile(rf"{_PAST}\b(?:pandemic|epidemic|disease outbreak|outbreak of|public health emergency|lockdowns?|quarantine|covid(?:-19)?|cholera|ebola|mpox|bird flu|H5N1)\b", re.I),),
}


@dataclass(frozen=True)
class Figure:
    text: str
    amount: float
    currency: str
    counted: bool
    reason: str | None  # why it was not counted


@dataclass(frozen=True)
class ScaleCues:
    figures: tuple[Figure, ...]
    figure_cue: bool
    action: tuple[str, ...]  # matched text per class
    action_classes: tuple[str, ...]

    @property
    def action_cue(self) -> bool:
        return bool(self.action_classes)

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["figures"] = [dict(item) for item in record["figures"]]
        record["action"], record["action_classes"] = list(record["action"]), list(record["action_classes"])
        return record


def parse_figures(text: str) -> list[Figure]:
    found: list[Figure] = []
    for match in _FIGURE.finditer(text):
        if match.group("cur"):
            currency = _PREFIX[match.group("cur").upper() if match.group("cur").upper() in _PREFIX else match.group("cur")]
            number, upper, unit = match.group("num"), match.group("num2"), (match.group("unit") or "").lower()
        else:
            currency = _SUFFIX[match.group("scur").lower()]
            number, upper, unit = match.group("snum"), None, match.group("sunit").lower()
        multiplier = _UNITS.get(unit, 1.0)
        amount = max(float(number.replace(",", "")), float((upper or number).replace(",", ""))) * multiplier
        window = text[max(0, match.start() - 80) : match.end() + 80]
        reason = None
        if amount < FIGURE_MIN:
            reason = "BELOW_MINIMUM"
        elif currency not in COUNTED_CURRENCIES:
            reason = "CURRENCY_NOT_COMPARABLE"
        elif _PROJECTION.search(window):
            reason = "MARKET_PROJECTION"
        found.append(Figure(match.group(0).strip(), amount, currency, reason is None, reason))
    return found


def scale_cues(candidate: Mapping[str, Any]) -> ScaleCues:
    title = str(candidate.get("title") or "")
    text = str(candidate.get("text") or "")
    figures = parse_figures(f"{title}\n{text}")
    lead = f"{title}\n{text[:LEAD_CHARS]}"
    classes: list[str] = []
    matched: list[str] = []
    for name, patterns in _CUES.items():
        for pattern in patterns:
            hit = pattern.search(lead)
            if hit:
                classes.append(name)
                matched.append(re.sub(r"\s+", " ", hit.group(0)).strip()[:80])
                break
    return ScaleCues(tuple(figures), any(item.counted for item in figures), tuple(matched), tuple(classes))
