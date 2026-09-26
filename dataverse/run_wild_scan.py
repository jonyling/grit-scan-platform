"""Read one GDELT 15-minute export, or two Currents headline pulls. No scoring, no approval.

Currents is currentsapi.services. It is not a GDELT call.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from acquisition.currents import collect_currents
from acquisition.gdelt import collect_export_scan
from dataverse.client import DataverseClient, DataverseError
from supervisor.service import Supervisor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", required=True, help="One condition. Do not combine them.")
    parser.add_argument("--slices", type=int, default=1, help="15-minute export files to read, from 1 to 4.")
    parser.add_argument("--currents", action="store_true", help="Currents headline pull. At most two queries. Not GDELT.")
    parser.add_argument("--timespan", default="7d", help="Unused. Kept so older commands still parse.")
    parser.add_argument("--max-records", type=int, default=10)
    parser.add_argument("--pause-seconds", type=float, default=5, help="Unused. Kept so older commands still parse.")
    parser.add_argument("--dry-run", action="store_true", help="With --currents, fetch headlines and write nothing.")
    args = parser.parse_args(argv)
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not args.currents:
        hits = collect_export_scan(args.query, max_records=args.max_records, slices=args.slices)
        print(f"GDELT export hits: {len(hits)}")
        for hit in hits:
            print(f"  {hit.publisher or 'unknown'} | {hit.url}")
        print("Dataverse was not changed. A 15-minute export has no headline.")
        return 0 if hits else 2
    if len(args.query) > 2:
        print("Currents allows one or two pulls per run, not a firehose.")
        return 1
    client = None
    if not args.dry_run:
        try:
            client = DataverseClient.from_env()
        except DataverseError as exc:
            print(exc)
            return 1
    candidates = collect_currents(
        args.query,
        fetched_at=fetched_at,
        max_records=min(args.max_records, 20),
    )
    print(f"Currents candidates: {len(candidates)}")
    if not candidates:
        print("No candidates. Dataverse was not changed.")
        return 2
    if args.dry_run:
        for candidate in candidates:
            print(f"  {candidate.publisher or 'unknown'} | {candidate.title}")
        print("Dry run. Dataverse was not changed.")
        return 0
    correlation_id = "CURRENTS-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    supervisor = Supervisor(actor_id="supervisor-currents")
    result = supervisor.persist_acquired_signals(
        candidates,
        client,
        correlation_id=correlation_id,
        accepted_at=fetched_at,
        reason_code="CURRENTS_CANDIDATE",
        reason="Currents headline candidate. Article body was not fetched. Outlet credibility is not established.",
        configuration_version="currents-1",
    )
    print(f"Execution: {result['execution_id']}")
    print(f"Created: {len(result['created'])}")
    print(f"Already present: {len(result['reused'])}")
    print(f"Skipped: {len(result['skipped'])}")
    _write_snapshot(candidates, correlation_id=correlation_id, result=result)
    return 0


def _write_snapshot(candidates, *, correlation_id: str, result: dict) -> None:
    path = Path(__file__).resolve().parents[1] / "output" / "currents-latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "correlation_id": correlation_id,
        "created": len(result["created"]),
        "signals": [
            {
                "title": candidate.title,
                "publisher": candidate.publisher,
                "url": candidate.url,
                "published_at": candidate.published_at,
                "evidence": "LOW",
            }
            for candidate in candidates
            if candidate.title and candidate.url
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Snapshot: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
