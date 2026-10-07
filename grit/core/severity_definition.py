"""Parser for the governed Markdown severity definition.

Comparators (``>``/``<``) are parsed explicitly per band rather than taking the
minimum of whatever percentages appear in a cell. Adjacent bands must share the
same boundary percentage or the parse fails loudly. Preceding-year EBIT is an
explicit input, never derived from the source table, so the parser can report
how consistent the table's own bracketed dollar figures are with it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from grit.core.impact_scoring import SeverityBand, SeverityBandBracket, SeverityDefinition

_BAND_ORDER = (
    SeverityBand.INSIGNIFICANT,
    SeverityBand.MINOR,
    SeverityBand.MODERATE,
    SeverityBand.MAJOR,
    SeverityBand.SEVERE,
)
_BAND_BY_LABEL = {
    "Insignificant": SeverityBand.INSIGNIFICANT,
    "Minor": SeverityBand.MINOR,
    "Moderate": SeverityBand.MODERATE,
    "Major": SeverityBand.MAJOR,
    "Severe": SeverityBand.SEVERE,
}
_COMPARATOR = re.compile(r"([<>])\s*(\d+(?:\.\d+)?)%\s*\[S\$(\d+(?:\.\d+)?)M\]")
_EBIT_CONSISTENCY_THRESHOLD_PERCENT = 2.0


def _normalise_label(value: str) -> str:
    return value.replace("\u200b", "").replace("\ufeff", "").strip()


def parse_severity_definition(path: str | Path, preceding_year_ebit: float) -> SeverityDefinition:
    """Parse 15_Severity_Definition.md against an explicitly supplied EBIT."""
    if preceding_year_ebit <= 0:
        raise ValueError(f"preceding_year_ebit must be positive, got {preceding_year_ebit}")

    text = Path(path).read_text(encoding="utf-8")
    brackets: dict[SeverityBand, SeverityBandBracket] = {}

    for row in text.splitlines():
        if not row.startswith("|"):
            continue
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if not cells or len(cells) < 2:
            continue
        label = _normalise_label(cells[0])
        if label not in _BAND_BY_LABEL:
            continue
        band = _BAND_BY_LABEL[label]
        lower_percent: float | None = None
        lower_amount: float | None = None
        upper_percent: float | None = None
        upper_amount: float | None = None
        for sign, percent_text, amount_text in _COMPARATOR.findall(cells[1]):
            percent = float(percent_text) / 100
            amount = float(amount_text)
            if sign == ">":
                if lower_percent is not None:
                    raise ValueError(f"{label} row has more than one '>' figure.")
                lower_percent, lower_amount = percent, amount
            else:
                if upper_percent is not None:
                    raise ValueError(f"{label} row has more than one '<' figure.")
                upper_percent, upper_amount = percent, amount

        if lower_percent is None:
            if band is not SeverityBand.INSIGNIFICANT:
                raise ValueError(f"{label} row is missing its '>' lower-bound figure.")
            lower_percent = 0.0
        if upper_percent is None and band is not SeverityBand.SEVERE:
            raise ValueError(f"{label} row is missing its '<' upper-bound figure.")

        brackets[band] = SeverityBandBracket(
            lower_percent=lower_percent,
            lower_amount=lower_amount,
            upper_percent=upper_percent,
            upper_amount=upper_amount,
        )

    missing = [band.name for band in _BAND_ORDER if band not in brackets]
    if missing:
        raise ValueError(f"Severity definition is missing rows for: {missing}")

    for current_band, next_band in zip(_BAND_ORDER, _BAND_ORDER[1:]):
        current = brackets[current_band]
        following = brackets[next_band]
        if current.upper_percent is None or following.lower_percent is None or not math.isclose(
            current.upper_percent, following.lower_percent, rel_tol=1e-9
        ):
            raise ValueError(
                f"Boundary percentage mismatch between {current_band.name} "
                f"(<{current.upper_percent}) and {next_band.name} (>{following.lower_percent})."
            )

    version = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SeverityDefinition(version=version, preceding_year_ebit=preceding_year_ebit, brackets=dict(brackets))


def _ebit_consistency_entries(definition: SeverityDefinition, band: SeverityBand) -> list[dict[str, Any]]:
    bracket = definition.brackets[band]
    entries: list[dict[str, Any]] = []
    for side, percent, amount in (
        ("lower", bracket.lower_percent, bracket.lower_amount),
        ("upper", bracket.upper_percent, bracket.upper_amount),
    ):
        if amount is None or percent is None:
            continue
        expected = definition.preceding_year_ebit * percent
        deviation_percent = round(abs(amount - expected) / expected * 100, 2) if expected else None
        entries.append(
            {
                "boundary": side,
                "percent_of_ebit": percent,
                "source_amount_sgd_millions": amount,
                "expected_amount_sgd_millions": round(expected, 2),
                "deviation_percent": deviation_percent,
                "exceeds_2_percent_threshold": deviation_percent is not None and deviation_percent > _EBIT_CONSISTENCY_THRESHOLD_PERCENT,
            }
        )
    return entries


def _boundary_conflicts(definition: SeverityDefinition) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for current_band, next_band in zip(_BAND_ORDER, _BAND_ORDER[1:]):
        current = definition.brackets[current_band]
        following = definition.brackets[next_band]
        if current.upper_amount is None or following.lower_amount is None:
            continue
        if round(current.upper_amount, 1) != round(following.lower_amount, 1):
            conflicts.append(
                {
                    "boundary_percent": current.upper_percent,
                    "band_below": current_band.name,
                    "band_below_upper_amount_sgd_millions": current.upper_amount,
                    "band_above": next_band.name,
                    "band_above_lower_amount_sgd_millions": following.lower_amount,
                }
            )
    return conflicts


def severity_definition_to_json(definition: SeverityDefinition) -> str:
    """Human-review JSON: derived thresholds, EBIT-consistency, and genuine
    cross-band boundary conflicts only (same-percentage figures that disagree)."""
    bands: list[dict[str, Any]] = []
    for band in _BAND_ORDER:
        bracket = definition.brackets[band]
        bands.append(
            {
                "band": band.name,
                "lower_bound_inclusive_percent_of_ebit": bracket.lower_percent,
                "derived_lower_bound_sgd_millions": round(definition.preceding_year_ebit * bracket.lower_percent, 2),
                "upper_bound_percent_of_ebit": bracket.upper_percent,
                "source_lower_amount_sgd_millions": bracket.lower_amount,
                "source_upper_amount_sgd_millions": bracket.upper_amount,
                "ebit_consistency": _ebit_consistency_entries(definition, band),
            }
        )
    return json.dumps(
        {
            "severity_definition_version_sha256": definition.version,
            "preceding_year_ebit_sgd_millions": definition.preceding_year_ebit,
            "financial_threshold_interpretation": "lower-bound-inclusive percentage of preceding-year EBIT",
            "bands": bands,
            "boundary_conflicts": _boundary_conflicts(definition),
        },
        indent=2,
        sort_keys=True,
    )
