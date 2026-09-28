"""One-off backfill: refresh STE theme match, Risk Level, Trend, and Basis of
Relevance to Business on wild-scan signals already sitting in Dataverse.

Only rows whose ``grit_correlationid`` came from a wild-scan lane (GDELT or
Currents) are touched, identified by the same prefixes those CLIs already
write (``GDELT-`` / ``CURRENTS-``). A weekly ranked finding's row already
carries a condition-anchored basis from ``grit_scan.relevance_scoring`` and
is left alone -- this script never overwrites that with the narrower,
title-only wild-scan basis.

Approval fields, workflow state, and everything else on the row are left
untouched; only the three PowerApps display columns are refreshed.
"""

from __future__ import annotations

import argparse

from dataverse.choices import TREND_NO_MATERIAL_CHANGE
from dataverse.client import DataverseClient, DataverseError
from grit_scan.wild_scan_relevance import (
    classify_wild_scan_risk_level,
    compose_wild_scan_basis,
    match_wild_scan_condition,
    match_wild_scan_themes,
    wild_scan_headline,
)

_WILD_SCAN_PREFIXES = ("GDELT-", "CURRENTS-")
_RISK_LEVEL_CHOICE = {"HIGH": 100002300, "MEDIUM": 100002301, "LOW": 100002302}
_SELECT = "grit_signalid,grit_signalname,grit_headline,grit_sourcename,grit_correlationid"
_THEMES_REGISTER = "config/urs-tw/URS-TW-STE-Annual-Risks.json"
_BASELINE_REGISTER = "config/urs-tw/URS-TW-Global-Baseline.json"


def main(argv: list[str] | None = None) -> int:
    from grit_scan.local_env import load_local_env

    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would change. Write nothing.")
    args = parser.parse_args(argv)

    try:
        client = DataverseClient.from_env()
    except DataverseError as exc:
        print(exc)
        return 1

    rows = client.get_all(f"grit_signals?$select={_SELECT}")
    wild_rows = [
        row for row in rows if str(row.get("grit_correlationid") or "").startswith(_WILD_SCAN_PREFIXES)
    ]
    print(f"Signals total: {len(rows)}; wild-scan rows to refresh: {len(wild_rows)}")

    updated = 0
    for row in wild_rows:
        text = str(row.get("grit_signalname") or "")
        matched_themes = match_wild_scan_themes(text, _THEMES_REGISTER)
        condition = match_wild_scan_condition(matched_themes, _BASELINE_REGISTER)
        risk_level = classify_wild_scan_risk_level(matched_themes)
        headline = wild_scan_headline(condition)
        basis = compose_wild_scan_basis(condition, publisher=row.get("grit_sourcename"))
        print(f"  {row['grit_signalid']} | {risk_level} | {headline}")
        if args.dry_run:
            continue
        client.patch(
            "grit_signals",
            row["grit_signalid"],
            {
                "grit_headline": headline,
                "grit_trend": TREND_NO_MATERIAL_CHANGE,
                "grit_risklevel": _RISK_LEVEL_CHOICE[risk_level],
                "grit_businessrelevancebasis": basis,
            },
        )
        updated += 1
    print("Dry run. Dataverse was not changed." if args.dry_run else f"Updated: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
