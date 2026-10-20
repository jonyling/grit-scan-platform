"""The Profiler and the fit report (spec 41), offline: a stub model and the reader-scan fixture's pool."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from grit.pipeline.reader_scan import stub_client
from grit.profiles.profiler import accept, changed_settings, check_proposal, propose, words
from grit.profiles.reader import Vocabulary, add_note, load_reader_profile, notes
from test_reader_profiles import GOOD, KNOWN, _profile, _write

TODAY = date(2026, 10, 21)
V2 = GOOD.replace("version: 1", "version: 2").replace("updated: 2026-10-20", "updated: 2026-10-21").replace("based_on: [N1]", "based_on: [N1, N2]") \
    .replace("  countries: [Taiwan]", "  countries: [Taiwan, Qatar]").replace("Export controls and memory prices first (N1).", "Export controls and memory prices first (N1).\nQatar's grid matters (N2).")


def _second_note(root: Path) -> None:
    source = root / "n2.md"
    source.write_text("Meeting with Wilson, 21 October. He said Qatar's grid and energy security matter to the URS rail bids.", encoding="utf-8")
    add_note("wilson", source, on=TODAY, source="meeting", root=root)


def _raw(text: str = V2, **changes) -> dict:
    return {"profile_md": text, "changes": [{"setting": "emphasis.countries", "change": "Added Qatar.", "note": "N2", "quote": "Qatar's grid and energy security matter"},
                                            {"setting": "body", "change": "Added the Qatar line.", "note": "N2", "quote": "Qatar's grid and energy security"}],
            "open_questions": ["Does Qatar matter beyond rail?"], "reasoning": "N2 adds Qatar.", **changes}


def _check(root: Path, raw: dict) -> list[str]:
    current = load_reader_profile("wilson", root)
    problems, _proposal = check_proposal(raw, profile_id="wilson", current=current, filed=notes("wilson", root), known=KNOWN, today=TODAY.isoformat(), path=root / "wilson" / "profile.md")
    return problems


def test_words_ignore_punctuation_and_broken_characters() -> None:
    assert words("Qatar \ufffd energy, electrical") == "qatar energy electrical"
    assert changed_settings({"version": 1, "emphasis": {"topics": ["a"]}}, {"version": 2, "emphasis": {"topics": ["a", "b"]}, "aperture": "WIDE"}) == ["aperture", "emphasis.topics"]


def test_a_grounded_proposal_passes(tmp_path: Path) -> None:
    _write(tmp_path)
    _second_note(tmp_path)
    assert _check(tmp_path, _raw()) == []


@pytest.mark.parametrize(("raw", "problem"), [
    (lambda: _raw(V2.replace("version: 2", "version: 3")), "VERSION"),
    (lambda: _raw(V2.replace("based_on: [N1, N2]", "based_on: [N1]")), "BASED_ON"),
    (lambda: _raw(V2.replace("updated: 2026-10-21", "updated: 2026-10-01")), "UPDATED"),
    (lambda: _raw(changes=[{"setting": "body", "note": "N2", "quote": "Qatar's grid and energy security"}]), "UNLISTED_CHANGE: emphasis.countries"),
    (lambda: _raw(changes=[{"setting": "emphasis.countries", "note": "N2", "quote": "Qatar's grid is collapsing tomorrow"}]), "is not in N2"),
    (lambda: _raw(changes=[{"setting": "emphasis.countries", "note": "N7", "quote": "Qatar's grid and energy security"}]), "N7 is not a filed note"),
    (lambda: _raw(V2.replace("[Taiwan, Qatar]", "[Taiwan, Qatar, Oman]")), "UNGROUNDED: emphasis.countries: 'Oman'"),
    (lambda: _raw(V2.replace("Qatar's grid matters (N2).", 'He said "Qatar is the priority" (N2).')), "QUOTE_NOT_IN_NOTE"),
    (lambda: _raw(V2.replace("Qatar's grid matters (N2).", "Qatar's grid matters.")), "cite the note"),
    (lambda: _raw(V2.replace("aperture: WIDE", "aperture: NARROW")), "PROFILE: aperture"),
    (lambda: {"profile_md": 5}, "BAD_SHAPE"),
    (lambda: _raw(V2.replace("Qatar's grid matters (N2).", 'Wants "AI" first, and "a Qatar blockade" (N2).')), "'a Qatar blockade' is not in N2"),
])
def test_every_proposal_fault_is_named(tmp_path: Path, raw, problem: str) -> None:
    _write(tmp_path)
    _second_note(tmp_path)
    found = _check(tmp_path, raw())
    assert any(problem in item for item in found), found


def test_propose_writes_a_proposal_for_review_and_changes_nothing(tmp_path: Path) -> None:
    _write(tmp_path)
    _second_note(tmp_path)
    before = (tmp_path / "wilson" / "profile.md").read_text()
    seen = []
    client = stub_client(lambda _s, payload: seen.append(payload) or _raw())
    result = propose(_profile(), client=client, model="stub", known=KNOWN, root=tmp_path, today=TODAY)
    assert result["status"] == "PROPOSED" and Path(result["path"]).name == "profile_v2.md"
    assert (tmp_path / "wilson" / "profile.md").read_text() == before, "a proposal never changes the profile in force"
    review = Path(result["review"]).read_text()
    assert "Added Qatar" in review and "Does Qatar matter beyond rail?" in review and "+  countries: [Taiwan, Qatar]" in review
    assert [note["new"] for note in seen[0]["notes"]] == [False, True] and seen[0]["next_version"] == 2
    accepted = accept("wilson", known=KNOWN, root=tmp_path)
    assert accepted == {"profile": str(tmp_path / "wilson" / "profile.md"), "version": 2, "previous": 1}
    assert load_reader_profile("wilson", tmp_path).version == 2 and (tmp_path / "wilson" / "history" / "profile_v1.md").read_text() == before
    assert propose(_profile(), client=client, model="stub", known=KNOWN, root=tmp_path, today=TODAY)["status"] == "NO_NEW_NOTES"


def test_a_failing_proposal_is_redrafted_once_then_withheld(tmp_path: Path) -> None:
    _write(tmp_path)
    _second_note(tmp_path)
    bad = _raw(V2.replace("[Taiwan, Qatar]", "[Taiwan, Qatar, Oman]"))
    replies = iter([bad, bad])
    result = propose(_profile(), client=stub_client(lambda _s, _p: next(replies)), model="stub", known=KNOWN, root=tmp_path, today=TODAY)
    assert result["status"] == "WITHHELD" and result["path"].endswith("profile_v2.withheld.md") and "Oman" in Path(result["review"]).read_text()
    with pytest.raises(FileNotFoundError):
        accept("wilson", known=KNOWN, root=tmp_path)
    replies = iter([bad, _raw()])
    assert propose(_profile(), client=stub_client(lambda _s, _p: next(replies)), model="stub", known=KNOWN, root=tmp_path, today=TODAY)["status"] == "PROPOSED"


def test_a_first_profile_can_be_proposed_from_notes_alone(tmp_path: Path) -> None:
    _second_note(tmp_path)
    first = """---
