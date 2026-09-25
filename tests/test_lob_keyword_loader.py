from pathlib import Path

import re

from grit_scan.impact_scoring import KeywordTier
from grit_scan.lob_keyword_loader import load_lob_keywords

ROOT = Path(__file__).parents[1]
_LEFTOVER_NUMBERING = re.compile(r"^(\d+\.\s|\(\d+\)\s)")


def test_keywords_are_attributed_to_the_correct_lob_via_forward_fill() -> None:
    keywords = load_lob_keywords(ROOT / "URS Entity Info.xlsx")
    by_keyword = {(keyword.keyword, keyword.tier): keyword.lob for keyword in keywords}
    assert by_keyword[("Smart Metro Systems", KeywordTier.KEY_TIER_2)] == "Mobility Rail"
    assert by_keyword[("Electronic Tolling", KeywordTier.KEY_TIER_2)] == "TransCore"


def test_leading_numbering_is_stripped_from_emerging_keywords() -> None:
    keywords = load_lob_keywords(ROOT / "URS Entity Info.xlsx")
    emerging = {keyword.keyword for keyword in keywords if keyword.tier is KeywordTier.EMERGING}
    assert "Building Decarbonisation & Net-Zero Regulations" in emerging
    assert "Critical Infrastructure Protection" in emerging
    assert not any(_LEFTOVER_NUMBERING.match(keyword) for keyword in emerging)


def test_unnumbered_emerging_keywords_are_kept_as_is() -> None:
    keywords = load_lob_keywords(ROOT / "URS Entity Info.xlsx")
    emerging = {keyword.keyword for keyword in keywords if keyword.tier is KeywordTier.EMERGING}
    assert "healthcare workforce shortages" in emerging
    assert "Open Road Tolling" in emerging


def test_loader_is_deterministic_across_repeated_calls() -> None:
    results = {load_lob_keywords(ROOT / "URS Entity Info.xlsx") for _ in range(5)}
    assert len(results) == 1
