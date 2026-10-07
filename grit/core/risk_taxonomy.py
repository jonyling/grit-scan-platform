"""Parser for the governed URS annual risk taxonomy (13_Annual_Risks.md).

Risk taxonomy is a tag and an optional gate, never a scoring multiplicand:
``resolve_risk_taxonomy_ref()`` only confirms a signal's claimed ref is a
real governed risk line, or reports that it is not. It never contributes to
severity, transmission, or evidence -- a risk category is a label, not a
magnitude.

The document has three separate tables (Key Risks, Tier-2 Risks, Emerging
Risks), each numbered from 1 independently, so ``ref`` is tier-prefixed
(e.g. ``KEY-3``) to stay unique across all three.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_KEY_HEADER = ("Ref", "Key Risk", "Key Drivers / Issues", "Key Controls", "Action Plans")
_TIER2_HEADER = ("S/N", "Tier-2 Risk", "Rationalisation")
_EMERGING_HEADER = ("No", "Emerging Risk", "Treatment / Monitoring Category", "Action Plans & Timeline")


class RiskTaxonomyTier(str, Enum):
    KEY = "KEY"
    TIER_2 = "TIER_2"
    EMERGING = "EMERGING"


_TABLES = (
    (_KEY_HEADER, "KEY", RiskTaxonomyTier.KEY),
    (_TIER2_HEADER, "TIER2", RiskTaxonomyTier.TIER_2),
    (_EMERGING_HEADER, "EMERGING", RiskTaxonomyTier.EMERGING),
)


@dataclass(frozen=True)
class RiskTaxonomyEntry:
    """One governed risk line. ``ref`` is unique across all three tiers."""

    ref: str
    tier: RiskTaxonomyTier
    name: str


def _clean(value: str) -> str:
    return value.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ").strip()


def _cells(row: str) -> list[str]:
    return [_clean(cell) for cell in row.strip().strip("|").split("|")]


def parse_risk_taxonomy(path: str | Path) -> tuple[RiskTaxonomyEntry, ...]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    rows = [_cells(line) for line in lines if line.strip().startswith("|")]

    entries: list[RiskTaxonomyEntry] = []
    for header, prefix, tier in _TABLES:
        try:
            start = next(index for index, cells in enumerate(rows) if tuple(cells[: len(header)]) == header)
        except StopIteration:
            raise ValueError(f"Could not find the '{prefix}' table header in {path}.")
        for cells in rows[start + 1 :]:
            if not cells or not cells[0].isdigit():
                break
            entries.append(RiskTaxonomyEntry(ref=f"{prefix}-{cells[0]}", tier=tier, name=cells[1]))

    if not entries:
        raise ValueError(f"No risk taxonomy entries could be parsed from {path}.")
    return tuple(entries)


def resolve_risk_taxonomy_ref(ref: str | None, taxonomy: tuple[RiskTaxonomyEntry, ...]) -> RiskTaxonomyEntry | None:
    """Look up a claimed ref against the governed register; never invents a match."""
    if ref is None:
        return None
    return next((entry for entry in taxonomy if entry.ref == ref), None)
