"""WSH Assessment and Critic loop, borrowed from the main reasoned weekly chain (spec 45 / 46).

Deterministic scope only nominates candidates. Assessment must state fact → STE exposure → transmission →
impact/lesson for every candidate. The Critic challenges the weakest link. The loop makes editorial INCLUDE, WATCH, or DROP decisions only.
Code publishes nothing.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Mapping, Sequence

from grit.wsh.bulletins import detect_series, is_bulletin, is_scanned_pdf_only
from grit.wsh.prompt import ASSESSMENT_SYSTEM, CRITIC_SYSTEM, REASON_PROMPT_VERSION
from grit.wsh.scope import classify, candidate_for_reasoning, section_for_signal

BATCH = 10
INDUSTRIES = (
    "defence",
    "aerospace",
    "marine",
    "construction",
    "facilities management",
    "security services",
)
SECTIONS = ("SERIOUS", "LEADING", "SIF", "HFACS", "EMERGING")
DECISIONS = frozenset({"INCLUDE", "WATCH", "EXCLUDE", "DEFER"})


class ReasonContractError(ValueError):
    pass


def _plain(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ReasonContractError("Model did not return a JSON object.")
    return parsed


def _call_json(client: Any, system: str, task: Mapping[str, Any], *, model: str) -> dict[str, Any]:
    config = {"system_instruction": system, "response_mime_type": "application/json", "temperature": 0.1}
    response = client.models.generate_content(
        model=model,
        contents=json.dumps({"agent": "WSH-Reason", "task": task}, default=str),
        config=config,
    )
    return _parse_json(response.text or "")


def candidate_record(row: Mapping[str, Any], classification: Mapping[str, Any], section_id: str | None, prefilter: str) -> dict[str, Any]:
    title = row.get("title") or ""
    body = row.get("text") or ""
    url = row.get("url") or ""
    publisher = row.get("publisher") or ""
    series = detect_series(title, body, url, publisher)
    return {
        "signal_id": row["signal_id"],
        "title": row.get("title"),
        "publisher": row.get("publisher"),
        "published_at": str(row.get("published_at") or "")[:10] or None,
        "url": row.get("url"),
        "excerpt": _plain(body)[:5000 if (section_id == "SERIOUS" or series) else 1800],
        "section_hint": section_id,
        "prefilter": prefilter,
        "bulletin_series": series,
        "is_bulletin": series is not None,
        "text_not_read": is_scanned_pdf_only(title, body, url),
        "classification": {
            "place": classification.get("place"),
            "industries": classification.get("industries"),
            "hazards": classification.get("hazards"),
            "frameworks": classification.get("frameworks"),
            "regulators": classification.get("regulators"),
            "workplace_cue": classification.get("workplace_cue"),
            "exclude_cue": classification.get("exclude_cue"),
            "emerging_topic": classification.get("emerging_topic"),
            "is_method": classification.get("is_method"),
            "is_regulatory": classification.get("is_regulatory"),
        },
        "source_band": row.get("source_band"),
    }


def _deferred(signal_id: str, reason: str) -> dict[str, Any]:
    return {
        "signal_id": signal_id,
        "decision": "DEFER",
        "reason": reason,
        "source_ids": [signal_id],
        "fact": "not established",
        "exposure": "not established",
        "transmission": "not established",
        "impact": "not established",
        "lesson_or_practice": "not established",
        "uncertainty": reason,
        "ste_industry": None,
        "section_id": None,
        "significance": {},
        "what_happened": "not established",
        "failed_controls": "not established",
        "earlier_cases": "not established",
        "what_companies_should_do": "not established",
        "instrument": "not established",
        "investigation_status": "not established",
        "penalty": "not established",
        "is_bulletin": False,
    }


def check_selection(row: Mapping[str, Any], expected: set[str], *, bulletin: bool = False) -> dict[str, Any]:
    """Normalize one selection. Bad INCLUDE/WATCH rows are downgraded rather than failing the batch (weekly pattern)."""
    signal_id = str(row.get("signal_id") or "")
    if signal_id not in expected:
        raise ReasonContractError(f"Unknown signal_id {signal_id}")
    decision = str(row.get("decision") or "").upper()
    if decision not in DECISIONS:
        return _deferred(signal_id, f"invalid decision {row.get('decision')!r}")
    out = {
        "signal_id": signal_id,
        "decision": decision,
        "reason": _plain(str(row.get("reason") or "")) or "not established",
        "source_ids": [str(item) for item in (row.get("source_ids") or [signal_id]) if item],
        "fact": _plain(str(row.get("fact") or "")) or "not established",
        "exposure": _plain(str(row.get("exposure") or "")) or "not established",
        "transmission": _plain(str(row.get("transmission") or "")) or "not established",
        "impact": _plain(str(row.get("impact") or "")) or "not established",
        "lesson_or_practice": _plain(str(row.get("lesson_or_practice") or "")) or "not established",
        "uncertainty": _plain(str(row.get("uncertainty") or "")) or "not established",
        "ste_industry": (str(row["ste_industry"]).strip() if row.get("ste_industry") else None),
        "section_id": str(row.get("section_id") or "").upper() or None,
        "significance": row.get("significance") if isinstance(row.get("significance"), dict) else {},
        "what_happened": _plain(str(row.get("what_happened") or "")) or "not established",
        "failed_controls": _plain(str(row.get("failed_controls") or "")) or "not established",
        "earlier_cases": _plain(str(row.get("earlier_cases") or "")) or "not established",
        "what_companies_should_do": _plain(str(row.get("what_companies_should_do") or "")) or "not established",
        "instrument": _plain(str(row.get("instrument") or "")) or "not established",
        "investigation_status": _plain(str(row.get("investigation_status") or "")) or "not established",
        "penalty": _plain(str(row.get("penalty") or "")) or "not established",
        "is_bulletin": bool(bulletin or row.get("is_bulletin")),
    }
    if out["section_id"] and out["section_id"] not in SECTIONS:
        out["section_id"] = None
    if decision in {"INCLUDE", "WATCH"}:
        # Chain fields must be present; "not established" is allowed (weekly Assessor contract).
        if not out["reason"] or out["reason"] == "not established":
            out["decision"] = "EXCLUDE"
            out["reason"] = "INCLUDE/WATCH without a reason"
        elif not out["section_id"]:
            out["decision"] = "WATCH" if out["fact"] != "not established" else "EXCLUDE"
            out["reason"] = (out["reason"] + "; section not established").strip("; ")
            if out["decision"] == "WATCH":
                out["section_id"] = "EMERGING"
        deep = out["is_bulletin"] or out["section_id"] == "SERIOUS"
        if deep and out["decision"] in {"INCLUDE", "WATCH"}:
            controls = out["what_companies_should_do"]
            if controls and controls != "not established":
                out["lesson_or_practice"] = controls
            if out["what_happened"] and out["what_happened"] != "not established":
                out["fact"] = out["what_happened"]
            # A serious INCLUDE needs the sequence. A control list is optional: many news reports do not state one.
            if out["decision"] == "INCLUDE" and out["what_happened"] == "not established":
                out["decision"] = "WATCH"
                out["uncertainty"] = (out["uncertainty"] + "; sequence not established").strip("; ")
    return out


def check_batch(
    raw: Mapping[str, Any],
    expected: set[str],
    *,
    bulletin_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    selections = raw.get("selections")
    if not isinstance(selections, list):
        raise ReasonContractError("selections must be a list")
    bulletins = bulletin_ids or set()
    by_id: dict[str, dict[str, Any]] = {}
    for row in selections:
        if not isinstance(row, Mapping):
            continue
        signal_id = str(row.get("signal_id") or "")
        if signal_id not in expected or signal_id in by_id:
            continue
        by_id[signal_id] = check_selection(row, expected, bulletin=signal_id in bulletins)
    for signal_id in expected:
        if signal_id not in by_id:
            by_id[signal_id] = _deferred(signal_id, "missing from model selections")
    return [by_id[signal_id] for signal_id in sorted(by_id)]


def _authority() -> dict[str, Any]:
    return {
        "watched_industries": list(INDUSTRIES),
        "geography": "Phase 1: Singapore is the workplace or regulatory locus for incidents and circulars. Method papers may be global.",
        "sections": list(SECTIONS),
        "rule": (
            "Exposure decides relevance for STE Workplace Safety & Health: a credible workplace incident, "
            "regulatory change, leading practice or method paper that could affect defence, aerospace, marine, "
            "construction, facilities management or security services operations. Never INCLUDE a residential, "
            "clinical, combat or passenger-lifestyle story. Write not established for unsupported links."
        ),
    }


def assess_batch(client: Any, candidates: Sequence[Mapping[str, Any]], *, model: str) -> list[dict[str, Any]]:
    expected = {str(row["signal_id"]) for row in candidates}
    bulletin_ids = {str(row["signal_id"]) for row in candidates if row.get("is_bulletin")}
    task = {
        "prompt_version": REASON_PROMPT_VERSION,
        "role": "Assessment",
        "authority": _authority(),
        "candidates": list(candidates),
        "return": {
            "selections": [
                {
                    "signal_id": "...",
                    "decision": "INCLUDE|WATCH|EXCLUDE|DEFER",
                    "reason": "...",
                    "source_ids": ["..."],
                    "fact": "...",
                    "exposure": "which STE watched industry / workforce is exposed, or not established",
                    "transmission": "how the event or practice would reach STE operations, or not established",
                    "impact": "sourced or conditional impact / why it matters, or not established",
                    "lesson_or_practice": "published lesson or practice only if the source states it, else not established",
                    "ste_industry": "one watched industry, or method, or regulatory, or null",
                    "section_id": "SERIOUS|LEADING|SIF|HFACS|EMERGING",
                    "significance": {"exposure": 0, "consequence": 0, "immediacy": 0, "evidence": 0, "change": 0},
                    "uncertainty": "...",
                    "what_happened": "sequence for SERIOUS and Council bulletins",
                    "failed_controls": "controls the source says failed, else not established",
                    "earlier_cases": "only accidents the source names, else not established",
                    "what_companies_should_do": "numbered controls from the bulletin, else not established",
                    "instrument": "named regulation/code/guideline or not established",
                    "investigation_status": "preliminary when the bulletin says so, else not established",
                    "penalty": "only if the bulletin states a figure, else not established",
                }
            ]
        },
    }
    raw = _call_json(client, ASSESSMENT_SYSTEM, task, model=model)
    return check_batch(raw, expected, bulletin_ids=bulletin_ids)


def critique_batch(client: Any, candidates: Sequence[Mapping[str, Any]], selections: Sequence[Mapping[str, Any]], *, model: str) -> list[dict[str, Any]]:
    expected = {str(row["signal_id"]) for row in candidates}
    bulletin_ids = {str(row["signal_id"]) for row in candidates if row.get("is_bulletin")}
    task = {
        "prompt_version": REASON_PROMPT_VERSION,
        "role": "Critic",
        "authority": _authority(),
        "candidates": list(candidates),
        "assessor_selections": list(selections),
        "return": {
            "issues": [{"kind": "FALSE_POSITIVE|FALSE_NEGATIVE|UNSUPPORTED|WRONG_ATTRIBUTION", "target": "signal_id", "problem": "...", "material": True}],
            "selections": "same contract as Assessment for every candidate",
        },
    }
    raw = _call_json(client, CRITIC_SYSTEM, task, model=model)
    reviewed = check_batch(raw, expected, bulletin_ids=bulletin_ids)
    return reviewed


def nominate(pool: Any, run_id: str, pack: Any, *, limit: int | None = 80) -> list[dict[str, Any]]:
    """Build the candidate list for Assessment/Critic.

    Prefer deterministic KEEP rows. Also pull method/regulatory DROPs the generic assessor may have
    discarded, so Campbell/CSRA/HFACS papers still reach the reasoned loop. Cap the pool so the
    model can go deep rather than skim hundreds of SEO hits.
    """
    scope = pack.scope or {}
    theme_to_section = {row["themeId"]: row["id"] for row in scope.get("sections") or []}
    nominated: list[dict[str, Any]] = []
    seen: set[str] = set()

    def consider(row: Mapping[str, Any], *, force_method: bool = False) -> None:
        nonlocal nominated
        signal_id = row["signal_id"]
        if signal_id in seen:
            return
        themes = [item.get("theme_id") for item in (row.get("themes") or []) if item.get("theme_id")]
        for target in (row.get("link") or {}).get("sighting_targets") or []:
            if target not in themes:
                themes.append(target)
        classification = classify(row.get("title") or "", row.get("text") or "", scope=scope)
        section_id = section_for_signal(classification, [str(item) for item in themes if item], scope,
                                        title=row.get("title") or "", body=row.get("text") or "")
        if not section_id:
            section_id = next((theme_to_section[theme] for theme in themes if theme in theme_to_section), None)
        ok, reason = candidate_for_reasoning(
            classification,
            section_id=section_id,
            title=row.get("title") or "",
            body=row.get("text") or "",
            url=row.get("url") or "",
            publisher=row.get("publisher") or "",
        )
        if not ok and force_method and (classification.get("is_method") or classification.get("is_regulatory") or is_bulletin(
            row.get("title") or "", row.get("text") or "", row.get("url") or "", row.get("publisher") or "",
        )):
            if is_bulletin(row.get("title") or "", row.get("text") or "", row.get("url") or "", row.get("publisher") or ""):
                ok, reason = True, "CANDIDATE_BULLETIN"
            else:
                ok, reason = True, "CANDIDATE_METHOD" if classification.get("is_method") else "CANDIDATE_REGULATORY"
        if not ok:
            return
        seen.add(signal_id)
        nominated.append(candidate_record(row, classification, section_id, reason))

    for row in pool.assessments(run_id, kept_only=True):
        consider(row)
    # Method, regulator, and Council bulletins sometimes fail the generic event assessor. Recover them for reasoning.
    for row in pool.assessments(run_id, kept_only=False):
        if row.get("decision") == "KEEP":
            continue
        classification = classify(row.get("title") or "", row.get("text") or "", scope=scope)
        if classification.get("is_method") or classification.get("is_regulatory") or is_bulletin(
            row.get("title") or "", row.get("text") or "", row.get("url") or "", row.get("publisher") or "",
        ):
            consider(row, force_method=True)
    if limit is not None and len(nominated) > limit:
        # Prefer bulletins / method / regulatory / Singapore workplace over loose industry hits.
        rank = {
            "CANDIDATE_BULLETIN": 0,
            "CANDIDATE_METHOD": 1,
            "CANDIDATE_REGULATORY": 2,
            "CANDIDATE_SG_WORKPLACE": 3,
            "CANDIDATE_SG_HAZARD": 4,
            "CANDIDATE_SG_EMERGING": 5,
            "CANDIDATE_FRAMEWORK": 6,
        }
        nominated.sort(key=lambda row: (rank.get(str(row.get("prefilter")), 9), row.get("published_at") or "", row["signal_id"]))
        nominated = nominated[:limit]
    return nominated


def run_reasoned_review(
    pool: Any,
    run_id: str,
    pack: Any,
    *,
    client: Any | None = None,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    batch_size: int = BATCH,
) -> dict[str, Any]:
    """Assessment then Critic over the wide WSH candidate pool. Returns selections keyed by signal_id."""
    from grit.agents.gemini import gemini_client

    active = client or gemini_client()
    values_model = model or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    log = progress or (lambda message: None)
    candidates = nominate(pool, run_id, pack)
    log(f"WSH reasoning: {len(candidates)} candidates nominated from the wide prefilter.")
    by_id = {row["signal_id"]: row for row in candidates}
    assessed: dict[str, dict[str, Any]] = {}
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        log(f"  Assessment batch {start // batch_size + 1}: {len(batch)} candidates")
        try:
            rows = assess_batch(active, batch, model=values_model)
        except Exception as exc:
            log(f"  Assessment batch failed ({type(exc).__name__}): falling back to EXCLUDE")
            rows = [{
                "signal_id": row["signal_id"], "decision": "EXCLUDE", "reason": f"Assessment failed: {type(exc).__name__}",
                "source_ids": [row["signal_id"]], "fact": "not established", "exposure": "not established",
                "transmission": "not established", "impact": "not established", "lesson_or_practice": "not established",
                "uncertainty": str(exc)[:160], "ste_industry": None, "section_id": row.get("section_hint"), "significance": {},
            } for row in batch]
        for row in rows:
            assessed[row["signal_id"]] = row
    final: dict[str, dict[str, Any]] = {}
    ids = list(assessed)
    for start in range(0, len(ids), batch_size):
        batch_ids = ids[start : start + batch_size]
        batch_candidates = [by_id[item] for item in batch_ids if item in by_id]
        batch_selections = [assessed[item] for item in batch_ids]
        log(f"  Critic batch {start // batch_size + 1}: {len(batch_candidates)} candidates")
        try:
            rows = critique_batch(active, batch_candidates, batch_selections, model=values_model)
        except Exception as exc:
            log(f"  Critic batch failed ({type(exc).__name__}): keeping Assessment decisions")
            rows = batch_selections
        for row in rows:
            final[row["signal_id"]] = {**row, "candidate": by_id.get(row["signal_id"])}
    included = sum(1 for row in final.values() if row["decision"] in {"INCLUDE", "WATCH"})
    log(f"WSH reasoning complete: {included} INCLUDE/WATCH of {len(final)} reviewed.")
    return {
        "prompt_version": REASON_PROMPT_VERSION,
        "nominated": len(candidates),
        "reviewed": len(final),
        "selections": final,
    }
