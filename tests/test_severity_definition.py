import json
import tempfile
from pathlib import Path

from grit.core.impact_scoring import SeverityBand
from grit.core.severity_definition import parse_severity_definition, severity_definition_to_json


ROOT = Path(__file__).parents[1]
EBIT_SGD_MILLIONS = 136.2

# 15_Severity_Definition.md is a live, human-editable governed document. Its
# Minor row previously read S$6.1M and was corrected to S$6.8M, the confirmed
# correct figure. Boundary-conflict *detection logic* must not be tied to
# whatever that document currently says; it is proven here against a fixed,
# self-contained fixture that deliberately reproduces the original (now-fixed)
# discrepancy.
_FIXTURE_WITH_ONE_CONFLICT = """
| Column 1 | Column 2 |
| --- | --- |
| Severe | > 50% [S$68.1M] of the preceding year's EBIT |
| Major | > 20% [S$27.2M] < 50% [S$68.1M] of the preceding year's EBIT |
| Moderate | > 5% [S$6.8M] < 20% [S$27.2M] of the preceding year's EBIT |
| Minor | > 1% [S$1.4M] < 5% [S$6.1M] of the preceding year's EBIT |
| Insignificant | <1% [S$1.4M] of the preceding year's EBIT |
"""


def test_parser_treats_percentages_as_authoritative_lower_bounds() -> None:
    definition = parse_severity_definition(ROOT / "specs" / "15_Severity_Definition.md", EBIT_SGD_MILLIONS)
    assert definition.financial_lower_bounds == {
        SeverityBand.INSIGNIFICANT: 0.0,
        SeverityBand.MINOR: 0.01,
        SeverityBand.MODERATE: 0.05,
        SeverityBand.MAJOR: 0.20,
        SeverityBand.SEVERE: 0.50,
    }
    assert definition.preceding_year_ebit == EBIT_SGD_MILLIONS


def test_version_is_a_reproducible_sha256_of_file_content() -> None:
    first = parse_severity_definition(ROOT / "specs" / "15_Severity_Definition.md", EBIT_SGD_MILLIONS)
    second = parse_severity_definition(ROOT / "specs" / "15_Severity_Definition.md", EBIT_SGD_MILLIONS)
    assert first.version == second.version
    assert len(first.version) == 64
    int(first.version, 16)  # raises ValueError if not valid hex


def test_severe_band_has_no_upper_bracket_and_insignificant_has_no_lower_bracket() -> None:
    definition = parse_severity_definition(ROOT / "specs" / "15_Severity_Definition.md", EBIT_SGD_MILLIONS)
    assert definition.brackets[SeverityBand.SEVERE].upper_percent is None
    assert definition.brackets[SeverityBand.SEVERE].upper_amount is None
    assert definition.brackets[SeverityBand.INSIGNIFICANT].lower_amount is None


def test_contiguity_between_adjacent_bands_is_enforced_at_parse_time() -> None:
    definition = parse_severity_definition(ROOT / "specs" / "15_Severity_Definition.md", EBIT_SGD_MILLIONS)
    order = [SeverityBand.INSIGNIFICANT, SeverityBand.MINOR, SeverityBand.MODERATE, SeverityBand.MAJOR, SeverityBand.SEVERE]
    for current, following in zip(order, order[1:]):
        assert definition.brackets[current].upper_percent == definition.brackets[following].lower_percent


def test_boundary_conflict_detection_flags_a_genuine_same_percentage_mismatch() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(_FIXTURE_WITH_ONE_CONFLICT)
        fixture_path = Path(handle.name)
    try:
        definition = parse_severity_definition(fixture_path, EBIT_SGD_MILLIONS)
        conflicts = json.loads(severity_definition_to_json(definition))["boundary_conflicts"]
    finally:
        fixture_path.unlink()

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["boundary_percent"] == 0.05
    assert conflict["band_below"] == "MINOR"
    assert conflict["band_below_upper_amount_sgd_millions"] == 6.1
    assert conflict["band_above"] == "MODERATE"
    assert conflict["band_above_lower_amount_sgd_millions"] == 6.8


def test_real_governed_document_parses_and_reports_a_conflicts_list() -> None:
    """Smoke test only: the live document's exact figures are not hardcoded
    here because the document is human-editable and has already changed once
    during this module's review."""
    definition = parse_severity_definition(ROOT / "specs" / "15_Severity_Definition.md", EBIT_SGD_MILLIONS)
    review_json = json.loads(severity_definition_to_json(definition))
    assert isinstance(review_json["boundary_conflicts"], list)


def test_ebit_consistency_is_reported_per_bracket_not_as_a_hard_failure() -> None:
    definition = parse_severity_definition(ROOT / "specs" / "15_Severity_Definition.md", EBIT_SGD_MILLIONS)
    review_json = json.loads(severity_definition_to_json(definition))
    severe_band = next(band for band in review_json["bands"] if band["band"] == "SEVERE")
    assert severe_band["ebit_consistency"][0]["percent_of_ebit"] == 0.5
    assert severe_band["ebit_consistency"][0]["exceeds_2_percent_threshold"] is False
