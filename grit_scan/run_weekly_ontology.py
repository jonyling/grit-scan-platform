"""Run this week's URS-TW standing-condition developments through the Python
impact-scoring Ontology and print a side-by-side against the document's own
trend/evidence labels.

Source-tier assignment below is NOT a governed register lookup -- no such
register exists yet in this codebase (see 30_Source_Taxonomy_and_Credibility_Model.md,
which is a different, Dataverse-side model). Tiers are this script's own
documented, one-off judgement call for the specific outlets appearing in this
week's batch, not a reusable credibility policy.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from grit_scan.impact_scoring import (
    RiskObservations,
    Signal,
    SourceEvidence,
    SourceTier,
    ValueAtRiskBasis,
    assess_impact,
)
from grit_scan.registry_loader import load_transmission_registry
from grit_scan.severity_definition import parse_severity_definition
from grit_scan.workbook import workbook_path

_OUTLET_TIER = {
    "Federal Reserve": SourceTier.TIER_0,
    "Taiwan MND": SourceTier.TIER_0,
    "Reuters": SourceTier.TIER_1,
    "CNBC": SourceTier.TIER_1,
    "Taiwan News": SourceTier.TIER_1,
    "Taipei Times": SourceTier.TIER_1,
    "PLA Tracker": SourceTier.TIER_2,
    "Rare Earth Exchanges": SourceTier.TIER_2,
    "Migrant Times": SourceTier.TIER_2,
    "calculator.name": SourceTier.TIER_4,
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _to_signal(revision_entry: dict, raw_entry: dict, scan_date: date) -> Signal:
    raw_sources = raw_entry.get("sources", [])
    dates = [source["publicationDate"] for source in raw_sources if source.get("publicationDate")]
    latest_date = date.fromisoformat(max(dates)) if dates else scan_date

    sources = tuple(
        SourceEvidence(
            url=source["url"],
            source_tier=_OUTLET_TIER.get(source["name"], SourceTier.UNRATED),
            independent_outlet=source["name"],
            url_check_passed=True,
            dates_cross_checked=len(raw_sources) >= 2,
            figures_cross_checked=len(raw_sources) >= 2,
        )
        for source in raw_sources
    )

    return Signal(
        signal_id=revision_entry["developmentId"],
        scan_date=scan_date,
        published_date=latest_date,
        event_date=latest_date,
        expected_impact_date=None,
        title=revision_entry.get("headline") or revision_entry["condition"],
        body=revision_entry["summary"],
        country="Taiwan",
        entity=None,
        lob=revision_entry.get("domain"),
        sources=sources,
        observations=RiskObservations(
            fatalities=None,
            permanent_incapacity=None,
            debarment_months=None,
            work_suspension_days=None,
            criminal_offence_or_censure=None,
            media_reach=None,
        ),
        value_at_risk=None,
        value_at_risk_basis=ValueAtRiskBasis.INSUFFICIENT_DATA,
        risk_taxonomy_ref=None,
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = _load_json(root / "output" / "2026-09-21" / "revision.json")
    raw_source = json.loads((root / "output" / "2026-09-21" / "raw-scan-source.json").read_text(encoding="utf-8"))
    scan_date = date.fromisoformat(revision["revisionDate"])

    registry = load_transmission_registry(workbook_path(), root / "config" / "registry_aliases.json")
    definition = parse_severity_definition(root / "specs" / "15_Severity_Definition.md", preceding_year_ebit=136.2)

    header = (
        f"{'developmentId':<20} {'our_severity':<14} {'our_transmission':<10} {'our_evidence':<8} {'our_verdict':<20} "
        f"| {'doc_movement':<18} {'doc_confidence':<8}"
    )
    print(header)
    print("-" * len(header))
    for revision_entry, raw_entry in zip(revision["revisions"], raw_source, strict=True):
        signal = _to_signal(revision_entry, raw_entry, scan_date)
        assessment = assess_impact(signal, registry, definition)
        print(
            f"{signal.signal_id:<20} {assessment.severity.max_band.name:<14} {assessment.transmission.rung.value:<10} "
            f"{assessment.evidence.confidence.value:<8} {assessment.verdict.verdict.value:<20} "
            f"| {revision_entry['movement']:<18} {revision_entry['evidenceConfidence']:<8}"
        )


if __name__ == "__main__":
    main()
