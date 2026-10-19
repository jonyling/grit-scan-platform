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
    if not profiles:
        print(f"No notes yet in {root}.")
    return 0


def profile_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grit profile", description="Check a reader's profile.md against the packs, conditions, sections and filed notes.")
    commands = parser.add_subparsers(dest="action", required=True)
    checking = commands.add_parser("check", help="List every problem with the profile. Exit code 1 if there is one.")
    checking.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
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
