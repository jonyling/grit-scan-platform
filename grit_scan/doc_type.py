"""Deterministic document type for a candidate. Pure: phrase rules and a publisher list, no model, no score.

MARKET_RESEARCH is a paid market-report advertisement ("Smart Water Meters Market worth $10 billion by 2031"). LISTICLE is a ranked
list of vendors ("Top 10: Smart Grid Providers"). EXPLAINER is a how-to, a guide, a sponsored post, or an opinion piece. FINANCE_COMMENTARY is an investment note about a listed company: a price target, a
rating, a technical signal ("Amtech Gains 79% in the Past Year: Should It Be in Your Portfolio?"). None of these reports an event. PRESS_RELEASE is text the issuer wrote and a wire carried, so a second wire copy is not independent evidence. NEWS is the rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

MARKET_RESEARCH = "MARKET_RESEARCH"
LISTICLE = "LISTICLE"
FINANCE_COMMENTARY = "FINANCE_COMMENTARY"
EXPLAINER = "EXPLAINER"
NON_EVENT_TYPES = frozenset({MARKET_RESEARCH, LISTICLE, FINANCE_COMMENTARY, EXPLAINER})
PRESS_RELEASE = "PRESS_RELEASE"
NEWS = "NEWS"

WIRE_DOMAINS = frozenset(
    {"globenewswire.com", "prnewswire.com", "businesswire.com", "openpr.com", "einpresswire.com", "accessnewswire.com", "accesswire.com",
     "prweb.com", "newswire.com", "issuewire.com", "send2press.com", "24-7pressrelease.com"}
)
_TITLE = re.compile(
    r"\bmarket\b.*\b(report|size|share|forecasts?|growth|trends|analysis|outlook|insights|to reach|worth|CAGR|expands?|booming|booms|set to|poised|20\d\d\s*[-–]\s*20\d\d)\b"
    r"|\b(report|forecast)\b.*\bmarket\b",
    re.I,
)
_LIST_TITLE = re.compile(r"^\s*(top|best)\s+\d+\b|\b\d+\s+(best|top|biggest|largest|leading)\b", re.I)
_COMMENTARY = re.compile(
    r"\b(?:price targets?|consensus (?:targets?|ratings?|price)|analyst (?:ratings?|upgrades?|downgrades?)|(?:buy|sell|hold) ratings?|gets? an? (?:buy|sell|upgrade|downgrade)|"
    r"moving averages?|should (?:it|you) (?:be in|buy)|your portfolio|growth stocks?|insider (?:confidence|buying|selling)|(?:over|under)valued|GF value|stock news|"
    r"(?:cuts?|raises?|lowers?|boosts?) (?:its )?(?:price )?targets?|stock trades?|investor (?:conference|day|presentation)|recommends? [\w\s]{0,30}\bstock|stock recommendations?|"
    r"(?:NASDAQ|NYSE|NYSEARCA|AMEX):\s?[A-Z]{1,5})\b",
    re.I,
)
_EXPLAINER = re.compile(
    r"^\s*(?:how to|how do|how does|what is|what are|why you|a guide|guide to|understanding|introduction to|a beginner)\b|\bhow to\b|\bexplained\b|\bin \d+ (?:easy )?steps\b|"
    r"\b\d+ (?:ways|tips|steps|things|reasons|questions)\b|\b\d+-(?:step|way|tip)s?\b|\bguide\s*$|\b(?:beginner'?s|complete|ultimate|definitive) guide\b|^\s*(?:council post|sponsored|op-ed|opinion|commentary)\b\s*[:|-]|\bFAQs?\b",
    re.I,
)
_TEXT_CUES = {
    "MARKET_SIZE_PHRASE": re.compile(r"\bmarket\s+(size|share|report|analysis|research|outlook|forecast|trends?|growth|study|insights?)\b", re.I),
    "CAGR": re.compile(r"\bCAGR\b|compound annual growth", re.I),
    "REACH_USD": re.compile(r"\b(to reach|expected to reach|projected to reach|anticipated to reach|valued at)\s+(USD|US\$|\$)\s?[\d.,]+\s?(million|billion|bn|mn|m|b)\b", re.I),
    "KEY_PLAYERS": re.compile(r"\bkey (market )?players\b|\btop players\b|\bleading (market )?players\b|\bcompetitive landscape\b", re.I),
    "FORECAST_PERIOD": re.compile(r"\bforecast period\b|\bforecast(ed)? to 20\d\d\b|\bhistorical data\b|\bsegmented by\b|\bmarket segmentation\b", re.I),
    "SAMPLE_OFFER": re.compile(r"\b(request|download) (a )?(free )?sample\b|\bfree sample\b|\bmarket\.us\b", re.I),
}


@dataclass(frozen=True)
class DocType:
    doc_type: str
    cues: tuple[str, ...]


def is_wire(publisher: str | None) -> bool:
    host = (publisher or "").casefold().removeprefix("www.")
    return any(host == domain or host.endswith("." + domain) for domain in WIRE_DOMAINS)


def classify(candidate: Mapping[str, Any]) -> DocType:
    """A title that names a market report, or two kinds of market-report phrase in the text, makes a market research listing."""
    title = str(candidate.get("title") or "")
    text = f"{title}\n{candidate.get('text') or ''}"
    cues = [name for name, pattern in _TEXT_CUES.items() if pattern.search(text)]
    if _TITLE.search(title):
        return DocType(MARKET_RESEARCH, tuple(["TITLE_NAMES_A_MARKET_REPORT", *cues]))
    if len(cues) >= 2:
        return DocType(MARKET_RESEARCH, tuple(cues))
    if _LIST_TITLE.search(title):
        return DocType(LISTICLE, ("TITLE_IS_A_RANKED_LIST",))
    if _EXPLAINER.search(title):
        return DocType(EXPLAINER, ("TITLE_IS_A_GUIDE_OR_OPINION",))
    if _COMMENTARY.search(title) or len({match.group(0).casefold() for match in _COMMENTARY.finditer(text[:600])}) >= 2:
        return DocType(FINANCE_COMMENTARY, ("INVESTMENT_COMMENTARY",))
    return DocType(PRESS_RELEASE if is_wire(candidate.get("publisher")) else NEWS, tuple(cues))
