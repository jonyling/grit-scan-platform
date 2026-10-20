"""grit notes and grit profile: file a reader's notes and check the profile distilled from them (spec 39)."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from grit.profiles.reader import add_note, check, load_reader_profile, notes, readers_dir, vocabulary


def notes_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grit notes", description="File a reader's note, or list the notes on file. Notes live in secrets/readers/<profile>/notes.")
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser("add", help="File a note under the next number, N1, N2, ...")
    add.add_argument("--profile", required=True)
    add.add_argument("--file", type=Path, required=True, help="The note: an email saved as text, meeting notes, a chat export.")
    add.add_argument("--source", required=True, help="One word: email, meeting, teams, chat, call.")
    add.add_argument("--date", type=date.fromisoformat, required=True, help="The date the reader said it, YYYY-MM-DD.")
    add.add_argument("--move", action="store_true", help="Move the file instead of copying it.")
    listing = commands.add_parser("list", help="List the notes on file.")
    listing.add_argument("--profile", help="One reader. Default: every reader with notes.")
    args = parser.parse_args(argv)
    if args.action == "add":
        note = add_note(args.profile, args.file, on=args.date, source=args.source, move=args.move)
        print(f"Filed {note.note_id} for {args.profile}: {note.path}")
        print("Next: update the reader's profile.md and cite the note, then run: grit profile check --profile " + args.profile)
        return 0
    root = readers_dir()
    profiles = [args.profile] if args.profile else sorted(path.name for path in root.iterdir() if path.is_dir()) if root.is_dir() else []
    for profile_id in profiles:
        filed = notes(profile_id)
        print(f"{profile_id}: {len(filed)} note(s)")
        for note in filed:
            print(f"  {note.note_id}  {note.on}  {note.source:<8}  {note.path.name}")
        inbox = sorted((root / profile_id / "inbox").glob("*.md")) if (root / profile_id / "inbox").is_dir() else []
        for path in inbox:
            print(f"  inbox     {path.name}  (file it: grit notes add --profile {profile_id} --file \"{path}\" --source copilot --date {path.name[:10]} --move)")
    if not profiles:
        print(f"No notes yet in {root}.")
    return 0


def profile_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grit profile", description="Check a reader's profile.md against the packs, conditions, sections and filed notes.")
    commands = parser.add_subparsers(dest="action", required=True)
    checking = commands.add_parser("check", help="List every problem with the profile. Exit code 1 if there is one.")
    checking.add_argument("--profile", required=True)
    proposing = commands.add_parser("propose", help="The Profiler drafts the next profile version from the filed notes, for review. It changes nothing.")
    proposing.add_argument("--profile", required=True)
    proposing.add_argument("--force", action="store_true", help="Propose even when no note is new since the current version.")
    proposing.add_argument("--model")
    accepting = commands.add_parser("accept", help="Make the latest proposal the reader's profile, after its checks. The current one goes to history/.")
    accepting.add_argument("--profile", required=True)
    fitting = commands.add_parser("fit", help="Write the fit report for a reader and a run.")
    fitting.add_argument("--profile", required=True)
    fitting.add_argument("--run", help="Run id. Default: the latest completed run of each pack the reader has a brief for.")
    args = parser.parse_args(argv)
    if args.action == "propose":
        return _propose(args)
    if args.action == "accept":
        from grit.profiles.profiler import accept

        result = accept(args.profile, known=vocabulary())
        print(f"Profile v{result['version']} is now in force for {args.profile}" + (f"; v{result['previous']} is kept in history/." if result["previous"] else "."))
        return 0
    if args.action == "fit":
        return _fit(args)
    reader = load_reader_profile(args.profile)
    if reader is None:
        print(f"No profile.md for {args.profile} in {readers_dir()}. The defaults apply.")
        return 0
    problems = check(reader, vocabulary(), notes(args.profile))
    front = reader.settings
    print(f"{reader.path}")
    print(f"Version {reader.version}, updated {reader.updated or 'not stated'}, based on {', '.join(reader.based_on) or 'no notes'}.")
    print(f"Packs: {', '.join(front.get('packs') or ['as configured'])}. Aperture: {front.get('aperture') or 'as configured'}.")
    for key in ("emphasis", "de_emphasis"):
        section = reader.section(key)
        counts = ", ".join(f"{len(section.get(name) or [])} {name}" for name in ("conditions", "topics", "countries"))
        print(f"{key.replace('_', '-').capitalize()}: {counts}.")
    sources = reader.section("sources")
    print(f"Sources: {len(sources.get('trust') or [])} trusted, {len(sources.get('distrust') or [])} distrusted. Signal buttons: {'on' if front.get('signal_buttons') else 'off'}.")
    if problems:
        print(f"{len(problems)} problem(s). The brief and the dashboard will not load until they are fixed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("No problems. The profile applies to the next brief.")
    return 0


def _propose(args: argparse.Namespace) -> int:
    from grit.cli import _client, _model
    from grit.pool import Pool
    from grit.profiles import _load_json_profiles
    from grit.profiles.profiler import propose
    from grit.settings import CONFIG_DIR

    profiles = _load_json_profiles(CONFIG_DIR / "profiles")
    if args.profile not in profiles:
        raise SystemExit(f"Unknown profile {args.profile}.")
    result = propose(profiles[args.profile], client=_client(), model=_model(args.model), known=vocabulary(), pool=Pool(), force=args.force)
    messages = {"NO_NOTES": "No notes are filed for this reader. File one with grit notes add.",
                "NO_NEW_NOTES": "Every filed note is already in the profile. Use --force to propose anyway."}
    if result["status"] in messages:
        print(messages[result["status"]])
        return 0
    print(f"{result['status']}: {result['path']}")
    if result.get("review"):
        print(f"Review: {result['review']}")
    for problem in result["problems"]:
        print(f"  - {problem}")
    return 0 if result["status"] == "PROPOSED" else 1


def _fit(args: argparse.Namespace) -> int:
    from grit.pool import Pool
    from grit.profiles import load_profiles
    from grit.profiles.fit import write_fit_report

    pool = Pool()
    profile = load_profiles().get(args.profile)
    if profile is None:
        raise SystemExit(f"Unknown profile {args.profile}.")
    if args.run:
        runs = [args.run]
    else:
        runs = [row["run_id"] for pack in profile.briefs if (row := pool.latest_run(pack))]
    for run_id in runs:
        print(f"Wrote {write_fit_report(pool, run_id, profile)}")
    return 0
