"""Check that a model's number is really in a text. Pure.

A number is found as a whole number: 5 is not in 50, 20 is not in 2026, and 1,200 is not in 1,200,000.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"\d[\d,.]*\d|\d")


def numbers_in(text: str) -> list[str]:
    return [token.rstrip(".,") for token in _NUMBER.findall(text)]


def has_number(token: str, text: str) -> bool:
    token = token.rstrip(".,")
    return bool(re.search(rf"(?<![\d.,]){re.escape(token)}(?!\d|[.,]\d)", text))
