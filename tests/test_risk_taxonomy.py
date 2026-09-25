from pathlib import Path

from grit_scan.risk_taxonomy import RiskTaxonomyTier, parse_risk_taxonomy, resolve_risk_taxonomy_ref

ROOT = Path(__file__).parents[1]


def test_all_fourteen_entries_are_parsed_with_tier_prefixed_refs() -> None:
    taxonomy = parse_risk_taxonomy(ROOT / "specs" / "13_Annual_Risks.md")
    assert len(taxonomy) == 14
    by_tier = {tier: [entry for entry in taxonomy if entry.tier is tier] for tier in RiskTaxonomyTier}
    assert len(by_tier[RiskTaxonomyTier.KEY]) == 6
    assert len(by_tier[RiskTaxonomyTier.TIER_2]) == 5
    assert len(by_tier[RiskTaxonomyTier.EMERGING]) == 3


def test_refs_are_unique_across_all_three_tiers() -> None:
    taxonomy = parse_risk_taxonomy(ROOT / "specs" / "13_Annual_Risks.md")
    refs = [entry.ref for entry in taxonomy]
    assert len(refs) == len(set(refs))


def test_known_entries_have_the_expected_ref_tier_and_name() -> None:
    taxonomy = parse_risk_taxonomy(ROOT / "specs" / "13_Annual_Risks.md")
    by_ref = {entry.ref: entry for entry in taxonomy}
    assert by_ref["KEY-1"].name == "Competition"
    assert by_ref["KEY-1"].tier is RiskTaxonomyTier.KEY
    assert by_ref["TIER2-1"].name == "Business concentration risk for Taiwan"
    assert by_ref["TIER2-1"].tier is RiskTaxonomyTier.TIER_2
    assert by_ref["EMERGING-1"].name == "Geopolitical volatility & conflicts impacting staff safety & supply chain"
    assert by_ref["EMERGING-1"].tier is RiskTaxonomyTier.EMERGING


def test_parser_stops_at_the_blank_row_and_does_not_swallow_section_5() -> None:
    taxonomy = parse_risk_taxonomy(ROOT / "specs" / "13_Annual_Risks.md")
    names = {entry.name for entry in taxonomy}
    assert "Risk Owner / Context" not in names
    assert not any("5." in entry.name for entry in taxonomy)


def test_resolve_risk_taxonomy_ref_never_invents_a_match() -> None:
    taxonomy = parse_risk_taxonomy(ROOT / "specs" / "13_Annual_Risks.md")
    assert resolve_risk_taxonomy_ref("KEY-1", taxonomy) is not None
    assert resolve_risk_taxonomy_ref("KEY-999", taxonomy) is None
    assert resolve_risk_taxonomy_ref(None, taxonomy) is None
