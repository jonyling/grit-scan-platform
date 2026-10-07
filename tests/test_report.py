import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from grit.report.digest import main
from grit.report.digest_writer import MAX_SNIPPET_CHARS, ReportError, build_payload, model_view, render, request_draft, sanitize, validate


def _signal(url, title, *, lob=None, level="LOB", names=(), types=("LOB_KEYWORD",), evidence=("Smart Metro Systems",), text="Body text 2033.", also=()):
    return {
        "url": url, "title": title, "publisher": url.split("/")[2], "published_at": "2026-10-05T10:00:00Z", "text": text,
        "canonical_lob": lob, "link_level": level, "entity_names": list(names), "match_types": list(types), "evidence_spans": list(evidence),
        "best_anchors": [{"anchor": "Hospital Logistics Automation (Healthcare Robotics & Automation)"}, {"anchor": "Smart Hospitals (Healthcare Robotics & Automation)"}],
        "also_reported_by": [{"url": u, "publisher": u.split("/")[2]} for u in also],
    }


def _signals():
    return {
        "runDate": "2026-10-06",
        "summary": {"candidates": 360, "keep_bound": 2, "keep_unbound": 1, "dropped": {"NOT_TOP_IN_QUERY": 211}},
        "config": {"window_days": 8, "per_query_top": 5, "near_duplicate": 0.95, "unbound_cap": 10, "priority": {"outlets_needed": 3, "min_cues": 1, "same_event": 0.9}},
        "bound": [
            _signal("https://a.com/x", "Siemens wins an order", level="ENTITY", names=("SIEMENS",), types=("REGISTER_EXACT",), evidence=("SIEMENS",), text="Siemens won a rail order."),
            _signal("https://b.com/y", "Smart Metro Systems extend a line", lob="Mobility Rail", also=("https://c.com/z",), text="x" * (MAX_SNIPPET_CHARS + 300)),
        ],
        "unbound": [_signal("https://d.com/w", "Hospital robots arrive", level=None, types=(), evidence=())],
    }


def _draft(payload):
    ids = [item["signal_id"] for item in payload["signals"]]
    return {
        "overview": "Rail, hospital robotics, and a customer order. 2 linked and 1 not linked.",
        "lob_themes": [{"lob": "Mobility Rail", "theme": "A line extension is reported.", "signal_ids": [ids[1]]}],
        "summaries": [{"signal_id": sid, "summary": f"Summary of {sid}."} for sid in ids],
    }


def test_the_payload_orders_signals_labels_groups_and_hides_urls_from_the_model() -> None:
    payload = build_payload(_signals())
    ids = [item["signal_id"] for item in payload["signals"]]
    assert ids == ["S01", "S02", "S03"] and [item["lob"] for item in payload["signals"]] == ["Linked by name", "Mobility Rail", "No STE link established"]
    assert payload["counts"] == {"candidates": 360, "linked": 2, "not_linked": 1, "in_cards": 0}
    assert len(payload["signals"][1]["text"]) == MAX_SNIPPET_CHARS and payload["signals"][1]["outlets"] == ["b.com", "c.com"]
    assert payload["signals"][2]["nearest_topics"][0].startswith("Hospital Logistics") and "link_basis" not in payload["signals"][2]
    focus = {"anchor": "Robotics scale-up. Robots must move from pilots to profitable deployments.", "kind": "MANAGEMENT_FOCUS"}
    from grit.report.digest_writer import _topic
    assert _topic(focus) == "Robotics scale-up" and _topic({"anchor": "Mobility Rail: Smart Metro; Rail", "kind": "LOB_SCOPE"}) == "Mobility Rail"
    assert "http" not in json.dumps(model_view(payload)) and payload["signals"][0]["url"] == "https://a.com/x"


