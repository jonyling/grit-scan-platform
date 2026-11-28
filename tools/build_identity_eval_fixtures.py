#!/usr/bin/env python3
"""Build Phase E identity-eval fixtures from Wilson briefs (+ optional local pool).

Authoritative weeks for this rebuild are the six Wilson DOCX briefs attached by R&A.
When `output/runs` + `output/grit.sqlite` exist they enrich signal text / near-elevated
rows; this cloud VM typically has neither, so the DOCX path is the primary source.

Does not change pipeline merge behaviour. Does not invent authoritative R&A pair labels.

Usage (from repo root):
  .venv/bin/python tools/build_identity_eval_fixtures.py
  WILSON_DOCX_DIR=/path/to/wilson-briefs .venv/bin/python tools/build_identity_eval_fixtures.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
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

DEFAULT_DOCX_DIRS = [
    Path(os.environ["WILSON_DOCX_DIR"]) if os.environ.get("WILSON_DOCX_DIR") else None,
    Path("/cursor/stores/bc-ad02b207-afda-408c-997d-5188616d063c/internal/topic-collapse/wilson-briefs"),
    ROOT / "tests" / "fixtures" / "identity_eval" / "wilson_briefs",
]

# Authoritative weeks from Jony's attached Wilson DOCX briefs (plan v2 Phase E refresh).
WEEKS: list[dict[str, str]] = [
    {
        "week_id": "2026-11-25-ste",
        "label": "Group (STE) — 25 Nov 2026 Strategic Radar (Wilson DOCX)",
        "role": "group",
        "pack": "ste",
        "docx_glob": "GRIT-Scan_ste_wilson_2026-11-25_*.docx",
    },
    {
        "week_id": "2026-11-26-urs",
        "label": "URS — 26 Nov 2026 (Wilson DOCX)",
        "role": "urs",
        "pack": "urs",
        "docx_glob": "GRIT-Scan_urs_wilson_2026-11-26_*.docx",
    },
    {
        "week_id": "2026-11-19-urs",
        "label": "URS — 19 Nov 2026 (Wilson DOCX)",
        "role": "urs",
        "pack": "urs",
        "docx_glob": "GRIT-Scan_urs_wilson_2026-11-19_*.docx",
    },
    {
        "week_id": "2026-11-25-marine",
        "label": "Marine — 25 Nov 2026 (Wilson DOCX)",
        "role": "marine",
        "pack": "marine",
        "docx_glob": "GRIT-Scan_marine_wilson_2026-11-25_*.docx",
    },
    {
        "week_id": "2026-11-19-marine",
        "label": "Marine — 19 Nov 2026 (Wilson DOCX)",
        "role": "marine",
        "pack": "marine",
        "docx_glob": "GRIT-Scan_marine_wilson_2026-11-19_*.docx",
    },
    {
        "week_id": "2026-11-24-ca",
        "label": "Commercial Aerospace — 24 Nov 2026 (Wilson DOCX)",
        "role": "ca",
        "pack": "ca",
        "docx_glob": "GRIT-Scan_ca_wilson_2026-11-24_*.docx",
    },
]

NEAR_SCORE_MARGIN = 2
TEXT_CAP = 400
HARD_PAIR_TARGET = 300

_SOURCE_RE = re.compile(
    r"^\[(S\d+)\]\s+([^,]+),\s+(.+?)(?:,\s*(\d{1,2}\s+\w+\s+\d{4}[^()]*)?)?(?:\s*\([^)]*\))?\s*(https?://\S+)?\s*$"
)
_SID_LIST_RE = re.compile(r"S\d+")
_MF_RE = re.compile(r"\[MF:\s*([^\]]+)\]", re.I)
_RISK_TAG_RE = re.compile(
    r"\[((?:Key|Tier-2|Emerging|Group)[^\]]*)\]",
    re.I,
)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _find_docx_dir() -> Path | None:
    for path in DEFAULT_DOCX_DIRS:
        if path and path.is_dir() and any(path.glob("*.docx")):
            return path
    return None


def _wilson_brief_json(week_id: str) -> Path | None:
    if not RUNS.exists():
        return None
    matches = sorted((RUNS / week_id).rglob("*wilson*.brief.json"))
    return matches[0] if matches else None


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
        "text": text or str(row.get("text") or "")[:TEXT_CAP],
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
            "decision": editorial.get("decision") or "INCLUDE",
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


def _parse_docx(path: Path) -> dict[str, Any]:
    from docx import Document

    doc = Document(str(path))
    run_id = None
    scan_window = None
    pack_title = None
    for para in doc.paragraphs[:20]:
        t = para.text.strip()
        if t.startswith("GRIT-Scan:") and not pack_title:
            pack_title = t
        m = re.search(r"Run\s+(\d{4}-\d{2}-\d{2}-[\w-]+)", t)
        if m:
            run_id = m.group(1)
        m2 = re.search(r"Scan window:\s*([^|]+)", t)
        if m2:
            scan_window = m2.group(1).strip()

    sources: dict[str, dict[str, Any]] = {}
    for para in doc.paragraphs:
        t = para.text.strip()
        if not t.startswith("[S"):
            continue
        m = _SOURCE_RE.match(t)
        if m:
            sid, publisher, title, published, url = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            title = title.strip().rstrip(",")
        else:
            sid = t[1 : t.index("]")]
            rest = t[t.index("]") + 1 :].strip()
            url_m = re.search(r"(https?://\S+)", rest)
            url = url_m.group(1) if url_m else ""
            parts = [p.strip() for p in rest.split(",", 2)]
            publisher = parts[0] if parts else ""
            title = parts[1] if len(parts) > 1 else rest[:200]
            published = ""
        if not sid:
            continue
        sources[sid] = {
            "brief_sid": sid,
            "publisher": publisher.strip(),
            "title": title.strip()[:300],
            "published_at": (published or "").strip(),
            "url": (url or "").strip(),
        }

    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    saw_sourced_band_table = False

    def _norm_key(item_text: str) -> str:
        text = re.sub(r"^[▲=▼]\s*", "", item_text)
        text = re.sub(r"\s*URS reach\s*\([^)]*\):.*$", "", text, flags=re.I)
        text = re.sub(r"\s*\[S\d+[^\]]*\]", "", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        # Keep the title before the first theme/risk bracket when present.
        m = re.match(r"^([^\[\]]+)", text)
        return (m.group(1).strip() if m else text)[:120]

    for table in doc.tables:
        if not table.rows:
            continue
        headers = [c.text.strip().replace("\n", " ").lower() for c in table.rows[0].cells]
        header_join = " | ".join(headers)
        has_sources_col = any("source" in h for h in headers)
        # Group radar elevated table: Band + Item + Sources.
        # Skip the owner-matrix duplicate (Band/Item/Trend/Owner) and theme catalogues.
        is_owner_matrix = any(h in {"owner or forum", "owner"} or h.startswith("owner or") for h in headers)
        is_trend_matrix = any(h == "trend" for h in headers) and not has_sources_col
        is_band_table = (
            any(h == "band" or h.startswith("band") for h in headers)
            and any("item" in h for h in headers)
            and has_sources_col
            and not is_owner_matrix
            and not is_trend_matrix
        )
        # BA briefs: numbered Item and Risk tables.
        is_item_risk = (
            any("item" in h for h in headers)
            and any("risk" in h for h in headers)
            and has_sources_col
            and not any(h == "band" or h.startswith("band") for h in headers)
        )
        if not (is_band_table or is_item_risk):
            continue
        if is_band_table and saw_sourced_band_table:
            # Only the first Escalating/Elevated sourced band table.
            continue
        if is_band_table:
            saw_sourced_band_table = True
        for row in table.rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            if len(cells) < 2:
                continue
            if is_band_table:
                band = cells[0].strip()
                item = cells[1].replace("\n", " ").strip()
                summary = cells[2].replace("\n", " ").strip() if len(cells) > 2 else ""
                sources_cell = cells[-1]
                section = "elevated_band"
                # Keep Escalating / Elevated / ▲ only — not Trends catalogue rows.
                band_l = band.lower()
                if not (
                    "escalat" in band_l
                    or "elevat" in band_l
                    or "▲" in band
                    or item.strip().startswith("▲")
                ):
                    continue
            else:
                item = cells[1].replace("\n", " ").strip()
                summary = cells[2].replace("\n", " ").strip() if len(cells) > 2 else ""
                sources_cell = cells[-1]
                if _MF_RE.search(item):
                    section = "management_focus"
                elif _RISK_TAG_RE.search(item):
                    section = "register_risk"
                elif re.search(r"\[trend\]", item, re.I):
                    section = "trend"
                else:
                    section = "other"
                band = "▲" if "▲" in item else ("=" if item.strip().startswith("=") else "")
            if not item or item.lower() in {"item and risk", "item", "#"}:
                continue
            source_ids = list(dict.fromkeys(_SID_LIST_RE.findall(sources_cell.replace("\n", " "))))
            if not source_ids:
                continue
            key = _norm_key(item)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if "escalat" in band.lower() or "elevat" in band.lower() or "▲" in item or "▲" in band:
                band_norm = "▲"
            elif item.strip().startswith("=") or band.strip() == "=":
                band_norm = "="
            else:
                band_norm = "="
            # BA: Trends section rows are near-elevated candidates, not elevated events.
            if section == "trend":
                band_norm = "="
            clean_item = re.sub(r"^[▲=▼]\s*", "", item).strip()
            # Strip trailing URS-reach boilerplate from Group radar item labels.
            clean_item = re.sub(r"\s*URS reach\s*\([^)]*\):.*$", "", clean_item, flags=re.I).strip()
            items.append(
                {
                    "dedupe_key": key,
                    "band": band_norm,
                    "item": clean_item[:400],
                    "summary": summary[:500],
                    "brief_source_ids": source_ids,
                    "section": section,
                    "mf_theme": (_m.group(1).strip() if (_m := _MF_RE.search(clean_item)) else ""),
                    "risk_tag": (_r.group(1).strip() if (_r := _RISK_TAG_RE.search(clean_item)) else ""),
                }
            )

    # Tier-1 physical threats (extra elevated seeds when present)
    threats: list[str] = []
    capture = False
    for para in doc.paragraphs:
        t = para.text.strip()
        if t.lower().startswith("tier-1 physical"):
            capture = True
            continue
        if capture:
            if re.match(r"^\d+\.\s", t) or t.startswith("Appendix") or t.startswith("Exposure") or t.startswith("1. Management"):
                capture = False
                continue
            if t:
                threats.append(t[:400])

    return {
        "path": str(path),
        "run_id": run_id,
        "pack_title": pack_title,
        "scan_window": scan_window,
        "sources": sources,
        "items": items,
        "threats": threats,
    }


def _signal_from_source(week_id: str, src: dict[str, Any], *, klass: str) -> dict[str, Any]:
    url = src.get("url") or ""
    title = src.get("title") or ""
    publisher = src.get("publisher") or ""
    sid = _stable_id("SIG", week_id, url or title, publisher)
    return _compact_signal(
        {
            "signal_id": sid,
            "title": title,
            "text": "",
            "publisher": publisher,
            "published_at": src.get("published_at") or "",
            "url": url,
            "tier": 2 if klass == "elevated" else 3,
            "source_band": "UNRATED",
            "lane": "",
            "editorial": {
                "decision": "INCLUDE",
                "source_ids": [sid],
                "immediate_threat": False,
                "significance": 10 if klass == "elevated" else 7,
                "risk_ids": [],
            },
        }
    )


def _is_elevated_item(item: dict[str, Any]) -> bool:
    if item.get("band") == "▲":
        return True
    if item.get("section") == "elevated_band":
        return True
    # Key / Emerging risk rows with ▲ already covered; MF ▲ also elevated.
    return False


def export_week_from_docx(meta: dict[str, str], docx_dir: Path, pool) -> dict[str, Any]:
    matches = sorted(docx_dir.glob(meta["docx_glob"]))
    if not matches:
        raise FileNotFoundError(f"no DOCX matching {meta['docx_glob']} in {docx_dir}")
    parsed = _parse_docx(matches[0])
    week_id = meta["week_id"]

    # Map brief S# → compact signals
    brief_to_signal: dict[str, dict[str, Any]] = {}
    for brief_sid, src in parsed["sources"].items():
        brief_to_signal[brief_sid] = _signal_from_source(week_id, src, klass="near_elevated")

    elevated_items = [it for it in parsed["items"] if _is_elevated_item(it)]
    # If Group band table found, prefer those; else all ▲ items.
    band_elevated = [it for it in elevated_items if it.get("section") == "elevated_band"]
    if band_elevated:
        elevated_items = band_elevated
    near_items = [it for it in parsed["items"] if it not in elevated_items and it.get("band") == "="]

    event_exports: list[dict[str, Any]] = []
    for it in elevated_items:
        member_sids: list[str] = []
        members: list[dict[str, Any]] = []
        for brief_sid in it.get("brief_source_ids") or []:
            sig = brief_to_signal.get(brief_sid)
            if not sig:
                stub_src = {"title": it["item"], "publisher": brief_sid, "url": "", "published_at": ""}
                sig = _signal_from_source(week_id, stub_src, klass="elevated")
                brief_to_signal[brief_sid] = sig
            member_sids.append(sig["signal_id"])
            members.append(sig)
        if not members:
            stub = _signal_from_source(
                week_id,
                {"title": it["item"], "publisher": "wilson-brief", "url": "", "published_at": ""},
                klass="elevated",
            )
            members = [stub]
            member_sids = [stub["signal_id"]]
            brief_to_signal[f"ITEM-{it['dedupe_key'][:24]}"] = stub
        # Mirror brief multi-source citation: each member cites the item's co-sources so
        # offline `_group` can join on shared distinctive headline terms (no SAME ledger here).
        cited = list(dict.fromkeys(member_sids))
        refreshed = []
        for sig in members:
            editorial = {
                **(sig.get("editorial") or {}),
                "decision": "INCLUDE",
                "source_ids": cited,
                "immediate_threat": True,
                "significance": 12,
                "risk_ids": list((sig.get("editorial") or {}).get("risk_ids") or []),
            }
            updated = {**sig, "tier": 1, "editorial": editorial}
            brief_to_signal_key = next(
                (k for k, v in brief_to_signal.items() if v.get("signal_id") == sig["signal_id"]),
                None,
            )
            if brief_to_signal_key:
                brief_to_signal[brief_to_signal_key] = updated
            refreshed.append(updated)
        members = refreshed
        event_id = _stable_id("EVT", week_id, it["item"])
        event_exports.append(
            {
                "week_id": week_id,
                "event_id": event_id,
                "band": "▲",
                "score": 12,
                "tier": 1,
                "item": it["item"],
                "lead": member_sids[0],
                "elevated": True,
                "near_elevated": False,
                "member_signal_ids": member_sids,
                "members": members,
                "section": it.get("section"),
                "mf_theme": it.get("mf_theme") or "",
                "risk_tag": it.get("risk_tag") or "",
                "summary": it.get("summary") or "",
            }
        )

    # Near-elevated: unused source-register rows + `=` items' sources.
    used_signal_ids = {m["signal_id"] for ev in event_exports for m in ev["members"]}
    near_signals: list[dict[str, Any]] = []
    for it in near_items:
        for brief_sid in it.get("brief_source_ids") or []:
            sig = brief_to_signal.get(brief_sid)
            if not sig or sig["signal_id"] in used_signal_ids:
                continue
            near_signals.append(sig)
            used_signal_ids.add(sig["signal_id"])
            event_exports.append(
                {
                    "week_id": week_id,
                    "event_id": f"NEAR-{sig['signal_id'][4:]}",
                    "band": "=",
                    "score": 7,
                    "tier": 3,
                    "item": sig.get("title") or it["item"],
                    "lead": sig["signal_id"],
                    "elevated": False,
                    "near_elevated": True,
                    "member_signal_ids": [sig["signal_id"]],
                    "members": [sig],
                }
            )
    # Remaining unused sources → near-elevated pool (cap)
    unused = [s for s in brief_to_signal.values() if s["signal_id"] not in used_signal_ids]
    for sig in unused[:40]:
        near_signals.append(sig)
        used_signal_ids.add(sig["signal_id"])
        event_exports.append(
            {
                "week_id": week_id,
                "event_id": f"NEAR-{sig['signal_id'][4:]}",
                "band": "=",
                "score": 6,
                "tier": 3,
                "item": sig.get("title") or "",
                "lead": sig["signal_id"],
                "elevated": False,
                "near_elevated": True,
                "member_signal_ids": [sig["signal_id"]],
                "members": [sig],
            }
        )

    # Optional pool enrichment (when present on a laptop / local pool).
    pool_gap = pool is None
    if pool is not None:
        for sig in list(brief_to_signal.values()):
            url = sig.get("url") or ""
            if not url:
                continue
            hit = pool.one(
                "SELECT signal_id, title, text, publisher, published_at, url, lane FROM signals WHERE url = ? LIMIT 1",
                (url,),
            )
            if hit:
                enriched = _compact_signal(sig, pool_row=dict(hit))
                sig.update(enriched)

    elevated_ids = {m["signal_id"] for ev in event_exports if ev.get("elevated") for m in ev["members"]}
    near_ids = {m["signal_id"] for ev in event_exports if ev.get("near_elevated") for m in ev["members"]}
    signals = []
    by_id: dict[str, dict[str, Any]] = {}
    for ev in event_exports:
        for m in ev["members"]:
            by_id[m["signal_id"]] = m
    for sid, raw in by_id.items():
        signals.append(
            {
                **raw,
                "week_id": week_id,
                "class": "elevated" if sid in elevated_ids else "near_elevated",
            }
        )

    # Offline merge snapshot
    from grit.pipeline.events import _group
    from grit.pool import Pool

    merge_rows = list(by_id.values())
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
    rel_docx = matches[0].name
    (week_dir / "manifest.json").write_text(
        json.dumps(
            {
                **{k: v for k, v in meta.items() if k != "docx_glob"},
                "docx_file": rel_docx,
                "docx_run_id": parsed.get("run_id"),
                "scan_window": parsed.get("scan_window"),
                "source": "wilson_docx" + ("+pool_url_enrichment" if not pool_gap else ""),
                "pool_available": not pool_gap,
                "escalating_events": sum(1 for e in event_exports if e.get("elevated")),
                "elevated_signals": len(elevated_ids),
                "near_elevated_signals": len(near_ids - elevated_ids),
                "exported_signals": len(signals),
                "offline_group_clusters": len(groups),
                "source_register_count": len(parsed["sources"]),
                "gap": (
                    "No local output/runs or grit.sqlite on this builder host; "
                    "fixtures built from Wilson DOCX source register + elevated item tables."
                    if pool_gap and not _wilson_brief_json(week_id)
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
        "brief_items": [{"event_id": e["event_id"], "item": e.get("item") or ""} for e in event_exports if e.get("elevated")],
        "parsed_items": elevated_items,
    }


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
    if re.search(r"[áéíóúñü]", ta + tb, re.I) or "plus ultra" in (ta + tb).lower():
        flags.append("accent_or_alias_surface")
    actors = {"houthi", "houthis", "saudi", "taiwan", "ofac", "nvidia", "bis", "china", "iran"}
    shared_actors = (wa | {t.lower() for t in ta.split()}) & (wb | {t.lower() for t in tb.split()}) & actors
    if shared_actors and not (len(shared) >= 2):
        flags.append("shared_actor_weak_overlap:" + ",".join(sorted(shared_actors)[:3]))
    return flags


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
        left = a if a["signal_id"] == sa else b
        right = b if b["signal_id"] == sb else a
        pairs.append(
            {
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
        )

    for exported in week_exports:
        week_id = exported["meta"]["week_id"]
        signal_event: dict[str, str] = {}
        for ev in exported["events"]:
            if not ev.get("elevated"):
                continue
            for sid in ev.get("member_signal_ids") or []:
                signal_event[sid] = ev["event_id"]
        by_id = {s["signal_id"]: s for s in exported["signals"]}

        for ev in exported["events"]:
            if not ev.get("elevated"):
                continue
            members = [by_id[s] for s in ev.get("member_signal_ids") or [] if s in by_id]
            for a, b in combinations(members, 2):
                add(week_id, a, b, reason="co_member_elevated_export", provisional="SAME")

        elevated_events = [ev for ev in exported["events"] if ev.get("elevated") and ev.get("members")]
        for ev_a, ev_b in combinations(elevated_events, 2):
            for a in ev_a["members"][:4]:
                for b in ev_b["members"][:4]:
                    flags = _hard_flags(a, b)
                    if not flags:
                        continue
                    add(week_id, a, b, reason="cross_event_hard", provisional="UNRELATED")

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

    hard = [p for p in pairs if p.get("hard_flags")]
    easy = [p for p in pairs if not p.get("hard_flags")]
    ordered = hard + easy
    if len(ordered) < HARD_PAIR_TARGET:
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

    if len(ordered) > 600:
        hard = [p for p in ordered if p.get("hard_flags")]
        rest = [p for p in ordered if not p.get("hard_flags")]
        ordered = hard[:450] + rest[:150]
    return ordered


def write_pairs(pairs: list[dict[str, Any]]) -> None:
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
                    "ra_label": "",
                    "ra_notes": "",
                }
            )


def _theme_bucket(item: str, mf_theme: str, risk_tag: str) -> str | None:
    blob = f"{item} {mf_theme} {risk_tag}".lower()
    # OFAC / SDN designations stay their own topic even when the brief tags "Export controls".
    if any(k in blob for k in ("ofac", "sdn list", "secondary sanction")):
        return "ofac-sanctions"
    if any(k in blob for k in ("houthi", "riyadh", "hormuz", "iran", "middle east", "red sea", "gulf flight", "bahrain", "kuwait")):
        return "middle-east-security-escalation"
    if any(k in blob for k in ("chip", "export control", "bis", "nvidia", "megaspeed", "advanced computing", "semiconductor")):
        return "us-advanced-compute-controls"
    if any(k in blob for k in ("saudisation", "saudi", "uae and qatar investment")):
        return "saudi-operating-policy"
    if any(k in blob for k in ("cyber", "ransomware", "cisa", "hack", "ot ")):
        return "cyber-ot"
    if any(k in blob for k in ("unmanned", "asv", "autonomous", "digital twin", "usv")):
        return "unmanned-autonomy"
    if any(k in blob for k in ("erp", "cbtc", "rail", "tolling", "metro")):
        return "rail-its-delivery"
    if any(k in blob for k in ("plus ultra", "new pacific", "jsx", "airline credit", "creditor protection")):
        return "airline-credit-restructuring"
    return None


def write_topics(week_exports: list[dict[str, Any]]) -> None:
    """Expected topic groupings seeded from Wilson elevated items; gaps marked for Jony."""
    rows: list[dict[str, Any]] = []
    # STE 25 Nov follows the Strategic Radar PDF shape (Wilson DOCX is now the source week).
    ste_preset_order = [
        ("middle-east-security-escalation", "Middle East security escalation"),
        ("us-advanced-compute-controls", "US advanced-compute controls"),
        ("ofac-sanctions", "OFAC designations / secondary sanctions"),
        ("saudi-operating-policy", "Saudi operating-policy / investment signals"),
    ]

    for exported in week_exports:
        week_id = exported["meta"]["week_id"]
        elevated = [e for e in exported["events"] if e.get("elevated")]
        esc_ids = [e["event_id"] for e in elevated]
        buckets: dict[str, list[dict[str, Any]]] = {}
        for ev in elevated:
            key = _theme_bucket(ev.get("item") or "", ev.get("mf_theme") or "", ev.get("risk_tag") or "")
            if key:
                buckets.setdefault(key, []).append(ev)

        if week_id == "2026-11-25-ste":
            topics = []
            claimed: set[str] = set()
            for topic_key, label in ste_preset_order:
                matched = buckets.get(topic_key) or []
                # Also substring fallback for STE
                if not matched:
                    substrs = {
                        "middle-east-security-escalation": ["Houthi", "Iran", "Middle East", "Riyadh"],
                        "us-advanced-compute-controls": ["chip", "BIS", "export control", "Megaspeed", "computing"],
                        "ofac-sanctions": ["OFAC", "SDN"],
                        "saudi-operating-policy": ["Saudisation", "Saudi"],
                    }[topic_key]
                    matched = [
                        ev
                        for ev in elevated
                        if any(s.lower() in (ev.get("item") or "").lower() for s in substrs)
                        and ev["event_id"] not in claimed
                    ]
                for ev in matched:
                    claimed.add(ev["event_id"])
                topics.append(
                    {
                        "topic_key": topic_key,
                        "label": label,
                        "event_ids": [ev["event_id"] for ev in matched],
                        "candidate_items": [ev.get("item") or "" for ev in matched],
                        "notes": "Seeded from Wilson DOCX elevated rows + PDF topic shape. Confirm with R&A.",
                        "gap_for_jony": True,
                    }
                )
            unassigned = [eid for eid in esc_ids if eid not in claimed]
            rows.append(
                {
                    "week_id": week_id,
                    "role": exported["meta"].get("role"),
                    "status": "skeleton_from_wilson_docx",
                    "reference": "Wilson DOCX GRIT-Scan_ste_wilson_2026-11-25 + topic-collapse PDF shape",
                    "topics": topics,
                    "unassigned_event_ids": unassigned,
                    "unassigned_guidance": "OFAC, Saudi operating-policy and standing conditions stay separate unless basis rule permits.",
                    "all_elevated_event_ids": esc_ids,
                    "gap_for_jony": True,
                    "notes": "Wilson elevated items pre-bucketed; Jony confirms R&A topic membership.",
                }
            )
        else:
            topics = []
            claimed: set[str] = set()
            for topic_key, evs in sorted(buckets.items()):
                for ev in evs:
                    claimed.add(ev["event_id"])
                topics.append(
                    {
                        "topic_key": topic_key,
                        "label": topic_key.replace("-", " "),
                        "event_ids": [ev["event_id"] for ev in evs],
                        "candidate_items": [ev.get("item") or "" for ev in evs],
                        "gap_for_jony": True,
                    }
                )
            leftover = [ev for ev in elevated if ev["event_id"] not in claimed]
            if leftover or not topics:
                topics.append(
                    {
                        "topic_key": "TBD",
                        "label": "GAP — Jony to group",
                        "event_ids": [],
                        "candidate_elevated": [{"event_id": e["event_id"], "item": e.get("item") or ""} for e in leftover or elevated],
                        "gap_for_jony": True,
                    }
                )
            rows.append(
                {
                    "week_id": week_id,
                    "role": exported["meta"].get("role"),
                    "status": "skeleton_from_wilson_docx",
                    "reference": f"Wilson DOCX elevated items for {week_id}",
                    "topics": topics,
                    "unassigned_event_ids": [e["event_id"] for e in leftover] if leftover else [],
                    "all_elevated_event_ids": esc_ids,
                    "gap_for_jony": True,
                    "notes": "Theme buckets are heuristic from item text; fill R&A judgement.",
                }
            )

    (OUT / "topics.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_readme(week_exports: list[dict[str, Any]], pair_count: int, *, pool_gap: bool, docx_dir: Path) -> None:
    lines = [
        "# Identity evaluation fixtures (Phase E)",
        "",
        "Built for plan v2 Phase E. **No pipeline merge behaviour is changed by these fixtures.**",
        "",
        "## Weeks exported (Wilson DOCX — authoritative)",
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
        "## Provenance",
        "",
        f"- Wilson DOCX directory used: `{docx_dir}`",
        (
            "- **Pool / weekly-run gap:** `output/grit.sqlite` and matching `output/runs/<week_id>` "
            "were not available on the builder host. Fixtures are built from Wilson DOCX elevated "
            "item tables + Appendix B source registers. Signal IDs are stable hashes of URL/title "
            "(not pool `SIG-*` ids). Re-run this builder on a host with the pool to enrich text/lanes."
            if pool_gap
            else "- Local pool was available; signal text/lanes enriched by URL match where possible."
        ),
        "- URS-Taiwan pack week is **not** in the attached six briefs; URS Group weeks "
        "`2026-11-19-urs` and `2026-11-26-urs` stand in for the URS role.",
        "",
        "## Files",
        "",
        "- `weeks/<week_id>/events.jsonl` — elevated + near-elevated events with member signals",
        "- `weeks/<week_id>/signals.jsonl` — compact signal payloads for the eval harness",
        "- `weeks/<week_id>/manifest.json` — provenance",
        "- `pairs.jsonl` — candidate pairs (`signal_a`, `signal_b`, `label`); labels are empty or **provisional** only",
        "- `labelling_sheet.csv` — sheet for Jony (fill `ra_label`: SAME | RELATED | UNRELATED)",
        "- `topics.jsonl` — expected topic groupings seeded from Wilson elevated items",
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
    # Drop stale week dirs from the previous proxy build.
    weeks_root = OUT / "weeks"
    if weeks_root.exists():
        for child in weeks_root.iterdir():
            if child.is_dir():
                for f in child.iterdir():
                    f.unlink()
                child.rmdir()

    docx_dir = _find_docx_dir()
    if docx_dir is None:
        raise SystemExit(
            "No Wilson DOCX directory found. Set WILSON_DOCX_DIR or place briefs under "
            "tests/fixtures/identity_eval/wilson_briefs/"
        )
    pool = _load_pool()
    pool_gap = pool is None and not RUNS.exists()
    week_exports = [export_week_from_docx(meta, docx_dir, pool) for meta in WEEKS]
    pairs = generate_pairs(week_exports)
    write_pairs(pairs)
    write_labelling_sheet(pairs)
    write_topics(week_exports)
    write_readme(week_exports, len(pairs), pool_gap=pool_gap, docx_dir=docx_dir)
    summary = {
        "weeks": [e["meta"]["week_id"] for e in week_exports],
        "docx_dir": str(docx_dir),
        "pool_available": pool is not None,
        "pool_gap": pool_gap,
        "pair_count": len(pairs),
        "hard_pairs": sum(1 for p in pairs if p.get("hard_flags")),
        "provisional_labels": sum(1 for p in pairs if p.get("label")),
        "unlabelled": sum(1 for p in pairs if not p.get("label")),
        "elevated_events_per_week": {
            e["meta"]["week_id"]: sum(1 for ev in e["events"] if ev.get("elevated")) for e in week_exports
        },
    }
    (OUT / "build_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
