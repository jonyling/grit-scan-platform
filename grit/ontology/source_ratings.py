"""grit ratings: the shared source credibility ratings in config/source-ratings.json.

One list for every pack, owned by R&A. A pack's own register rating wins. This list fills in where the register has none.
grit pack-build never touches it. Each rating is PROPOSED until a named person confirms it.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from grit.ontology.matchers.credibility_lookup import RATINGS
from grit.settings import CONFIG_DIR

PATH = CONFIG_DIR / "source-ratings.json"
SEED_PACKS = ("urs-tw", "ste")
_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _empty() -> dict[str, Any]:
    return {
        "registerId": "STE-SOURCE-RATINGS",
        "owner": "Group Risk & Assurance",
        "rule": "HIGH, MEDIUM or LOW per outlet domain. A pack's own register rating wins. A rating is PROPOSED until a named person confirms it.",
        "ratings": [],
        "contested": [],
    }


def load(path: Path = PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else _empty()


def save(document: dict[str, Any], path: Path = PATH) -> None:
    document["ratings"] = sorted(document.get("ratings") or [], key=lambda row: row["domain"])
    document["contested"] = sorted(document.get("contested") or [], key=lambda row: row["domain"])
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _domain(text: str) -> str:
    from grit.ontology.matchers.credibility_lookup import _domain as host

    text = text.strip().lower()
    return (host(text if "://" in text else f"https://{text}") or text).removeprefix("www.")


def seed(config_dir: Path = CONFIG_DIR, packs: tuple[str, ...] = SEED_PACKS) -> dict[str, Any]:
    """Ratings already given in the named packs' source registers, one per domain. Where two registers differ, the lower
    rating is taken and both are recorded. Every seeded rating is PROPOSED."""
    from grit.ontology.pack import load_ontology_pack

    found: dict[str, dict[str, Any]] = {}
    for pack in packs:
        register = load_ontology_pack(config_dir, pack).source_register
        for row in register.get("sources") or []:
            rating, domain = row.get("credibilityRating"), str(row.get("sourceDomain") or "").lower()
            if rating not in RATINGS or not domain:
                continue
            entry = found.setdefault(domain, {"domain": domain, "sourceName": row.get("sourceName"), "rating": rating, "status": "PROPOSED", "basis": []})
            entry["basis"].append(f"{register.get('registerId') or pack}: {rating}")
            if _ORDER[rating] < _ORDER[entry["rating"]]:
                entry["rating"] = rating
    document = _empty()
    for entry in found.values():
        entry["basis"] = "; ".join(entry["basis"]) + (". The registers differ, so the lower rating is used." if len({part.rsplit(": ", 1)[1] for part in entry["basis"]}) > 1 else ".")
    document["ratings"] = list(found.values())
    return document


def set_rating(document: dict[str, Any], domain: str, rating: str, *, by: str, name: str | None = None, today: date | None = None) -> dict[str, Any]:
    """Set one domain's rating, confirmed by the named person."""
    rating = rating.upper()
    if rating not in RATINGS:
        raise ValueError(f"Rating must be one of {', '.join(RATINGS)}")
    domain = _domain(domain)
    rows = document.setdefault("ratings", [])
    entry = next((row for row in rows if row["domain"] == domain), None)
    if entry is None:
        entry = {"domain": domain, "sourceName": name or domain, "basis": "Set by hand."}
        rows.append(entry)
    entry.update(rating=rating, status="CONFIRMED", confirmedBy=by, confirmedOn=(today or date.today()).isoformat())
    if name:
        entry["sourceName"] = name
    # A rating decision replaces a contested listing for the same outlet.
    document["contested"] = [row for row in document.get("contested") or [] if row["domain"] != domain]
    return entry


def contest(document: dict[str, Any], domain: str, *, by: str, reason: str, today: date | None = None) -> dict[str, Any]:
    """List an outlet as contested, confirmed by the named person: a labeled signal, never the sole support for a fact.
    Any rating for the outlet is removed."""
    if not reason.strip():
        raise ValueError("A contested listing needs a reason.")
    domain = _domain(domain)
    document["ratings"] = [row for row in document.get("ratings") or [] if row["domain"] != domain]
    rows = document.setdefault("contested", [])
    entry = next((row for row in rows if row["domain"] == domain), None)
    if entry is None:
        entry = {"domain": domain}
        rows.append(entry)
    entry.update(reason=reason.strip(), status="CONFIRMED", confirmedBy=by, confirmedOn=(today or date.today()).isoformat())
    return entry


def uncontest(document: dict[str, Any], domain: str) -> bool:
    domain = _domain(domain)
    before = len(document.get("contested") or [])
    document["contested"] = [row for row in document.get("contested") or [] if row["domain"] != domain]
    return len(document["contested"]) < before


def confirm(document: dict[str, Any], *, by: str, domains: list[str] | None = None, today: date | None = None) -> int:
    """Confirm the named domains, or every PROPOSED rating when none is named."""
    wanted = {_domain(domain) for domain in domains or []}
    count = 0
    for entry in [*(document.get("ratings") or []), *(document.get("contested") or [])]:
        if entry.get("status") == "CONFIRMED" or (wanted and entry["domain"] not in wanted):
            continue
        entry.update(status="CONFIRMED", confirmedBy=by, confirmedOn=(today or date.today()).isoformat())
        count += 1
    return count


