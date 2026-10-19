"""Reader notes and the profile distilled from them (spec 39): filing, checks, apply, and emphasis. No network, no model."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from grit.profiles import Profile, load_profiles
from grit.profiles.emphasis import CAP, profile_effect
from grit.profiles.reader import Vocabulary, add_note, apply, check, load_reader_profile, notes

KNOWN = Vocabulary(frozenset({"urs", "urs-tw", "dsc"}), {"TW-GLOBAL-03": "Cross-strait military activity"}, frozenset({"PRIORITY_FINDINGS", "OTHER_SIGNALS", "RESPONSE_OPTIONS"}))
GOOD = """---
profile: wilson
version: 1
updated: 2026-10-20
based_on: [N1]
packs: [all]
aperture: WIDE
brief:
  add_sections: [RESPONSE_OPTIONS]
  length: short
  show_review_status: false
emphasis:
  conditions: [TW-GLOBAL-03]
  topics: [export control, HBM]
  countries: [Taiwan]
de_emphasis:
  topics: [ESG]
sources:
  trust: [csis.org]
  distrust: [example-news.com]
---
## What he wants to know
Export controls and memory prices first (N1).

## Open questions for the reader
Does he want the review label?
"""


def _write(root: Path, text: str = GOOD, profile_id: str = "wilson", note: bool = True) -> None:
    folder = root / profile_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile.md").write_text(text, encoding="utf-8")
    if note:
        source = root / "email.txt"
        source.write_bytes(b"Points from the email \x92 with a Windows quote.")
        add_note(profile_id, source, on=date(2026, 10, 9), source="email", root=root)


def _profile(**changes) -> Profile:
    base = dict(profile_id="wilson", name="Wilson", role="Head", aperture="MATERIAL_APERTURE", packs=("urs",), business_scope={}, review_role="REVIEWER",
                red_alerts={}, email=None, briefs={"urs": {"sections": ["PRIORITY_FINDINGS", "OTHER_SIGNALS"], "maxFindings": 8}, "dsc": {"sections": ["PRIORITY_FINDINGS"]}})
    return Profile(**{**base, **changes})


def test_notes_are_numbered_in_order_and_kept_byte_for_byte(tmp_path: Path) -> None:
    source = tmp_path / "in.txt"
    source.write_bytes(b"caf\xe9")
    first = add_note("wilson", source, on=date(2026, 10, 9), source="email", root=tmp_path)
    second = add_note("wilson", source, on=date(2026, 10, 20), source="meeting", root=tmp_path, move=True)
    assert (first.note_id, second.note_id) == ("N1", "N2")
    assert second.path.name == "N2_2026-10-20_meeting.txt" and not source.exists()
    assert first.path.read_bytes() == b"caf\xe9" and first.text() == "caf\ufffd"
    assert [note.note_id for note in notes("wilson", tmp_path)] == ["N1", "N2"]
    with pytest.raises(ValueError):
        add_note("wilson", first.path, on=date(2026, 10, 9), source="Email chain", root=tmp_path)


def test_a_good_profile_has_no_problems(tmp_path: Path) -> None:
    _write(tmp_path)
    reader = load_reader_profile("wilson", tmp_path)
    assert check(reader, KNOWN, notes("wilson", tmp_path)) == []


@pytest.mark.parametrize(("old", "new", "problem"), [
    ("based_on: [N1]", "based_on: [N1, N2]", "N2 is not a filed note"),
    ("packs: [all]", "packs: [urs, mars]", "unknown pack mars"),
    ("aperture: WIDE", "aperture: NARROW", "aperture: one of"),
    ("length: short", "length: brief", "length: one of"),
    ("conditions: [TW-GLOBAL-03]", "conditions: [TW-GLOBAL-99]", "unknown condition or theme TW-GLOBAL-99"),
    ("topics: [ESG]", "topics: [esg]", "too short to match safely"),
    ("topics: [ESG]", "topics: [HBM]", "both name: hbm"),
    ("distrust: [example-news.com]", "distrust: [example news]", "is not a web domain"),
    ("show_review_status: false", "show_review_status: maybe", "true or false"),
    ("aperture: WIDE", "aperture: WIDE\ncolour: blue", "unknown setting colour"),
    ("Export controls and memory prices first (N1).", "Export controls and memory prices first.", "cite the note"),
    ("Export controls and memory prices first (N1).", "Export controls first (N4).", "N4 is not a filed note"),
    ("version: 1", "version: two", "version: a whole number"),
    ("aperture: WIDE", "aperture: [WIDE]", "aperture: one of"),
    ("length: short", "length: [short]", "length: one of"),
    ("aperture: WIDE", "aperture: WIDE\nbriefs: [urs]", "briefs: a set of settings"),
])
def test_every_mistake_is_named(tmp_path: Path, old: str, new: str, problem: str) -> None:
    _write(tmp_path, GOOD.replace(old, new))
    found = check(load_reader_profile("wilson", tmp_path), KNOWN, notes("wilson", tmp_path))
    assert any(problem in item for item in found), found


def test_apply_overrides_only_what_the_profile_sets(tmp_path: Path) -> None:
    _write(tmp_path)
    applied = apply(_profile(), load_reader_profile("wilson", tmp_path), KNOWN)
    assert applied.packs == ("dsc", "urs", "urs-tw") and applied.aperture == "WIDE_APERTURE"
    assert applied.briefs["urs"]["sections"] == ["PRIORITY_FINDINGS", "OTHER_SIGNALS", "RESPONSE_OPTIONS"]
    assert applied.briefs["urs"]["maxFindings"] == 5 and applied.briefs["urs"]["showReviewStatus"] is False
    assert applied.reader.names == {"TW-GLOBAL-03": "Cross-strait military activity"}
    assert not applied.signal_buttons and "v1" in applied.in_force
    untouched = apply(_profile(), load_reader_profile("wilson", tmp_path).__class__(**{**load_reader_profile("wilson", tmp_path).__dict__, "settings": {"version": 1}}), KNOWN)
    assert untouched.packs == ("urs",) and untouched.briefs["urs"]["maxFindings"] == 8 and untouched.aperture == "MATERIAL_APERTURE"


def _signal(**changes) -> dict:
    return {"signal_id": "SIG-1", "title": "Taiwan tightens export control on HBM", "text": "", "url": "https://www.csis.org/analysis/x", "publisher": "csis.org", "condition_ids": [], "themes": [], **changes}


def test_emphasis_moves_within_a_tier_and_never_hides(tmp_path: Path) -> None:
    _write(tmp_path)
    profile = apply(_profile(), load_reader_profile("wilson", tmp_path), KNOWN)
    up = profile_effect(_signal(), profile)
    assert up.affinity == CAP + 1 and not up.hidden, "three matches cap at +6, and the trusted outlet adds one"
    assert "profile v1, from N1" in up.reasons[0] and "export control" in up.reasons[0]
    named = profile_effect(_signal(title="Strait patrols", condition_ids=["TW-GLOBAL-03"], url="", publisher=""), profile)
    assert named.affinity == 2 and "Cross-strait military activity" in named.reasons[0]
    down = profile_effect(_signal(title="An ESG rating update", url="https://example-news.com/a", publisher=""), profile)
    assert down.affinity == -12 and not down.hidden and "distrusts" in down.reasons[-1]
    assert profile_effect(_signal(title="An esg aside", url="", publisher=""), profile).affinity == 0, "an acronym in capitals matches only in capitals"
    assert profile_effect(_signal(title="New export controls on HBMs", url="", publisher=""), profile).affinity == 4, "plurals match"
    assert profile_effect(_signal(title="Exporters control costs", url="", publisher=""), profile).affinity == 0, "whole words only"
    protected = profile_effect(_signal(title="An ESG rating update", url="https://example-news.com/a", publisher=""), profile, protected=True)
    assert protected.affinity == 0 and "never moved down" in protected.reasons[-1]
    assert profile_effect(_signal(), _profile()).affinity == 0, "no reader profile, no effect"


def test_rejections_count_only_with_signal_buttons_and_still_hide_nothing(tmp_path: Path) -> None:
    from grit.profiles.memory import Memory

    memory = Memory("wilson", {}, {}, {"SIG-1": "REJECT"})
    _write(tmp_path, GOOD.replace("aperture: WIDE", "aperture: WIDE\nsignal_buttons: true"))
    profile = apply(_profile(), load_reader_profile("wilson", tmp_path), KNOWN)
    plain = profile_effect(_signal(title="Unrelated", url="", publisher=""), profile)
    rejected = profile_effect(_signal(title="Unrelated", url="", publisher=""), profile, memory=memory)
    assert rejected.affinity < plain.affinity and not rejected.hidden
    assert any("Nothing is hidden" in reason for reason in rejected.reasons)
    off = apply(_profile(), load_reader_profile("wilson", tmp_path).__class__(**{**load_reader_profile("wilson", tmp_path).__dict__, "settings": {"version": 1}}), KNOWN)
    assert profile_effect(_signal(title="Unrelated", url="", publisher=""), off, memory=memory).affinity == 0


def test_a_broken_reader_profile_stops_the_load_and_names_the_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRIT_READERS_DIR", str(tmp_path))
    _write(tmp_path, GOOD.replace("packs: [all]", "packs: [mars]"))
    with pytest.raises(ValueError, match=r"wilson/profile\.md has \d+ problem"):
        load_profiles()


def test_a_good_reader_profile_is_applied_on_load(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRIT_READERS_DIR", str(tmp_path))
    _write(tmp_path, GOOD.replace("conditions: [TW-GLOBAL-03]", "conditions: []").replace("add_sections: [RESPONSE_OPTIONS]", "add_sections: []"))
    profiles = load_profiles()
    assert profiles["wilson"].reader is not None and profiles["wilson"].aperture == "WIDE_APERTURE"
    assert all(profile.reader is None for name, profile in profiles.items() if name != "wilson")


def test_the_profile_shapes_a_real_brief(tmp_path: Path, monkeypatch) -> None:
    from test_reader_scan import _scanned

    from grit.report.brief import build_brief, routed_signals
    from grit.report.markdown import render_markdown

    pool, run = _scanned(tmp_path, monkeypatch)
    before = render_markdown(build_brief(pool, run.run_id, load_profiles()["wilson"]))
    assert "draft for review" in before, "the review label shows by default"
    readers = tmp_path / "readers"
    monkeypatch.setenv("GRIT_READERS_DIR", str(readers))
    _write(readers, "---\nprofile: wilson\nversion: 1\nbased_on: [N1]\nbrief:\n  show_review_status: false\nemphasis:\n  topics: [PLA]\n---\n## Wants\nPLA activity first (N1).\n")
    wilson = load_profiles()["wilson"]
    brief = build_brief(pool, run.run_id, wilson)
    assert "draft for review" not in render_markdown(brief)
    assert brief["method"]["profile"].startswith("Reader profile v1") and brief["method"]["moved_by_profile"] == 1
    routed = {row["title"][:12]: row for row in routed_signals(pool, run.run_id, wilson)}
    assert routed["Taiwan repor"]["preference"]["affinity"] == 2 and all(row["shown"] for row in routed.values())
