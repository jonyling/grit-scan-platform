"""Drop a later copy of a sentence, and a later sentence that restates an earlier point with the same figures.

A figure is a percent, an amount of money, or a count with a unit. A later sentence is a restatement when every figure in it
was used before, in one earlier sentence that shares at least half of its content words (OVERLAP). A sentence that only
shares a figure with an unrelated sentence is a different fact and stays: "revenue fell 12%" does not remove "a 12%
discount on the bid". A short label, a heading, and the standing disclaimer can repeat. The first use is the one that stays.
"""

from __future__ import annotations

import re

from grit.report.notices import DISCLAIMER, SUGGESTION_LABEL

_FIGURE = re.compile(
    r"(?:(?:USD|SGD|US\$|S\$|\$)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:billion|million|bn|m))?)"
    r"|(?:\d[\d,]*(?:\.\d+)?\s?%)"
    r"|(?:\b\d[\d,]*(?:\.\d+)?\s(?:staff|employees|workers|people|persons|ships|vessels|flights|aircraft|contracts|sites|plants|fabs)\b)",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z][a-z'-]{3,}")
_STOP = frozenset({
    "this", "that", "these", "those", "with", "from", "into", "onto", "over", "under", "their", "there", "they", "them", "have",
    "has", "had", "were", "was", "been", "being", "will", "would", "could", "should", "also", "than", "then", "when", "where",
    "which", "while", "week", "same", "about", "after", "before", "across", "said", "says", "more", "most", "some", "such",
})
OVERLAP = 0.5  # shared content words, as a share of the shorter sentence's


class FirstUse:
    """Reading order. Call filter on prose. Call observe on a block that must stay whole, such as a table cell."""

    def __init__(self) -> None:
        self.claims: set[str] = set()
        self.points: list[tuple[frozenset[str], frozenset[str]]] = []  # (figures, content words) of each tracked sentence

    def observe(self, text: str) -> None:
        for chunk in _chunks(text):
            self._remember(chunk)

    def filter(self, text: str) -> str:
        kept = []
        for chunk in _chunks(text):
            if not self._drop(chunk):
                self._remember(chunk)
                kept.append(chunk)
        return " ".join(kept)

    def _remember(self, chunk: str) -> None:
        if _exempt(chunk):
            return
        if _tracked(chunk):
            self.claims.add(_key(chunk))
        figures = _figures(chunk)
        if figures:
            self.points.append((figures, _words(chunk)))

    def _drop(self, chunk: str) -> bool:
        if _exempt(chunk) or not _tracked(chunk):
            return False
        if chunk.startswith(SUGGESTION_LABEL):
            return _key(chunk) in self.claims
        if _key(chunk) in self.claims:
            return True
        figures, words = _figures(chunk), _words(chunk)
        if not figures or not words:
            return False
        return any(figures <= seen and len(words & said) >= OVERLAP * min(len(words), len(said)) for seen, said in self.points if said)


def dedupe_markdown(text: str) -> str:
    """Apply first-use to prose lines. A table row, a heading, and a horizontal rule stay as written."""
    state = FirstUse()
    kept = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("|", "#", "---", "*", ">")):
            state.observe(line)
            kept.append(line)
            continue
        kept.append(state.filter(line))
    return "\n".join(kept)


def _chunks(text: str) -> list[str]:
    flat = " ".join(str(text or "").split())
    if not flat:
        return []
    return [chunk for chunk in _SENTENCE.split(flat) if chunk]


def _figures(chunk: str) -> frozenset[str]:
    return frozenset(" ".join(figure.split()).casefold() for figure in _FIGURE.findall(chunk))


def _words(chunk: str) -> frozenset[str]:
    """Content words, without figures: what the sentence is about."""
    return frozenset(word for word in _WORD.findall(_FIGURE.sub(" ", chunk.casefold())) if word not in _STOP)


def _key(chunk: str) -> str:
    return " ".join(chunk.split()).casefold()


def _exempt(chunk: str) -> bool:
    """The disclaimer is on the cover and again in every footer. Its sentences can repeat."""
    text = " ".join(chunk.split())
    return text == DISCLAIMER or text in _chunks(DISCLAIMER)


def _tracked(chunk: str) -> bool:
    """A claim long enough to be a repeated point. A heading or a badge is shorter and can repeat."""
    return len(chunk) >= 40 and chunk.endswith((".", "!", "?"))