def unrated_outlets(run_id: str, pool: Any = None) -> list[tuple[str, int]]:
    """Outlets with no R&A band among a run's assessed signals, with how many signals each carried, most first."""
    from collections import Counter

    from grit.core.source_bands import host_of
    from grit.pool import Pool

    pool = pool or Pool()
    counts: Counter[str] = Counter()
    for row in pool.rows("SELECT outlets_json FROM assessments WHERE run_id = ?", (run_id,)):
        for outlet in row.get("outlets") or []:
            if outlet.get("band") == "UNRATED":
                counts[host_of(outlet.get("url"), outlet.get("outlet"))] += 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grit ratings", description="List, seed, set or confirm the shared source credibility ratings.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Print every rating with its status.")
    seed_cmd = sub.add_parser("seed", help="Create the file from the ratings in the URS Taiwan and Group registers, all PROPOSED.")
    seed_cmd.add_argument("--force", action="store_true", help="Replace an existing file.")
    set_cmd = sub.add_parser("set", help="Set one outlet's rating, confirmed by you.")
    set_cmd.add_argument("domain", help="The outlet's domain or URL, such as reuters.com.")
    set_cmd.add_argument("rating", choices=[*RATINGS, *(rating.lower() for rating in RATINGS)])
    set_cmd.add_argument("--by", required=True, help="Your name.")
    set_cmd.add_argument("--name", help="The outlet's name, such as Reuters.")
    confirm_cmd = sub.add_parser("confirm", help="Confirm PROPOSED ratings and contested listings: the named domains, or all of them.")
    confirm_cmd.add_argument("domains", nargs="*")
    confirm_cmd.add_argument("--by", required=True, help="Your name.")
    contest_cmd = sub.add_parser("contest", help="List an outlet as contested: never the sole support for a fact.")
    contest_cmd.add_argument("domain")
    contest_cmd.add_argument("--by", required=True, help="Your name.")
    contest_cmd.add_argument("--reason", required=True, help="Why the outlet is contested.")
    uncontest_cmd = sub.add_parser("uncontest", help="Remove an outlet from the contested list.")
    uncontest_cmd.add_argument("domain")
    unrated_cmd = sub.add_parser("unrated", help="List a run's outlets with no R&A band, most-used first, for banding.")
    unrated_cmd.add_argument("--run", required=True, help="The run, such as 2026-11-02-urs.")
    unrated_cmd.add_argument("--limit", type=int, default=40)
    args = parser.parse_args(argv)
    if args.command == "unrated":
        from grit.pool import Pool

        pool = Pool()
        banded = (pool.one("SELECT COUNT(*) AS n FROM assessments WHERE run_id = ? AND outlets_json IS NOT NULL AND outlets_json != '[]'",
                           (args.run,)) or {}).get("n")
        if not banded:
            print(f"{args.run} in {pool.path} has no source bands: the run is missing, or it was assessed before bands existed "
                  "(3 November 2026). Run the weekly for that date in this database, or set GRIT_DB to the database that has it.")
            return 1
        found = unrated_outlets(args.run, pool)
        for domain, count in found[: args.limit]:
            print(f"{count:4}  {domain}")
        print(f"{len(found)} unrated outlets in {args.run}. Band one with: grit ratings set DOMAIN HIGH|MEDIUM|LOW --by NAME")
        return 0

    if args.command == "seed":
        if PATH.is_file() and not args.force:
            print(f"{PATH} exists. Use --force to replace it.")
            return 1
        document = seed()
        document["contested"] = load().get("contested") or []
        save(document)
        print(f"Wrote {len(document['ratings'])} PROPOSED ratings to {PATH}.")
        return 0
    document = load()
    if args.command == "list":
        for entry in sorted(document.get("ratings") or [], key=lambda row: (-_ORDER[row["rating"]], row["domain"])):
            print(f"{entry['rating']:7} {entry.get('status', 'PROPOSED'):10} {entry['domain']:32} {entry.get('sourceName') or ''}")
        for entry in document.get("contested") or []:
            print(f"{'CONTESTED':9} {entry.get('status', 'PROPOSED'):10} {entry['domain']:32} {entry.get('reason') or ''}")
        print(f"{len(document.get('ratings') or [])} ratings, {len(document.get('contested') or [])} contested.")
        return 0
    if args.command == "contest":
        entry = contest(document, args.domain, by=args.by, reason=args.reason)
        save(document)
        print(f"{entry['domain']}: contested, confirmed by {args.by}.")
        return 0
    if args.command == "uncontest":
        removed = uncontest(document, args.domain)
        save(document)
        print(f"{args.domain}: {'removed from' if removed else 'not on'} the contested list.")
        return 0
    if args.command == "set":
        entry = set_rating(document, args.domain, args.rating, by=args.by, name=args.name)
        save(document)
        print(f"{entry['domain']}: {entry['rating']}, confirmed by {args.by}.")
        return 0
    count = confirm(document, by=args.by, domains=args.domains)
    save(document)
    print(f"Confirmed {count} rating(s).")
    return 0
