"""Phase E: offline identity-eval harness runs without network or merge-code changes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "identity_eval"


def test_identity_eval_fixtures_present():
    assert (FIXTURES / "pairs.jsonl").exists()
    assert (FIXTURES / "topics.jsonl").exists()
    assert (FIXTURES / "labelling_sheet.csv").exists()
    weeks = sorted(p.name for p in (FIXTURES / "weeks").iterdir() if p.is_dir())
    assert len(weeks) >= 4
    required_roles = {"group-25nov-proxy", "urs-taiwan", "marine", "group"}
    roles = set()
    for week in weeks:
        manifest = json.loads((FIXTURES / "weeks" / week / "manifest.json").read_text(encoding="utf-8"))
        roles.add(manifest.get("role"))
        assert (FIXTURES / "weeks" / week / "events.jsonl").exists()
        assert (FIXTURES / "weeks" / week / "signals.jsonl").exists()
    assert required_roles <= roles


def test_pairs_jsonl_has_at_least_300_candidates_and_marks_non_authoritative():
    lines = [ln for ln in (FIXTURES / "pairs.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= 300
    hard = 0
    for line in lines:
        row = json.loads(line)
        assert "signal_a" in row and "signal_b" in row
        assert row.get("authoritative") is False
        assert row.get("label_status") in {"unlabelled", "provisional"}
        if row.get("label"):
            assert row["label"] in {"SAME", "RELATED", "UNRELATED"}
            assert row.get("label_source") == "export_heuristic"
        if row.get("hard_flags"):
            hard += 1
    assert hard >= 100, "hard cases should be over-sampled"


def test_topics_jsonl_marks_gaps_and_covers_exported_weeks():
    rows = [json.loads(ln) for ln in (FIXTURES / "topics.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    week_ids = {row["week_id"] for row in rows}
    exported = {p.name for p in (FIXTURES / "weeks").iterdir() if p.is_dir()}
    assert exported <= week_ids
    assert any(row.get("gap_for_jony") for row in rows)
    proxy = next(row for row in rows if row["week_id"] == "2026-11-10-ste")
    assert proxy["status"] == "skeleton_from_pdf"
    assert len(proxy.get("topics") or []) >= 3


def test_eval_identity_runs_offline(tmp_path: Path):
    """Harness imports current merge helpers and returns headline metrics without touching events.py behaviour."""
    import importlib.util

    path = ROOT / "tools" / "eval_identity.py"
    spec = importlib.util.spec_from_file_location("eval_identity", path)
    assert spec and spec.loader
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    result = harness.evaluate(FIXTURES)
    assert result["weeks"]
    headline = result["headline"]
    assert "false_merge_rate_authoritative" in headline
    assert "missed_merge_rate_authoritative" in headline
    assert "max_cluster_size" in headline
    assert "max_distinct_country_count" in headline
    assert "topic_purity_mean" in headline
    assert "topic_completeness_mean" in headline
    assert headline["max_cluster_size"] >= 1
    # Authoritative rates stay null until Jony labels the sheet.
    assert headline["authoritative_pairs_considered"] == 0
    assert headline["baseline_status"] == "provisional_only"

    out = tmp_path / "report.txt"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "eval_identity.py"), "--fixtures", str(FIXTURES)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    out.write_text(completed.stdout, encoding="utf-8")
    assert "false-merge rate" in completed.stdout
    assert "max cluster size" in completed.stdout
