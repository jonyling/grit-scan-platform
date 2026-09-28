"""Write a weekly ontology ranking into Dataverse with Trend, Risk Level,
and Basis of Relevance to Business Area populated.

This is the Python, reader-pack-general replacement for the archived
GRIT-Scan repository's URS-TW-only PowerShell cascade
(Import-URSTWAllFindingsFromRevision.ps1). It defaults to the Urban
Solutions ``urs`` pack, not ``urs-tw``: pass ``--pack urs-tw`` only if the
Taiwan-specific baseline is what is actually being reviewed this cycle.

Reads ``ranking.json`` written by ``grit_scan.weekly_pipeline.run_weekly_pipeline``
(or ``rank()`` directly). It does not run the scan itself and does not write
a severity or likelihood score -- Trend, Risk Level, and Basis of Relevance
are indicative communication labels, computed deterministically by
``grit_scan.relevance_scoring`` from the Ontology model alone.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from dataverse.client import DataverseClient, DataverseError
from grit_scan.local_env import load_local_env
from supervisor.service import Supervisor

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ranking", required=True, type=Path, help="Path to a ranking.json from run_weekly_pipeline.")
    parser.add_argument("--pack", default="urs", choices=("urs", "urs-tw"), help="Reader pack this ranking was produced from.")
    parser.add_argument("--country-market", default=None, help="Optional grit_countrymarket value, e.g. Taiwan.")
    parser.add_argument("--line-of-business", default=None, help="Optional grit_lineofbusiness value.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written and write nothing.")
    args = parser.parse_args(argv)

    if not args.ranking.is_file():
        print(f"Ranking file not found: {args.ranking}")
        return 1
    ranking = json.loads(args.ranking.read_text(encoding="utf-8-sig"))
    items = list(ranking.get("rankedItems") or [])
    print(f"Pack: {args.pack}")
    print(f"Ranked findings: {len(items)}")
    if not items:
        print("Nothing to write.")
        return 2

    if args.dry_run:
        for item in items:
            print(
                f"  [{item.get('riskLevel')}/{item.get('trend')}] {item.get('condition')}: "
                f"{item.get('businessRelevanceBasis')}"
            )
        print("Dry run. Dataverse was not changed.")
        return 0

    try:
        client = DataverseClient.from_env()
    except DataverseError as exc:
        print(exc)
        return 1

    accepted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    revision_date = str(ranking.get("revisionDate") or accepted_at[:10])
    correlation_id = f"{args.pack.upper()}-WEEKLY-{revision_date}"
    supervisor = Supervisor(actor_id="supervisor-weekly-import")
    result = supervisor.persist_ranked_findings(
        items,
        client,
        correlation_id=correlation_id,
        accepted_at=accepted_at,
        country_market=args.country_market,
        line_of_business=args.line_of_business,
        configuration_version=f"{args.pack}-weekly-1",
    )
    print(f"Execution: {result['execution_id']}")
    print(f"Created: {len(result['created'])}")
    print(f"Updated (Trend/Risk Level/Basis refreshed): {len(result['updated'])}")
    print(f"Skipped: {len(result['skipped'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
