"""Load the governed LOB/sector keyword taxonomy from URS Entity Info.xlsx.

Sheet '6. LOB_Keywords' is a merged-cell layout: the LOB name appears once
per block (blank cells below it belong to the same LOB), and each row after
that carries at most one Key/Tier-2 keyword and/or one Emerging keyword,
some prefixed with a "1." or "(1)" style number. This module unpacks that
block layout into flat, typed records; it invents no keyword and no LOB
name of its own -- anything that cannot be attributed to a LOB block is
dropped, not guessed.

The DS&C workbook uses a simpler layout: one row per LOB, with its keywords
in one comma-separated cell. A cell with two or more commas is split.

The Marine workbook puts the keyword first: a 'Keyword | LOB' header, one
keyword per row, and the LOB beside it. A keyword with a blank LOB cell
covers the whole business area and is filed under ALL_LOBS.
"""

from __future__ import annotations

import re
from pathlib import Path

from grit.core.impact_scoring import KeywordTier, LobKeyword
from grit.ontology.xlsx_reader import read_worksheet

_SHEET = "6. LOB_Keywords"
_LEADING_NUMBERING = re.compile(r"^\(?\d+\)?\.?\s*")
# Inside a comma-separated cell, a number is a list marker only with its punctuation and a space: "1. Cloud", "(2) AI".
# "5G" and "3D mapping" keep their digits.
_LIST_MARKER = re.compile(r"^(?:\(\d+\)|\d+[.)])\s+")
ALL_LOBS = "All LOBs"


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _clean_keyword(value: object) -> str | None:
    text = str(value or "").replace("\xa0", " ").strip()
    if not text:
        return None
    text = _LEADING_NUMBERING.sub("", text).strip().strip(",").strip()
    return text or None


def _keywords(value: object) -> list[str]:
    """One keyword per cell, or, where a workbook lists a LOB's keywords in one cell (DS&C), a comma-separated list."""
    text = str(value or "")
    if text.count(",") < 2:
        keyword = _clean_keyword(text)
        return [keyword] if keyword else []
    parts = (_LIST_MARKER.sub("", part.replace("\xa0", " ").strip()).strip() for part in text.split(","))
    return [part for part in parts if part]


def load_lob_keywords(workbook_path: str | Path) -> tuple[LobKeyword, ...]:
    return parse_lob_keyword_rows(read_worksheet(workbook_path, _SHEET))


def parse_lob_keyword_rows(rows: list[list[object | None]]) -> tuple[LobKeyword, ...]:
    """Parse the sheet's LOB and Key-Tier-2 columns, and the Emerging column when present."""
    keyword_first = next(
        (index for index, row in enumerate(rows[:3]) if len(row) > 1 and _key(row[0]).startswith("keyword") and _key(row[1]).startswith("lob")),
        None,
    )
    if keyword_first is not None:
        return _keyword_first_rows(rows[keyword_first + 1:])
    if not rows or len(rows[0]) < 2:
        raise ValueError(f"'{_SHEET}' sheet does not have the expected LOB and Key-Tier-2 columns.")

    keywords: list[LobKeyword] = []
    current_lob: str | None = None
    for row in rows[1:]:
        lob_cell = str(row[0] or "").replace("\xa0", " ").strip() if len(row) > 0 else ""
        if lob_cell:
            current_lob = lob_cell
        if current_lob is None:
            continue  # stray row before the first LOB block; nothing to attribute it to

        for keyword in _keywords(row[1]) if len(row) > 1 else []:
            keywords.append(LobKeyword(lob=current_lob, keyword=keyword, tier=KeywordTier.KEY_TIER_2))
        for keyword in _keywords(row[2]) if len(row) > 2 else []:
            keywords.append(LobKeyword(lob=current_lob, keyword=keyword, tier=KeywordTier.EMERGING))

    if not keywords:
        raise ValueError(f"No keywords could be parsed from '{_SHEET}'.")
    return tuple(keywords)


def _keyword_first_rows(rows: list[list[object | None]]) -> tuple[LobKeyword, ...]:
    keywords: list[LobKeyword] = []
    for row in rows:
        lob = str(row[1] or "").replace("\xa0", " ").strip() if len(row) > 1 else ""
        for keyword in _keywords(row[0]) if row else []:
            keywords.append(LobKeyword(lob=lob or ALL_LOBS, keyword=keyword, tier=KeywordTier.KEY_TIER_2))
    if not keywords:
        raise ValueError(f"No keywords could be parsed from '{_SHEET}'.")
    return tuple(keywords)