def test_a_good_draft_validates_and_renders_with_links_and_the_reason_from_the_signals() -> None:
    payload = build_payload(_signals())
    draft = _draft(payload)
    assert validate(draft, payload) == []
    text = render(draft, payload, meta={"model": "m", "prompt_version": "p6-report-3", "method": ["- Method line."]})
    assert "Draft for review. Not approved." in text and "Source: https://a.com/x" in text and "Linked to the STE register by name: SIEMENS." in text
    assert 'Linked to the Mobility Rail line of business by keyword "Smart Metro Systems".' in text and "Also carried by: c.com." in text
    assert text.index("### Linked by name") < text.index("### Mobility Rail") < text.index("## No STE link established")
    assert "Nearest ontology topic: Hospital Logistics Automation" in text and "A line extension is reported." in text and "- Method line." in text


def test_validation_rejects_each_kind_of_bad_draft() -> None:
    payload = build_payload(_signals())
    cases = {
        "UNKNOWN_SIGNAL": lambda d: d["summaries"].append({"signal_id": "S99", "summary": "x"}),
        "MISSING_SUMMARY": lambda d: d["summaries"].pop(),
        "DUPLICATE_SUMMARY": lambda d: d["summaries"].append(dict(d["summaries"][0])),
        "UNGROUNDED_NUMBER": lambda d: d["summaries"][0].update(summary="Orders rose 12 percent."),
        "URL_IN_TEXT": lambda d: d["summaries"][0].update(summary="See https://x.com for more."),
        "BANNED_TERM": lambda d: d["summaries"][0].update(summary="The severity is high."),
        "SUMMARY_TOO_LONG": lambda d: d["summaries"][0].update(summary="word " * 61),
        "THEME_LOB_MISMATCH": lambda d: d["lob_themes"][0].update(signal_ids=["S01"]),
        "BAD_SHAPE": lambda d: d.pop("overview"),
    }
    for expected, mutate in cases.items():
        draft = _draft(payload)
        mutate(draft)
        assert any(item.startswith(expected) for item in validate(draft, payload)), expected
    grounded = _draft(payload)
    grounded["summaries"][2]["summary"] = "Robots arrive; the source text mentions 2033, as S03 shows."
    assert validate(grounded, payload) == [], "a number that the source text contains is allowed"
    unlinked_theme = _draft(payload)
    unlinked_theme["lob_themes"].append({"lob": "No STE link established", "theme": "Hospital robotics.", "signal_ids": ["S03"]})
    assert validate(unlinked_theme, payload) == [], "a theme for the unlinked group is allowed"
    unlinked_theme["lob_themes"].append({"lob": "Mobility Rail", "theme": "x", "signal_ids": ["S03"]})
    assert any(item.startswith("THEME_LOB_MISMATCH") for item in validate(unlinked_theme, payload))


def _client(text=None, error=None):
    seen = {}

    def generate_content(**kwargs):
        seen.update(kwargs)
        if error:
            raise error
        return SimpleNamespace(text=text)

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), seen


def test_the_request_parses_fenced_json_sends_no_url_and_never_leaks() -> None:
    payload = build_payload(_signals())
    client, seen = _client("```json\n" + json.dumps(_draft(payload)) + "\n```")
    assert request_draft(client, payload, model="m")["overview"].startswith("Rail")
    assert "http" not in seen["contents"] and seen["config"]["response_mime_type"] == "application/json" and seen["config"]["temperature"] == 0.1
    for client, expected in ((_client(error=RuntimeError('quota {"key": "secret-value"}'))[0], "quota"), (_client("not json")[0], "did not return JSON"), (_client("[1]")[0], "JSON object")):
        try:
            request_draft(client, payload, model="m")
        except ReportError as error:
            assert expected in str(error) and "secret-value" not in str(error)
        else:
            raise AssertionError("A bad answer must raise")


