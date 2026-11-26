#!/usr/bin/env python3
"""Build Phase E identity-eval fixtures from local weekly run artifacts.

Reads Wilson briefs under output/runs/ and (when present) pool assessments /
signal text. Does not change pipeline merge behaviour.

Usage (from repo root):
  .venv/bin/python tools/build_identity_eval_fixtures.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT = ROOT / "tests" / "fixtures" / "identity_eval"
RUNS = ROOT / "output" / "runs"
POOL_DB = ROOT / "output" / "grit.sqlite"

# Weeks required by plan v2 Phase E. No 2026-11-25 run exists on this machine;
# 2026-11-10-ste is the local Group week whose elevated mix matches the PDF narrative.
WEEKS: list[dict[str, str]] = [
    {
        "week_id": "2026-11-10-ste",
        "label": "Group (STE) — local proxy for 25 Nov 2026 Strategic Radar PDF",
        "role": "group-25nov-proxy",
        "pack": "ste",
    },
    {
        "week_id": "2026-11-02-urs-tw",
        "label": "URS-Taiwan",
        "role": "urs-taiwan",
        "pack": "urs-tw",
    },
    {
        "week_id": "2026-11-06-marine",
        "label": "Marine",
        "role": "marine",
        "pack": "marine",
    },
    {
        "week_id": "2026-11-05-ste",
        "label": "Group (STE) reasoned week",
        "role": "group",
        "pack": "ste",
    },
]

NEAR_SCORE_MARGIN = 2  # score in [ELEVATED_SCORE - margin, ELEVATED_SCORE)
TEXT_CAP = 400
HARD_PAIR_TARGET = 300


def _wilson_brief(week_id: str) -> Path:
    matches = sorted((RUNS / week_id).rglob("*wilson*.brief.json"))
    if not matches:
        raise FileNotFoundError(f"no wilson brief for {week_id}")
    return matches[0]


def _compact_signal(row: dict[str, Any], *, pool_row: dict[str, Any] | None = None) -> dict[str, Any]:
    editorial = row.get("editorial") if isinstance(row.get("editorial"), dict) else {}
    text = ""
    lane = row.get("lane")
    if pool_row:
        text = str(pool_row.get("text") or "")[:TEXT_CAP]
        lane = lane or pool_row.get("lane")
        if not row.get("title"):
            row = {**row, "title": pool_row.get("title")}
    return {
        "signal_id": row["signal_id"],
        "title": row.get("title") or "",
        "text": text,
        "publisher": row.get("publisher") or "",
        "published_at": row.get("published_at") or "",
        "url": row.get("url") or "",
        "tier": row.get("tier"),
        "source_band": row.get("source_band") or ((pool_row or {}).get("source_band")),
        "rung": row.get("rung") or (pool_row or {}).get("rung"),
        "lane": lane or "",
        "red": row.get("red") if row.get("red") is not None else (pool_row or {}).get("red"),
        "red_status": row.get("red_status") or (pool_row or {}).get("red_status"),
        "editorial": {
            "decision": editorial.get("decision"),
            "source_ids": list(editorial.get("source_ids") or [])[:12],
            "immediate_threat": bool(editorial.get("immediate_threat")),
            "significance": editorial.get("significance"),
            "risk_ids": list(editorial.get("risk_ids") or [])[:8],
        },
    }


def _load_pool():
    if not POOL_DB.exists():
        return None
    from grit.pool import Pool

    return Pool(POOL_DB)


def _selection_rows(brief: dict[str, Any]) -> list[dict[str, Any]]:
    weekly = ((brief.get("method") or {}).get("weekly") or {})
    sels = weekly.get("selections") or []
    rows = []
    for item in sels:
        if not isinstance(item, dict) or not item.get("signal_id"):
            continue
        editorial = item.get("editorial") if isinstance(item.get("editorial"), dict) else {}
        decision = editorial.get("decision")
        if decision not in {"INCLUDE", "WATCH"}:
            continue
        rows.append(item)
    return rows


def _pool_assessments(pool, week_id: str) -> list[dict[str, Any]] | None:
    if pool is None:
        return None
    run = pool.run(week_id) or {}
    try:
        if (run.get("config") or {}).get("weekly_mode") == "reasoned":
            return pool.weekly_assessments(week_id)
        rows = pool.assessments(week_id, kept_only=True)
        return rows or None
    except Exception:
        return None


def _is_near(row: dict[str, Any], elevated_score: int) -> bool:
    from grit.pipeline.events import _elevated, significance_total

    if _elevated([row]):
        return False
    score = significance_total(row)
    if score is not None and score >= elevated_score - NEAR_SCORE_MARGIN:
        return True
    tier = row.get("tier")
    try:
        tier_n = int(tier) if tier is not None else 9
    except (TypeError, ValueError):
        tier_n = 9
    return tier_n <= 2


def _country_tokens(title: str) -> set[str]:
    from grit.core.countries import named

    return {c.lower() for c in named(title or "")}


def _hard_flags(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    from grit.core.headlines import alias_ids, distinctive_terms

    flags: list[str] = []
    ta, tb = str(a.get("title") or ""), str(b.get("title") or "")
    wa, wb = distinctive_terms(ta), distinctive_terms(tb)
    shared = wa & wb
    if shared:
        flags.append("shared_terms:" + ",".join(sorted(shared)[:4]))
    ca, cb = _country_tokens(ta), _country_tokens(tb)
    if ca & cb:
        flags.append("shared_country:" + ",".join(sorted(ca & cb)[:3]))
    if alias_ids(ta) & alias_ids(tb):
        flags.append("shared_alias")
    # Accent / Plus Ultra style names
    if re.search(r"[áéíóúñü]", ta + tb, re.I) or "plus ultra" in (ta + tb).lower():
        flags.append("accent_or_alias_surface")
    # Shared actor-ish token with different action verbs
    actors = {"houthi", "houthis", "saudi", "taiwan", "ofac", "nvidia", "bis", "china", "iran"}
    shared_actors = (wa | {t.lower() for t in ta.split()}) & (wb | {t.lower() for t in tb.split()}) & actors
    if shared_actors and not (len(shared) >= 2):
        flags.append("shared_actor_weak_overlap:" + ",".join(sorted(shared_actors)[:3]))
    return flags


def export_week(meta: dict[str, str], pool) -> dict[str, Any]:
    from grit.pipeline.events import ELEVATED_SCORE, _elevated, _group, significance_total
    from grit.pool import Pool

    week_id = meta["week_id"]
    brief_path = _wilson_brief(week_id)
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    esc = list((brief.get("v2") or {}).get("escalating") or [])

    pool_rows = _pool_assessments(pool, week_id)
    if pool_rows:
        source = "pool_weekly_or_assessments"
        base_rows = pool_rows
    else:
        source = "brief_method_weekly_selections"
        base_rows = _selection_rows(brief)

    by_id = {row["signal_id"]: row for row in base_rows if row.get("signal_id")}

    # Enrich from pool signals table when assessment rows are missing text/lane.
    signal_cache: dict[str, dict[str, Any]] = {}
    if pool is not None:
        for sid in list(by_id):
            hit = pool.one(
                "SELECT signal_id, title, text, publisher, published_at, url, lane FROM signals WHERE signal_id = ?",
                (sid,),
            )
            if hit:
                signal_cache[sid] = dict(hit)

    # Build elevated / near-elevated signal sets.
    elevated_ids: set[str] = set()
    near_ids: set[str] = set()
    for sid, row in by_id.items():
        if _elevated([row]):
            elevated_ids.add(sid)
        elif _is_near(row, ELEVATED_SCORE):
            near_ids.add(sid)

    # Always include escalating event members even if score path missed them.
    event_exports: list[dict[str, Any]] = []
    for item in esc:
        signal_ids = [str(x) for x in (item.get("signal_ids") or []) if x]
        lead = str(item.get("lead") or "") or (signal_ids[0] if signal_ids else "")
        members = []
        for sid in signal_ids:
            raw = by_id.get(sid) or {"signal_id": sid, "title": "", "editorial": {}}
            members.append(_compact_signal(raw, pool_row=signal_cache.get(sid)))
            elevated_ids.add(sid)
        event_exports.append(
            {
                "week_id": week_id,
                "event_id": item.get("event_id"),
                "band": item.get("band"),
                "score": item.get("score"),
                "tier": item.get("tier"),
                "item": item.get("item") or "",
                "lead": lead,
                "elevated": True,
                "near_elevated": False,
                "member_signal_ids": signal_ids,
                "members": members,
            }
        )

    # Near-elevated singleton events (not already listed as escalating members).
    for sid in sorted(near_ids - elevated_ids):
        raw = by_id[sid]
        compact = _compact_signal(raw, pool_row=signal_cache.get(sid))
        event_exports.append(
            {
                "week_id": week_id,
                "event_id": f"NEAR-{sid[4:]}" if sid.startswith("SIG-") else f"NEAR-{sid}",
                "band": "=",
                "score": significance_total(raw),
                "tier": raw.get("tier"),
                "item": compact["title"],
                "lead": sid,
                "elevated": False,
                "near_elevated": True,
                "member_signal_ids": [sid],
                "members": [compact],
            }
        )

    # Signal catalog for this week (elevated + near only).
    wanted = elevated_ids | near_ids
    signals = []
    for sid in sorted(wanted):
        raw = by_id.get(sid) or {"signal_id": sid, "title": "", "editorial": {}}
        signals.append(
            {
                **_compact_signal(raw, pool_row=signal_cache.get(sid)),
                "week_id": week_id,
                "class": "elevated" if sid in elevated_ids else "near_elevated",
            }
        )

    # Offline merge snapshot inputs: rows shaped for _group / _merge_stories.
    merge_rows = []
    for sid in sorted(wanted):
        raw = by_id.get(sid)
        if not raw:
            continue
        merge_rows.append(_compact_signal(raw, pool_row=signal_cache.get(sid)))

    # Reproduce current clustering offline for diagnostics (not authoritative labels).
    tmp_pool = Pool(":memory:")
    groups = _group(tmp_pool, merge_rows, same_pairs=[]) if merge_rows else {}

    week_dir = OUT / "weeks" / week_id
    week_dir.mkdir(parents=True, exist_ok=True)
    (week_dir / "events.jsonl").write_text(
        "".join(json.dumps(ev, ensure_ascii=False) + "\n" for ev in event_exports),
        encoding="utf-8",
    )
    (week_dir / "signals.jsonl").write_text(
        "".join(json.dumps(sig, ensure_ascii=False) + "\n" for sig in signals),
        encoding="utf-8",
    )
    (week_dir / "manifest.json").write_text(
        json.dumps(
            {
                **meta,
                "brief_path": str(brief_path.relative_to(ROOT)),
                "source": source,
                "escalating_events": len(esc),
                "elevated_signals": len(elevated_ids),
                "near_elevated_signals": len(near_ids - elevated_ids),
                "exported_signals": len(signals),
                "offline_group_clusters": len(groups),
                "note_25nov": (
                    "No local run_id 2026-11-25-*; PDF '25 November 2026 ST Engineering Strategic Radar' "
                    "is represented by this week when role=group-25nov-proxy."
                    if meta.get("role") == "group-25nov-proxy"
                    else None
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "meta": meta,
        "events": event_exports,
        "signals": signals,
        "groups": {eid: [r["signal_id"] for r in members] for eid, members in groups.items()},
        "brief_items": [{"event_id": e.get("event_id"), "item": e.get("item") or ""} for e in esc],
    }


def generate_pairs(week_exports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidate pairs for R&A labelling. Labels are blank / provisional only."""
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(week_id: str, a: dict[str, Any], b: dict[str, Any], *, reason: str, provisional: str | None = None):
        sa, sb = sorted([a["signal_id"], b["signal_id"]])
        key = (week_id, sa, sb)
        if sa == sb or key in seen:
            return
        seen.add(key)
        flags = _hard_flags(a if a["signal_id"] == sa else b, b if b["signal_id"] == sb else a)
        # Prefer the dicts in sorted id order
        left = a if a["signal_id"] == sa else b
        right = b if b["signal_id"] == sb else a
        row = {
            "week_id": week_id,
            "signal_a": sa,
            "signal_b": sb,
            "title_a": left.get("title") or "",
            "title_b": right.get("title") or "",
            "label": provisional or "",
            "label_status": "provisional" if provisional else "unlabelled",
            "label_source": "export_heuristic" if provisional else "needs_ra",
            "hard_flags": flags,
            "pair_reason": reason,
            "authoritative": False,
        }
        pairs.append(row)

    for exported in week_exports:
        week_id = exported["meta"]["week_id"]
        # Map signal -> reported escalating event (if any)
        signal_event: dict[str, str] = {}
        for ev in exported["events"]:
            if not ev.get("elevated"):
                continue
            for sid in ev.get("member_signal_ids") or []:
                signal_event[sid] = ev["event_id"]

        by_id = {s["signal_id"]: s for s in exported["signals"]}

        # 1) Co-members of an exported elevated event → provisional SAME (export co-membership only)
        for ev in exported["events"]:
            if not ev.get("elevated"):
                continue
            members = [by_id[s] for s in ev.get("member_signal_ids") or [] if s in by_id]
            for a, b in combinations(members, 2):
                add(week_id, a, b, reason="co_member_elevated_export", provisional="SAME")

        # 2) Cross-event hard negatives: shared country / weak actor overlap
        elevated_events = [ev for ev in exported["events"] if ev.get("elevated") and ev.get("members")]
        for ev_a, ev_b in combinations(elevated_events, 2):
            for a in ev_a["members"][:4]:
                for b in ev_b["members"][:4]:
                    flags = _hard_flags(a, b)
                    if not flags:
                        continue
                    add(week_id, a, b, reason="cross_event_hard", provisional="UNRELATED")

        # 3) Near-elevated vs elevated with shared terms
        near = [s for s in exported["signals"] if s.get("class") == "near_elevated"]
        elev = [s for s in exported["signals"] if s.get("class") == "elevated"]
        for n in near[:40]:
            scored = []
            for e in elev:
                flags = _hard_flags(n, e)
                if flags:
                    scored.append((len(flags), e, flags))
            scored.sort(key=lambda x: -x[0])
            for _, e, _flags in scored[:3]:
                add(week_id, n, e, reason="near_vs_elevated_hard")

        # 4) Within offline _group clusters that are NOT the same brief elevated event → hard / ambiguous
        for _eid, members in (exported.get("groups") or {}).items():
            if len(members) < 2:
                continue
            brief_eids = {signal_event.get(sid) for sid in members}
            if len(brief_eids) <= 1 and None not in brief_eids:
                continue
            for sa, sb in combinations(members[:8], 2):
                if sa not in by_id or sb not in by_id:
                    continue
                add(week_id, by_id[sa], by_id[sb], reason="offline_group_cross_brief_event")

    # Prefer hard pairs first, then fill to target
    hard = [p for p in pairs if p.get("hard_flags")]
    easy = [p for p in pairs if not p.get("hard_flags")]
    ordered = hard + easy
    if len(ordered) < HARD_PAIR_TARGET:
        # Extra random-ish pairs within week elevated pool
        for exported in week_exports:
            week_id = exported["meta"]["week_id"]
            elev = [s for s in exported["signals"] if s.get("class") == "elevated"]
            for a, b in combinations(elev, 2):
                add(week_id, a, b, reason="elevated_pool_fill")
                if len(seen) >= HARD_PAIR_TARGET + 50:
                    break
            if len(seen) >= HARD_PAIR_TARGET + 50:
                break
        hard = [p for p in pairs if p.get("hard_flags")]
        easy = [p for p in pairs if not p.get("hard_flags")]
        ordered = hard + easy

    # Cap enormous sets but keep ≥300 and over-sample hard
    if len(ordered) > 600:
        hard = [p for p in ordered if p.get("hard_flags")]
        rest = [p for p in ordered if not p.get("hard_flags")]
        ordered = hard[:450] + rest[:150]
    return ordered


