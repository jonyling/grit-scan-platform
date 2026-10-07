from pathlib import Path

import re

from grit.core.impact_scoring import KeywordTier
from grit.ontology.lob_keyword_loader import load_lob_keywords, parse_lob_keyword_rows
from grit.ontology.workbook import workbook_path

ROOT = Path(__file__).parents[1]
WORKBOOK = workbook_path()
_LEFTOVER_NUMBERING = re.compile(r"^(\d+\.\s|\(\d+\)\s)")


def test_keywords_are_attributed_to_the_correct_lob_via_forward_fill() -> None:
    keywords = load_lob_keywords(WORKBOOK)
    by_keyword = {(keyword.keyword, keyword.tier): keyword.lob for keyword in keywords}
    assert by_keyword[("Smart Metro Systems", KeywordTier.KEY_TIER_2)] == "Mobility Rail"
    assert by_keyword[("Electronic Tolling", KeywordTier.KEY_TIER_2)] == "TransCore"


_ROWS_WITH_EMERGING = [
    ["LOB", "Keywords (Key/Tier 2)", "Keywords (Emerging)"],
    ["Smart Sustainable Utilities", "Smart Utilities", "1. Building Decarbonisation & Net-Zero Regulations "],
    [None, "Smart Water & Water Security", "(2) Critical Infrastructure Protection"],
    ["Mobility Road", "Electronic Tolling", "Open Road Tolling"],
]


def test_leading_numbering_is_stripped_from_emerging_keywords() -> None:
    keywords = parse_lob_keyword_rows(_ROWS_WITH_EMERGING)
    emerging = {keyword.keyword for keyword in keywords if keyword.tier is KeywordTier.EMERGING}
    assert "Building Decarbonisation & Net-Zero Regulations" in emerging
    assert "Critical Infrastructure Protection" in emerging
    assert not any(_LEFTOVER_NUMBERING.match(keyword) for keyword in emerging)


def test_unnumbered_emerging_keywords_are_kept_as_is() -> None:
    keywords = parse_lob_keyword_rows(_ROWS_WITH_EMERGING)
    emerging = {keyword.keyword for keyword in keywords if keyword.tier is KeywordTier.EMERGING}
    assert "Open Road Tolling" in emerging


def test_a_sheet_without_an_emerging_column_yields_no_emerging_keywords() -> None:
    rows = [["LOB", "Keywords (Key/Tier 2)"], ["Mobility Rail", "Smart Metro Systems"], [None, "Rail Signalling"]]
    keywords = parse_lob_keyword_rows(rows)
    assert {keyword.tier for keyword in keywords} == {KeywordTier.KEY_TIER_2}
    assert {keyword.lob for keyword in keywords} == {"Mobility Rail"}


def test_the_workbook_has_no_emerging_column_and_none_is_invented() -> None:
    keywords = load_lob_keywords(WORKBOOK)
    assert keywords and not any(keyword.tier is KeywordTier.EMERGING for keyword in keywords)


def test_loader_is_deterministic_across_repeated_calls() -> None:
    results = {load_lob_keywords(WORKBOOK) for _ in range(5)}
    assert len(results) == 1