def test_the_command_writes_a_report_or_a_rejection_and_a_dry_run_sends_nothing() -> None:
    payload = build_payload(_signals())
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        assert main(["--run", str(folder)]) == 1, "no signals.json"
        (folder / "signals.json").write_text(json.dumps(_signals()), encoding="utf-8")
        client, seen = _client(json.dumps(_draft(payload)))
        assert main(["--run", str(folder), "--dry-run"], client=client) == 0 and not seen and not (folder / "report.md").exists()
        assert main(["--run", str(folder), "--model", "test-model"], client=client) == 0 and seen["model"] == "test-model"
        report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        assert report["status"] == "DRAFT_FOR_REVIEW" and report["validation"]["accepted"] and len(report["response_hash"]) == 64
        assert report["input_signal_ids"] == ["S01", "S02", "S03"] and "Draft for review" in (folder / "report.md").read_text(encoding="utf-8")
        bad = copy.deepcopy(_draft(payload))
        bad["summaries"][0]["summary"] = "Orders rose 12 percent."
        # A figure the source lacks is withheld. The rest of the report is kept.
        assert main(["--run", str(folder)], client=_client(json.dumps(bad))[0]) == 0
        report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        text = (folder / "report.md").read_text(encoding="utf-8")
        assert report["validation"]["withheld"] == ["S01"] and "Summary withheld" in text and "Withheld by validation: S01" in text
        # A draft with no usable structure is rejected.
        assert main(["--run", str(folder)], client=_client(json.dumps({"overview": 1}))[0]) == 1
        rejected = json.loads((folder / "report.rejected.json").read_text(encoding="utf-8"))
        assert rejected["violations"][0].startswith("BAD_SHAPE")


def _sequence(*texts):
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=texts[min(len(calls), len(texts)) - 1])

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), calls


def test_a_rejected_draft_is_sent_back_once_with_its_violations_and_the_history_is_kept() -> None:
    payload = build_payload(_signals())
    bad = _draft(payload)
    bad["summaries"][0]["summary"] = "Orders rose 12 percent."
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        (folder / "signals.json").write_text(json.dumps(_signals()), encoding="utf-8")
        client, calls = _sequence(json.dumps(bad), json.dumps(_draft(payload)))
        assert main(["--run", str(folder)], client=client) == 0 and len(calls) == 2
        assert json.loads(calls[1]["contents"])["fix"][0].startswith("UNGROUNDED_NUMBER") and "fix" not in json.loads(calls[0]["contents"])
        report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        assert report["attempts"] == 2 and report["validation"]["earlier_attempts"][0][0].startswith("UNGROUNDED_NUMBER")
        stubborn, calls = _sequence(json.dumps(bad))
        assert main(["--run", str(folder), "--retries", "0"], client=stubborn) == 0 and len(calls) == 1
        client, calls = _sequence(json.dumps(bad))
        assert main(["--run", str(folder)], client=client) == 0 and len(calls) == 2
        report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        assert report["attempts"] == 2 and report["validation"]["withheld"] == ["S01"] and len(report["validation"]["earlier_attempts"]) == 2


def test_sanitize_withholds_only_the_failing_parts_and_the_render_says_so() -> None:
    payload = build_payload(_signals())
    draft = _draft(payload)
    draft["overview"] = "Orders rose 55 percent."
    draft["summaries"][0]["summary"] = "See https://x.com."
    draft["lob_themes"].append({"lob": "Mobility Rail", "theme": "Ok.", "signal_ids": ["S01"]})
    clean, withheld = sanitize(draft, payload)
    assert withheld == ["overview", "S01", "theme: Mobility Rail"] and clean["overview"] == ""
    assert [entry["signal_id"] for entry in clean["summaries"]] == ["S02", "S03"] and len(clean["lob_themes"]) == 1
    assert validate({**clean, "summaries": clean["summaries"]}, payload) == ["MISSING_SUMMARY: S01"]
    text = render(clean, payload, meta={"model": "m", "prompt_version": "p", "method": [], "withheld": withheld})
    assert "No overview passed validation." in text and text.count("Summary withheld") == 1 and "Withheld by validation: overview, S01" in text


def test_a_signal_with_a_finding_card_is_left_out_of_the_summary_pass_and_a_theme_level_signal_is_labelled() -> None:
    signals = _signals()
    signals["bound"].append(_signal("https://e.com/t", "Rule change hits suppliers", level="THEME", types=(), evidence=()))
    payload = build_payload(signals, {"https://a.com/x"})
    assert [item["title"] for item in payload["signals"]] == ["Smart Metro Systems extend a line", "Rule change hits suppliers", "Hospital robots arrive"]
    assert [item["signal_id"] for item in payload["signals"]] == ["S01", "S02", "S03"]
    assert payload["counts"] == {"candidates": 360, "linked": 3, "not_linked": 1, "in_cards": 1}
    assert payload["signals"][1]["lob"] == "Theme-level" and "Big event with no register link" in render(_draft(payload), payload, meta={"model": "m", "prompt_version": "p", "method": []})