profile: wilson
version: 1
updated: 2026-10-21
based_on: [N1]
emphasis:
  countries: [Qatar]
---
## What they want to know
Qatar's grid and energy security (N1).
"""
    raw = {"profile_md": first, "changes": [{"setting": "emphasis.countries", "note": "N1", "quote": "Qatar's grid and energy security matter"}]}
    result = propose(_profile(), client=stub_client(lambda _s, _p: raw), model="stub", known=Vocabulary(KNOWN.packs, KNOWN.items, KNOWN.sections), root=tmp_path, today=TODAY)
    assert result["status"] == "PROPOSED", result["problems"]


@pytest.fixture
def scanned(tmp_path: Path, monkeypatch):  # noqa: ANN201
    from test_reader_scan import _scanned

    return _scanned(tmp_path, monkeypatch)


def test_the_fit_report_counts_topics_and_names_silent_lines(scanned, tmp_path: Path, monkeypatch) -> None:
    from grit.copilot.agent import record
    from grit.profiles import load_profiles
    from grit.profiles.fit import write_fit_report

    pool, run = scanned
    readers = tmp_path / "readers"
    monkeypatch.setenv("GRIT_READERS_DIR", str(readers))
    _write(readers, "---\nprofile: wilson\nversion: 1\nbased_on: [N1]\nemphasis:\n  topics: [PLA, quantum]\nsources:\n  trust: [focustaiwan.tw, csis.org]\n---\n"
                    "## Standing questions\nWhere is PLA activity heading? (N1)\n\n## Open questions for the reader\nDoes he want weekly briefs?\n")
    wilson = load_profiles()["wilson"]
    for question in ("What is the Middle East outlook?", "Any news on Saudi Arabia and Qatar?", "/note keep it short", "Quantum milestones?"):
        record(pool, wilson, question, "An answer.")
    record(pool, load_profiles()["mkteo"], "Saudi Arabia again?", "An answer.")
    text = write_fit_report(pool, run.run_id, wilson, root=readers).read_text()
    assert "Reader profile v1" in text and "moved 1 of" in text
    assert "- emphasis topic: quantum" in text and "- trusted source: csis.org" in text and "focustaiwan.tw" not in text.split("## Lines that never fired")[1].split("##")[0]
    asked = text.split("## What the reader asked")[1].split("##")[0]
    assert "Middle East defence and infrastructure spending: 2" in asked and "quantum: 1" in asked
    assert "Saudi Arabia again" not in text and "What is the Middle East outlook" not in text, "the questions themselves never appear"
    assert "Asked about Middle East defence and infrastructure spending 2 times" in text and "Does he want weekly briefs?" in text
    assert "Standing question, 1 shown signals share its key words" in text
    default = write_fit_report(pool, run.run_id, load_profiles()["hwaimay"], root=readers).read_text()
    assert "No reader profile yet" in default and "No notes yet" in default


def test_quotations_are_paired_so_a_short_one_does_not_hide_the_next(tmp_path: Path) -> None:
    _write(tmp_path)
    _second_note(tmp_path)
    ok = _raw(V2.replace("Qatar's grid matters (N2).", 'Wants "AI" first, and "Qatar\'s grid and energy security" (N2).'))
    assert _check(tmp_path, ok) == []


def test_a_withheld_attempt_retires_the_earlier_proposal_of_that_version(tmp_path: Path) -> None:
    _write(tmp_path)
    _second_note(tmp_path)
    assert propose(_profile(), client=stub_client(lambda _s, _p: _raw()), model="stub", known=KNOWN, root=tmp_path, today=TODAY)["status"] == "PROPOSED"
    bad = _raw(V2.replace("[Taiwan, Qatar]", "[Taiwan, Qatar, Oman]"))
    assert propose(_profile(), client=stub_client(lambda _s, _p: bad), model="stub", known=KNOWN, root=tmp_path, today=TODAY, force=True)["status"] == "WITHHELD"
    assert not (tmp_path / "wilson" / "proposals" / "profile_v2.md").exists()
    with pytest.raises(FileNotFoundError):
        accept("wilson", known=KNOWN, root=tmp_path)
