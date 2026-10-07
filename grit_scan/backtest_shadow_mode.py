"""Shadow-mode backtest: run assess_impact against the audited-signals export
and print a read-only comparison against the human audit's own ratings.

This is observation only. It does not write to the audited-signals workbook,
does not feed its output back into scoring, and does not treat the human
audit as ground truth to reproduce. The audited export carries no structured
RiskObservations field, no value_at_risk figure, and no expected_impact_date
-- every record maps those as absent rather than inferring them from the
narrative text, so the comparison reflects the engine against real
acquisition-shaped input, not against facts only a human reviewer could read
between the lines.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .impact_scoring import (
    RiskObservations,
    Signal,
    SourceEvidence,
    SourceTier,
    ValueAtRiskBasis,
    assess_impact,
)
from .registry_loader import load_transmission_registry
from .severity_definition import parse_severity_definition
from .workbook import workbook_path as default_workbook
from .xlsx_reader import read_worksheet

_SHEET = "GRITAuditedSignals"


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _load_records(workbook_path: Path) -> list[dict[str, object]]:
    rows = read_worksheet(workbook_path, _SHEET)
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]


def _to_signal(record: dict[str, object], scan_date: date) -> Signal:
    published = _parse_date(record.get("sourcePublicationDate"))
    source = SourceEvidence(
        url=str(record.get("sourceUrl") or ""),
        # No governed SourceTier register exists yet for this export's
        # publishers; treated as TIER_1 for this backtest only.
        source_tier=SourceTier.TIER_1,
        independent_outlet=str(record.get("sourceName") or "UNKNOWN"),
        url_check_passed=bool(record.get("sourceUrl")),
        dates_cross_checked=False,
        figures_cross_checked=False,
    )
    return Signal(
        signal_id=str(record.get("signalId")),
        scan_date=scan_date,
        published_date=published,
        event_date=published,
        expected_impact_date=None,
        title=str(record.get("headline") or ""),
        body=str(record.get("eventSummary") or ""),
        country=str(record.get("countryMarket") or ""),
        entity=None,
        lob=str(record.get("matchedBusinessArea") or "") or None,
        sources=(source,),
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
    )


def run(audited_signals: Path, entity_workbook: Path, aliases_path: Path, severity_definition_path: Path, ebit: float) -> None:
    registry = load_transmission_registry(entity_workbook, aliases_path)
    definition = parse_severity_definition(severity_definition_path, ebit)
    records = _load_records(audited_signals)
    scan_date = date.today()

    header = (
        f"{'signal_id':<18} {'our_severity':<14} {'our_transmission':<16} {'our_evidence':<8} {'our_verdict':<20} "
        f"| {'human_severity':<10} {'human_overall':<13} {'human_evidence':<8}"
    )
    print(header)
    print("-" * len(header))
    for record in records:
        signal = _to_signal(record, scan_date)
        assessment = assess_impact(signal, registry, definition)
        print(
            f"{signal.signal_id:<18} {assessment.severity.max_band.name:<14} {assessment.transmission.rung.value:<16} "
            f"{assessment.evidence.confidence.value:<8} {assessment.verdict.verdict.value:<20} "
            f"| {str(record.get('severityRating', '')):<10} {str(record.get('overallRiskRating', '')):<13} "
            f"{str(record.get('evidenceConfidence', '')):<8}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audited_signals", type=Path)
    parser.add_argument("--entity-workbook", type=Path, default=None, help="Defaults to the workbook named in grit_scan/workbook.py.")
    parser.add_argument("--aliases", type=Path, default=Path("config/registry_aliases.json"))
    parser.add_argument("--severity-definition", type=Path, default=Path("specs/15_Severity_Definition.md"))
    parser.add_argument("--ebit", type=float, required=True, help="Preceding-year EBIT in S$ millions.")
    arguments = parser.parse_args()
    run(arguments.audited_signals, arguments.entity_workbook or default_workbook(), arguments.aliases, arguments.severity_definition, arguments.ebit)


if __name__ == "__main__":
    main()
