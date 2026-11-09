"""Report text stays in Latin script. Letters from any other writing system are omitted."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_LETTER = re.compile(r"[A-Za-z]")
_SPACE = re.compile(r"\s+")
_DANGLING = re.compile(r"(?:\s*[;|,/]\s*)+(?=[;|,/]|$)")


def latin_script(text: str | None) -> str:
    """Keep Latin letters, ASCII, and punctuation. Drop Thai, Tamil, Hangul, CJK, Arabic, Cyrillic and the rest."""
    kept: list[str] = []
    for ch in str(text or ""):
        if ord(ch) < 128:
            kept.append(ch)
            continue
        name = unicodedata.name(ch, "")
        category = unicodedata.category(ch)
        if category[0] in "LM" and "LATIN" not in name:
            continue
        if category[0] in "LM":
            kept.append(ch)
            continue
        # Non-letter marks from another script (Thai vowels, CJK punctuation) stay out.
        if name.startswith((
            "THAI", "LAO", "KHMER", "MYANMAR", "TIBETAN", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM",
            "SINHALA", "DEVANAGARI", "BENGALI", "GURMUKHI", "GUJARATI", "ORIYA", "ARABIC", "HEBREW",
            "SYRIAC", "THAANA", "CYRILLIC", "ARMENIAN", "GEORGIAN", "ETHIOPIC", "CHEROKEE",
            "HANGUL", "HIRAGANA", "KATAKANA", "CJK", "IDEOGRAPHIC", "BOPOMOFO",
        )):
            continue
        kept.append(ch)
    cleaned = _DANGLING.sub("", _SPACE.sub(" ", "".join(kept))).strip(" \t;|,/")
    return cleaned


def has_latin_letter(text: str | None) -> bool:
    return bool(_LETTER.search(latin_script(text)))


def latin_copy(value: Any) -> Any:
    """A copy of a brief (or any JSON-like value) with non-Latin letters removed from every string."""
    if isinstance(value, str):
        return latin_script(value)
    if isinstance(value, dict):
        return {key: latin_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [latin_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(latin_copy(item) for item in value)
    return value
