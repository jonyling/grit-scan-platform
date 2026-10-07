"""Load the governed LOB/sector keyword taxonomy from URS Entity Info.xlsx.

Sheet '6. LOB_Keywords' is a merged-cell layout: the LOB name appears once
per block (blank cells below it belong to the same LOB), and each row after
that carries at most one Key/Tier-2 keyword and/or one Emerging keyword,
some prefixed with a "1." or "(1)" style number. This module unpacks that
block layout into flat, typed records; it invents no keyword and no LOB
name of its own -- anything that cannot be attributed to a LOB block is
dropped, not guessed.
"""

from __future__ import annotations

import re
from pathlib import Path

from .impact_scoring import KeywordTier, LobKeyword
from .xlsx_reader import read_worksheet

_SHEET = "6. LOB_Keywords"
_LEADING_NUMBERING = re.compile(r"^\(?\d+\)?\.?\s*")


def _clean_keyword(value: object) -> str | None:
    text = str(value or "").replace("\xa0", " ").strip()
    if not text:
        return None
    text = _LEADING_NUMBERING.sub("", text).strip().strip(",").strip()
    return text or None


def load_lob_keywords(workbook_path: str | Path) -> tuple[LobKeyword, ...]:
    return parse_lob_keyword_rows(read_worksheet(workbook_path, _SHEET))


def parse_lob_keyword_rows(rows: list[list[object | None]]) -> tuple[LobKeyword, ...]:
    """Parse the sheet's LOB and Key-Tier-2 columns, and the Emerging column when present."""
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

        key_tier2 = _clean_keyword(row[1]) if len(row) > 1 else None
        if key_tier2:
            keywords.append(LobKeyword(lob=current_lob, keyword=key_tier2, tier=KeywordTier.KEY_TIER_2))

        emerging = _clean_keyword(row[2]) if len(row) > 2 else None
        if emerging:
            keywords.append(LobKeyword(lob=current_lob, keyword=emerging, tier=KeywordTier.EMERGING))

    if not keywords:
        raise ValueError(f"No keywords could be parsed from '{_SHEET}'.")
    return tuple(keywords)