def test_an_undated_signal_says_the_date_is_not_stated_in_full() -> None:
    signals = _signals()
    signals["bound"][0]["published_at"] = None
    payload = build_payload(signals)
    assert "(a.com, date not stated)" in render(_draft(payload), payload, meta={"model": "m", "prompt_version": "p", "method": []})


def test_the_report_places_the_finding_cards_before_the_other_signals() -> None:
    payload = build_payload(_signals(), {"https://a.com/x"})
    text = render(_draft(payload), payload, meta={"model": "m", "prompt_version": "p", "method": []}, findings_lines=["## Priority findings", "", "### F01. A card", ""])
    assert text.index("## Priority findings") < text.index("## Overview of the other signals") < text.index("## Other linked signals") < text.index("## No STE link established")
    assert "1 of the linked signals have a finding card." in text and "Siemens wins an order" not in text


def test_a_findings_file_from_an_unfinished_loop_is_refused_before_any_model_call() -> None:
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        (folder / "signals.json").write_text(json.dumps(_signals()), encoding="utf-8")
        (folder / "findings.json").write_text(json.dumps({"runDate": "2026-10-06", "status": "IN_PROGRESS", "findings": []}), encoding="utf-8")
        assert main(["--run", str(folder), "--dry-run"]) == 1
        (folder / "findings.json").write_text(json.dumps({"runDate": "2026-10-05", "status": "DRAFT_FOR_REVIEW", "findings": []}), encoding="utf-8")
        assert main(["--run", str(folder), "--dry-run"]) == 1  # another run date


def test_odd_model_output_is_a_violation_and_not_a_crash() -> None:
    payload = build_payload(_signals())
    ids = [item["signal_id"] for item in payload["signals"]]
    draft = _draft(payload)
    draft["lob_themes"] = [{"theme": "No label.", "signal_ids": []}, {"lob": "Mobility Rail", "theme": "Odd ids.", "signal_ids": [["S01"], {"a": 1}]}, "text"]
    draft["summaries"] = [{"signal_id": ["S01"], "summary": "x"}, {"signal_id": None, "summary": "y"}] + [{"signal_id": sid, "summary": f"Summary of {sid}."} for sid in ids]
    violations = validate(draft, payload)
    assert violations.count("BAD_THEME_SHAPE") == 3 and any(item.startswith("UNKNOWN_SIGNAL: ['S01']") for item in violations)
    clean, withheld = sanitize(draft, payload)  # must not raise
    assert clean["lob_themes"] == [] and [item["signal_id"] for item in clean["summaries"]] == ids and "theme: ?" in withheld[0] or "theme" in " ".join(withheld)
    assert render(clean, payload, meta={"model": "m", "prompt_version": "p", "method": [], "withheld": withheld})  # renders


def test_a_figure_must_be_a_whole_number_in_the_source_text() -> None:
    payload = build_payload(_signals())
    ids = [item["signal_id"] for item in payload["signals"]]
    draft = _draft(payload)
    # The first signal's text is "Siemens won a rail order." The third signal's text is "Body text 2033." Neither holds 20 or 203.
    draft["summaries"] = [{"signal_id": ids[0], "summary": "Siemens won 20 rail orders."}, {"signal_id": ids[1], "summary": "Plain."}, {"signal_id": ids[2], "summary": "The plan names 203."}]
    violations = validate(draft, payload)
    assert "UNGROUNDED_NUMBER: S01: 20" in violations and "UNGROUNDED_NUMBER: S03: 203" in violations
    draft["summaries"][2] = {"signal_id": ids[2], "summary": "The plan names 2033."}
    assert not any("S03" in item for item in validate(draft, payload))
