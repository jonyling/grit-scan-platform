"""Shared normalisation for deterministic matchers."""

from __future__ import annotations

import re

_POSSESSIVE = re.compile(r"['’]s\b")
_PUNCT = re.compile(r"['’,.]")
_SPACE = re.compile(r"\s+")
_DASH = re.compile(r"[\u2013\u2014]")
_SHORT_KEYWORD = 6


def normalise(value: str | None) -> str:
    if value is None or not str(value).strip():
        return ""
    return _SPACE.sub(" ", str(value).lower()).strip()


def normalise_theme(value: str | None) -> str:
    text = normalise(value)
    text = _POSSESSIVE.sub("", text)
    text = _PUNCT.sub("", text)
    text = text.replace("-", " ")
    return _SPACE.sub(" ", text).strip()


def normalise_exposure(value: str | None) -> str:
    return _DASH.sub("-", normalise(value))


def standalone_pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def keyword_in_text(text: str, keyword: str) -> bool:
    """Match a governed keyword without the short-token false positive.

    Tokens shorter than 6 characters match only as a standalone word.
    ``ai`` must not match inside ``Taiwan``, and ``war`` must not match
    inside ``warehouses``.
    """
    if not keyword:
        return False
    if len(keyword) < _SHORT_KEYWORD:
        return standalone_pattern(keyword).search(text) is not None
    return keyword in text or text in keyword
