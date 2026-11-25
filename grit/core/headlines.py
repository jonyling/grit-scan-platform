"""Distinctive headline words, so two reports of one story can be recognized. Pure.

A word counts when it has five or more letters and is not common in headlines. Plurals fold to the singular. A
multi-word place counts once, so "Saudi Arabia" does not make two unrelated Saudi stories look alike.

Matching folds accents (NFKD) so "Líneas" and "Lineas" are the same token. R&A entity aliases in
config/event_aliases.json name one story when any listed form appears; they do not change report text.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

GENERIC = frozenset("""about after again against alert alerts amid among announces annual another around before between
billion breaking could during first following highlights latest major million month months monthly other report reports
since their there these third today under update updates weekly where which while world would years quarter group
critical security vulnerability vulnerabilities
january february march april june july august september october november december""".split())
PLACES = (("saudi arabian", "saudi"), ("saudi arabia", "saudi"), ("united arab emirates", "uae"), ("united states", " "),
          ("hong kong", "hongkong"), ("south korea", "korea"), ("north korea", "northkorea"), ("new zealand", "newzealand"))

# Soft near-miss band for instrumentation only: cosine in [NEAR_MISS_LOW, SAME_STORY) with ≥1 shared term or identifier.
# cluster_events does not join these; P1 may offer them to the Event Relation Resolver.
NEAR_MISS_LOW = 0.82


def fold_accents(text: str) -> str:
    """NFKD then drop combining marks, for matching only."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(ch))


def _fold(text: str) -> str:
    return fold_accents(text).casefold()


@lru_cache(maxsize=1)
def _alias_rows(path: str) -> tuple[tuple[str, str], ...]:
    """(folded phrase, canonical id) pairs, longest phrase first."""
    file = Path(path)
    if not file.is_file():
        return ()
    document = json.loads(file.read_text(encoding="utf-8-sig"))
    pairs: list[tuple[str, str]] = []
    for entry in document.get("aliases") or []:
        if not isinstance(entry, Mapping):
            continue
        canonical = _fold(str(entry.get("canonical") or "")).replace(" ", "")
        if len(canonical) < 5:
            continue
        for name in entry.get("names") or []:
            phrase = _fold(str(name))
            if phrase:
                pairs.append((phrase, canonical))
    pairs.sort(key=lambda item: (-len(item[0]), item[0], item[1]))
    return tuple(pairs)


def event_aliases(path: Path | str | None = None) -> tuple[tuple[str, str], ...]:
    """R&A entity alias phrases → canonical id. path None reads config/event_aliases.json."""
    if path is None:
        from grit.settings import CONFIG_DIR

        path = CONFIG_DIR / "event_aliases.json"
    return _alias_rows(str(path))


def alias_ids(text: str, aliases: Sequence[tuple[str, str]] | None = None) -> frozenset[str]:
    """Canonical ids for entity aliases named in text. Longer phrases win; matches do not overlap."""
    folded = _fold(text)
    if not folded:
        return frozenset()
    found: list[str] = []
    used: list[tuple[int, int]] = []
    for phrase, canonical in (aliases if aliases is not None else event_aliases()):
        start = 0
        while True:
            index = folded.find(phrase, start)
            if index < 0:
                break
            end = index + len(phrase)
            if not any(index < stop and end > begin for begin, stop in used):
                used.append((index, end))
                found.append(canonical)
            start = end
    return frozenset(found)


def distinctive_terms(text: str) -> frozenset[str]:
    folded = _fold(text)
    for phrase, token in PLACES:
        folded = folded.replace(phrase, token)
    words = (word for word in re.findall(r"[^\W\d_]{5,}", folded, flags=re.UNICODE) if word not in GENERIC)
    return frozenset(word[:-1] if len(word) > 5 and word.endswith("s") and not word.endswith("ss") else word for word in words)


COMMON_IDENTIFIERS = frozenset({"covid-19", "covid19", "g20", "g7", "q1", "q2", "q3", "q4", "h1", "h2", "5g", "mp4"})
_IDENTIFIER = re.compile(r"\b(?=[a-z0-9-]*[a-z])(?=[a-z0-9-]*\d)[a-z0-9][a-z0-9-]{3,}\b")


def identifiers(text: str) -> frozenset[str]:
    """Names that mix letters and digits, such as "PHP2Shell", "CVE-2026-1096" or "A320neo". One shared identifier names
    the same story. A plain number, such as a year, is not an identifier."""
    return frozenset(_IDENTIFIER.findall(_fold(text))) - COMMON_IDENTIFIERS


def is_near_miss(cosine: float, *, shared_terms: int = 0, shared_identifiers: int = 0, high: float = 0.90) -> bool:
    """True for a soft near-miss: cosine in [NEAR_MISS_LOW, high) with at least one shared term or identifier.

    Instrumentation only — does not join events. high defaults to the SAME_STORY cosine threshold (0.90)."""
    try:
        score = float(cosine)
    except (TypeError, ValueError):
        return False
    return NEAR_MISS_LOW <= score < float(high) and (shared_terms >= 1 or shared_identifiers >= 1)