def write_pairs(pairs: list[dict[str, Any]]) -> None:
    # pairs.jsonl: plan shape {signal_a, signal_b, label} plus metadata for the harness
    path = OUT / "pairs.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for p in pairs:
            fh.write(
                json.dumps(
                    {
                        "signal_a": p["signal_a"],
                        "signal_b": p["signal_b"],
                        "label": p["label"] or None,
                        "week_id": p["week_id"],
                        "label_status": p["label_status"],
                        "label_source": p["label_source"],
                        "authoritative": False,
                        "hard_flags": p.get("hard_flags") or [],
                        "pair_reason": p.get("pair_reason"),
                        "title_a": p.get("title_a"),
                        "title_b": p.get("title_b"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def write_labelling_sheet(pairs: list[dict[str, Any]]) -> None:
    path = OUT / "labelling_sheet.csv"
    fields = [
        "pair_id",
        "week_id",
        "signal_a",
        "signal_b",
        "title_a",
        "title_b",
        "hard_flags",
        "pair_reason",
        "provisional_label",
        "label_status",
        "ra_label",
        "ra_notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for i, p in enumerate(pairs, start=1):
            writer.writerow(
                {
                    "pair_id": f"P-{i:04d}",
                    "week_id": p["week_id"],
                    "signal_a": p["signal_a"],
                    "signal_b": p["signal_b"],
                    "title_a": p.get("title_a") or "",
                    "title_b": p.get("title_b") or "",
                    "hard_flags": "|".join(p.get("hard_flags") or []),
                    "pair_reason": p.get("pair_reason") or "",
                    "provisional_label": p.get("label") or "",
                    "label_status": p.get("label_status") or "unlabelled",
                    "ra_label": "",  # Jony fills: SAME | RELATED | UNRELATED
                    "ra_notes": "",
                }
            )


def write_topics(week_exports: list[dict[str, Any]]) -> None:
    """Expected topic groupings. PDF-backed for the 25 Nov proxy week; gaps marked for Jony."""
    rows: list[dict[str, Any]] = []
    pdf_topics = {
        "2026-11-10-ste": {
            "status": "skeleton_from_pdf",
            "reference": "internal/topic-collapse/grit-scan-topic-collapse-recommendation.pdf",
            "topics": [
                {
                    "topic_key": "middle-east-security-escalation",
                    "label": "Middle East security escalation",
                    "expected_item_substrings": [
                        "Houthi",
                        "Riyadh",
                        "Gulf of Oman",
                        "Red Sea",
                        "UAE",
                    ],
                    "notes": "PDF: Riyadh attack / regional security alerts / Israel–Iran warning. Map event_ids after R&A review.",
                    "event_ids": [],
                    "gap_for_jony": True,
                },
                {
                    "topic_key": "us-advanced-compute-controls",
                    "label": "US advanced-compute controls",
                    "expected_item_substrings": ["Nvidia", "export control", "chip", "BIS"],
                    "notes": "PDF: BIS remote-access / Entity List / licensing / Malaysia enforcement.",
                    "event_ids": [],
                    "gap_for_jony": True,
                },
                {
                    "topic_key": "ofac-sanctions",
                    "label": "OFAC designations / secondary sanctions",
                    "expected_item_substrings": ["OFAC", "SDN", "sanctions"],
                    "notes": "PDF: keep OFAC separate from Middle East security and compute-control topics.",
                    "event_ids": [],
                    "gap_for_jony": True,
                },
                {
                    "topic_key": "other-distinct",
                    "label": "Other distinct elevated (cyber / naval / India AI — confirm with R&A)",
                    "expected_item_substrings": ["PHP2Shell", "Hanwha", "India"],
                    "notes": "PDF: Saudi operating-policy and standing conditions stay separate when present; confirm which elevated rows are unassigned.",
                    "event_ids": [],
                    "gap_for_jony": True,
                },
            ],
            "unassigned_guidance": "OFAC, Saudi operating-policy changes, standing strategic conditions stay separate unless basis rule permits.",
        }
    }

    for exported in week_exports:
        week_id = exported["meta"]["week_id"]
        esc_ids = [e["event_id"] for e in exported["events"] if e.get("elevated")]
        preset = pdf_topics.get(week_id)
        if preset:
            # Best-effort fill event_ids from substring match on exported items
            topics = []
            claimed: set[str] = set()
            for topic in preset["topics"]:
                matched = []
                for ev in exported["events"]:
                    if not ev.get("elevated"):
                        continue
                    item = ev.get("item") or ""
                    if any(s.lower() in item.lower() for s in topic["expected_item_substrings"]):
                        matched.append(ev["event_id"])
                        claimed.add(ev["event_id"])
                topics.append({**topic, "event_ids": matched})
            unassigned = [eid for eid in esc_ids if eid not in claimed]
            rows.append(
                {
                    "week_id": week_id,
                    "role": exported["meta"].get("role"),
                    "status": preset["status"],
                    "reference": preset["reference"],
                    "topics": topics,
                    "unassigned_event_ids": unassigned,
                    "unassigned_guidance": preset["unassigned_guidance"],
                    "all_elevated_event_ids": esc_ids,
                    "gap_for_jony": True,
                    "notes": "Substring pre-fill is provisional; Jony confirms R&A topic membership.",
                }
            )
        else:
            rows.append(
                {
                    "week_id": week_id,
                    "role": exported["meta"].get("role"),
                    "status": "skeleton_gap",
                    "reference": "Wilson brief escalating rows; awaiting R&A topic judgement",
                    "topics": [
                        {
                            "topic_key": "TBD",
                            "label": "GAP — Jony to group",
                            "event_ids": [],
                            "candidate_elevated": [
                                {"event_id": e["event_id"], "item": e.get("item") or ""}
                                for e in exported["events"]
                                if e.get("elevated")
                            ],
                            "gap_for_jony": True,
                        }
                    ],
                    "unassigned_event_ids": esc_ids,
                    "all_elevated_event_ids": esc_ids,
                    "gap_for_jony": True,
                    "notes": "No Wilson topic roll-up available for this week; fill from report reading.",
                }
            )

    (OUT / "topics.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_readme(week_exports: list[dict[str, Any]], pair_count: int) -> None:
    lines = [
        "# Identity evaluation fixtures (Phase E)",
        "",
        "Built for plan v2 Phase E. **No pipeline merge behaviour is changed by these fixtures.**",
        "",
        "## Weeks exported",
        "",
    ]
    for exported in week_exports:
        m = exported["meta"]
        lines.append(
            f"- `{m['week_id']}` — {m['label']} (role={m.get('role')}; "
            f"{sum(1 for e in exported['events'] if e.get('elevated'))} elevated events, "
            f"{len(exported['signals'])} signals)"
        )
    lines += [
        "",
        "## 25 Nov 2026",
        "",
        "No `2026-11-25-*` weekly run is present under `output/runs/` or in the local pool on this machine.",
        "The architecture PDF is dated 25 November 2026; the closest matching Group week on disk is",
        "`2026-11-10-ste` (seven elevated rows including Houthi/Riyadh, chip/export-control, and OFAC).",
        "It is filed under role `group-25nov-proxy`.",
        "",
        "## Files",
        "",
        "- `weeks/<week_id>/events.jsonl` — elevated + near-elevated events with member signals",
        "- `weeks/<week_id>/signals.jsonl` — compact signal payloads for the eval harness",
        "- `weeks/<week_id>/manifest.json` — provenance",
        "- `pairs.jsonl` — candidate pairs (`signal_a`, `signal_b`, `label`); labels are empty or **provisional** only",
        "- `labelling_sheet.csv` — sheet for Jony (fill `ra_label`: SAME | RELATED | UNRELATED)",
        "- `topics.jsonl` — expected topic groupings (PDF skeleton + gaps)",
        "",
        f"Candidate pairs in this build: **{pair_count}**.",
        "",
        "## Labelling",
        "",
        "Provisional labels from export co-membership / cross-event heuristics are **not** R&A-authoritative.",
        "Only `ra_label` on the CSV (copied into `pairs.jsonl` after review) counts as ground truth.",
        "",
    ]
    (OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pool = _load_pool()
    week_exports = [export_week(meta, pool) for meta in WEEKS]
    pairs = generate_pairs(week_exports)
    write_pairs(pairs)
    write_labelling_sheet(pairs)
    write_topics(week_exports)
    write_readme(week_exports, len(pairs))
    summary = {
        "weeks": [e["meta"]["week_id"] for e in week_exports],
        "pair_count": len(pairs),
        "hard_pairs": sum(1 for p in pairs if p.get("hard_flags")),
        "provisional_labels": sum(1 for p in pairs if p.get("label")),
        "unlabelled": sum(1 for p in pairs if not p.get("label")),
    }
    (OUT / "build_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
