"""P3 ranking quality: challenge / override learning loop and score→band tuning proposals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grit.core.band_learning import (
    challenge_outcome_stats,
    format_learning_report,
    learning_report,
    override_hit_stats,
    propose_threshold_changes,
    tuning_signals,
)
from grit.core.challenges import (
    STATUS_ACCEPT_RULE_GAP,
    STATUS_DISMISS,
    STATUS_OPEN,
    STATUS_UPHOLD,
    open_from_editorial,
    resolve_challenge,
)
from grit.core.overrides import (
    BAND_ELEVATED,
    BAND_ESCALATING,
    BAND_WATCH,
    OVERRIDE_STAFF_THREAT,
    READER_BAND_VOCAB_NOTE,
    band_from_tier,
    clear_score_band_threshold_cache,
    load_score_band_thresholds,
    product_band_alias,
)
from grit.pool import Pool


def _run(pool: Pool, run_id: str = "2026-11-01-demo", pack: str = "demo") -> None:
    pool.insert(
        "runs",
        {
            "run_id": run_id,
            "run_date": "2026-11-01",
            "pack": pack,
            "kind": "READER_SCAN",
            "status": "COMPLETE",
            "started_at": "2026-11-01T00:00:00Z",
            "config_json": {},
        },
    )


def _signal(pool: Pool, signal_id: str) -> None:
    pool.insert(
        "signals",
        {
            "signal_id": signal_id,
            "url": f"https://example.com/{signal_id}",
            "title": f"Title {signal_id}",
            "publisher": "Test",
            "published_at": "2026-11-01",
            "lane": "MANUAL",
            "text": "Body.",
            "content_hash": signal_id,
            "first_seen_at": "2026-11-01T00:00:00Z",
            "last_seen_at": "2026-11-01T00:00:00Z",
        },
    )


def _assess(
    pool: Pool,
    *,
    signal_id: str,
    run_id: str = "2026-11-01-demo",
    pack: str = "demo",
    band: str = BAND_WATCH,
    tier: int = 4,
    override_rule_id: str | None = None,
) -> None:
    priority = {"band": band, "tier": tier}
    if override_rule_id:
        priority["override_rule_id"] = override_rule_id
        priority["override_reason"] = "fixture override"
    pool.insert(
        "assessments",
        {
            "signal_id": signal_id,
            "run_id": run_id,
            "pack": pack,
            "decision": "KEEP",
            "priority_json": priority,
            "tier": tier,
            "assessed_at": "2026-11-01T00:00:00Z",
        },
    )


def test_default_thresholds_match_p0_mapping() -> None:
    thresholds = load_score_band_thresholds()
    assert thresholds["tier"]["escalating_max"] == 1
    assert thresholds["tier"]["elevated_max"] == 3
    assert band_from_tier(1) == BAND_ESCALATING
    assert band_from_tier(2) == BAND_ELEVATED
    assert band_from_tier(4) == BAND_WATCH


def test_thresholds_file_can_raise_escalating_max(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grit import settings

    config = tmp_path / "config"
    config.mkdir()
    (config / "score-band-thresholds.json").write_text(
        json.dumps({"tier": {"escalating_max": 2, "elevated_max": 3}, "significance": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "CONFIG_DIR", config)
    clear_score_band_threshold_cache()
    try:
        assert band_from_tier(2) == BAND_ESCALATING
        assert band_from_tier(3) == BAND_ELEVATED
    finally:
        clear_score_band_threshold_cache()


def test_override_hit_rate_and_challenge_outcomes(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "pool.db")
    _run(pool)
    fixtures = (
        ("SIG-aaaaaaaaaaaaaaaa", BAND_ESCALATING, 1, OVERRIDE_STAFF_THREAT),
        ("SIG-bbbbbbbbbbbbbbbb", BAND_WATCH, 4, None),
        ("SIG-cccccccccccccccc", BAND_ELEVATED, 2, None),
        ("SIG-dddddddddddddddd", BAND_WATCH, 4, None),
    )
    for signal_id, band, tier, override in fixtures:
        _signal(pool, signal_id)
        _assess(
            pool,
            signal_id=signal_id,
            band=band,
            tier=tier,
            override_rule_id=override,
        )

    # Score-based Watch → Escalating accepted as rule gap (tuning signal).
    gap = open_from_editorial(
        pool,
        run_id="2026-11-01-demo",
        signal_id="SIG-bbbbbbbbbbbbbbbb",
        band=BAND_WATCH,
        challenged_band=BAND_ESCALATING,
        challenge_reason="Score cut too low; should be Escalating.",
        package_id="PKG-1",
        package_version=1,
    )
    resolve_challenge(
        pool, challenge_id=gap["challenge_id"], resolution=STATUS_ACCEPT_RULE_GAP, reviewer_id="wilson"
    )
    # Adjacent Elevated↔Watch upheld (defends cutoff).
    upheld = open_from_editorial(
        pool,
        run_id="2026-11-01-demo",
        signal_id="SIG-cccccccccccccccc",
        band=BAND_ELEVATED,
        challenged_band=BAND_WATCH,
        challenge_reason="Model noise.",
        package_id="PKG-1",
        package_version=1,
    )
    resolve_challenge(
        pool, challenge_id=upheld["challenge_id"], resolution=STATUS_UPHOLD, reviewer_id="wilson"
    )
    # Override challenge dismissed.
    dismissed = open_from_editorial(
        pool,
        run_id="2026-11-01-demo",
        signal_id="SIG-aaaaaaaaaaaaaaaa",
        band=BAND_ESCALATING,
        challenged_band=BAND_ELEVATED,
        challenge_reason="Override over-fire claim.",
        override_rule_id=OVERRIDE_STAFF_THREAT,
        package_id="PKG-1",
        package_version=1,
    )
    resolve_challenge(
        pool, challenge_id=dismissed["challenge_id"], resolution=STATUS_DISMISS, reviewer_id="wilson"
    )
    # One OPEN left.
    open_from_editorial(
        pool,
        run_id="2026-11-01-demo",
        signal_id="SIG-dddddddddddddddd",
        band=BAND_WATCH,
        challenged_band=BAND_ELEVATED,
        challenge_reason="Borderline.",
        package_id="PKG-1",
        package_version=1,
    )

    overrides = override_hit_stats(pool, pack="demo")
    assert overrides["assessments"] == 4
    assert overrides["override_hits"] == 1
    assert overrides["override_hit_rate"] == 0.25
    assert overrides["by_rule"][OVERRIDE_STAFF_THREAT] == 1

    report = learning_report(pool, pack="demo")
    assert report["apply"] is False
    assert report["challenge_rate"] == 1.0  # 4 challenges / 4 assessments
    outcomes = report["outcomes"]
    assert outcomes["by_status"][STATUS_ACCEPT_RULE_GAP] == 1
    assert outcomes["by_status"][STATUS_UPHOLD] == 1
    assert outcomes["by_status"][STATUS_DISMISS] == 1
    assert outcomes["by_status"][STATUS_OPEN] == 1
    assert outcomes["false_precision"]["adjacent_score_challenges"] >= 2
    assert report["rule_gap_misses"]
    assert report["rule_gap_misses"][0]["miss_id"]

    proposals = tuning_signals(pool.band_challenges_latest(pack="demo"))
    assert any(row.get("accept_rule_gap", 0) >= 1 for row in proposals)
    text = format_learning_report(report)
    assert "Challenge rate" in text
    assert "Override hit rate" in text
    assert "False-precision" in text

    proposal = propose_threshold_changes(report)
    assert proposal["apply"] is False
    assert "proposed_score_band_thresholds" in proposal
    assert proposal["proposed_score_band_thresholds"]["tier"]["escalating_max"] >= 1


def test_challenge_outcome_stats_empty() -> None:
    stats = challenge_outcome_stats([])
    assert stats["challenges"] == 0
    assert stats["uphold_rate"] is None
    assert stats["false_precision"]["adjacent_score_challenges"] == 0


def test_product_band_vocabulary_aliases() -> None:
    assert product_band_alias("Immediate Attention") == BAND_ESCALATING
    assert product_band_alias("Enhanced Monitoring") == BAND_ELEVATED
    assert product_band_alias("Watch") == BAND_WATCH
    assert product_band_alias("unknown label") is None
    assert "Escalating / Elevated / Watch" in READER_BAND_VOCAB_NOTE


def test_cli_report_and_propose_tuning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grit import cli

    pool = Pool(tmp_path / "pool.db")
    _run(pool)
    _signal(pool, "SIG-eeeeeeeeeeeeeeee")
    _assess(pool, signal_id="SIG-eeeeeeeeeeeeeeee", band=BAND_WATCH, tier=4)
    opened = open_from_editorial(
        pool,
        run_id="2026-11-01-demo",
        signal_id="SIG-eeeeeeeeeeeeeeee",
        band=BAND_WATCH,
        challenged_band=BAND_ELEVATED,
        challenge_reason="Should be Elevated.",
    )
    resolve_challenge(
        pool, challenge_id=opened["challenge_id"], resolution=STATUS_ACCEPT_RULE_GAP, reviewer_id="wilson"
    )

    monkeypatch.setattr(cli, "_pool", lambda: pool)
    out = tmp_path / "outside" / "tuning.json"
    ns = type(
        "NS",
        (),
        {
            "action": "propose-tuning",
            "pack": "demo",
            "run": None,
            "out": str(out),
            "json": False,
            "package": None,
            "version": None,
            "id": None,
            "profile": None,
            "note": None,
            "all": False,
        },
    )()
    assert cli.cmd_challenges(ns) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["apply"] is False
    assert "proposed_score_band_thresholds" in payload

    ns_report = type(
        "NS",
        (),
        {
            "action": "report",
            "pack": "demo",
            "run": None,
            "out": None,
            "json": True,
            "package": None,
            "version": None,
            "id": None,
            "profile": None,
            "note": None,
            "all": False,
        },
    )()
    assert cli.cmd_challenges(ns_report) == 0
