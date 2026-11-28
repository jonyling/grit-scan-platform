"""Topic collapse rules: persistent storylines over distinct events (plan v2 Phase P2).

TOPIC is a third object beside SAME and RELATED. Topics never write SAME, never collapse
child event_ids, and never write controlled fields (band, tier, override, challenge).
Agents propose; this module validates and applies.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Mapping, Sequence

from grit.core.canonical import canonical_hash

CONTRACT = "topic-collapse-2"
PROMPT_VERSION = "topic-collapse-2"

STATUSES_VS_LAST = frozenset({"NEW", "CONTINUING", "ESCALATING", "EASING"})
CRITIC_ACTIONS = frozenset({"UPHOLD", "SPLIT", "REASSIGN"})
RESOLUTION_STATUSES = frozenset({"VALIDATED", "REJECTED", "UNASSIGNED", "THEME_CANDIDATE"})

# Apply thresholds (plan P2.4).
T_TOPIC = 0.80
AMBIGUOUS_LO, AMBIGUOUS_HI = 0.55, 0.80

# Input selection (plan P2.2).
NEAR_THRESHOLD_TOP_N = 15
PRIOR_TOPIC_WEEKS = 4

BASIS_CHARS = 400
LABEL_CHARS = 120
DIFF_CHARS = 400
STE_PATH_CHARS = 240
REASON_CHARS = 400

# Weak-basis rejection: location-only / staff / single shared word.
_STAFF_BASIS = re.compile(
    r"\b(staff|employee|personnel|headcount|colleague|travell?er|internal evidence|"
    r"approved.?internal|human.?approved)\b",
    re.I,
)
_LOCATION_ONLY_BASIS = re.compile(
    r"\b(same (country|region|place|city|geography|location)|only (share|shared) (a )?(country|region|place)|"
    r"both (events? )?(are )?in |located in |geographic(ally)?|in [A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    re.I,
)
_PLACE_WORDS = frozenset({
    "saudi", "arabia", "iran", "iraq", "yemen", "israel", "gulf", "taiwan", "china", "malaysia",
    "singapore", "uae", "qatar", "bahrain", "kuwait", "hormuz", "middle", "east", "asia", "europe",
    "us", "usa", "america", "region", "country", "city", "port", "airport",
})
_GENERIC_BASIS_WORDS = frozenset({
    "both", "events", "event", "share", "shared", "same", "only", "linked", "link", "related",
    "because", "they", "their", "these", "those", "about", "from", "with", "have", "been",
})


def enabled(config: Mapping[str, Any] | None) -> bool:
    """Feature flag: run config topic_collapse=1 (or true/yes/on)."""
    value = (config or {}).get("topic_collapse")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def mint_topic_key(label: str, *, first_seen: str | date | None = None) -> str:
    """Stable key for a NEW topic: slug + first-seen date (plan P2.4)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "topic").casefold()).strip("-") or "topic"
    slug = slug[:60]
    if isinstance(first_seen, date):
        day = first_seen.isoformat()
    else:
        day = str(first_seen or "")[:10] or date.today().isoformat()
    return f"{slug}-{day}"


def topic_input_hash(events: Sequence[Mapping[str, Any]], prior_topics: Sequence[Mapping[str, Any]]) -> str:
    """Reuse / ledger key over the resolver input set."""
    return canonical_hash({
        "contract": CONTRACT,
        "events": [
            {
                "event_id": row.get("event_id"),
                "lead_title": (row.get("lead") or {}).get("title") if isinstance(row.get("lead"), Mapping) else row.get("lead_title"),
                "signature": row.get("signature"),
                "elevated": bool(row.get("elevated")),
                "score": row.get("score"),
            }
            for row in events
        ],
        "prior_topics": [
            {
                "topic_key": row.get("topic_key"),
                "label": row.get("label"),
                "event_ids": list(row.get("event_ids") or row.get("last_event_ids") or []),
            }
            for row in prior_topics
        ],
    })


def topic_ledger_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    """Hash of standing topic resolutions for ReviewGate package content (plan P2.5)."""
    compact = []
    for row in rows:
        compact.append({
            "topic_key": row.get("topic_key"),
            "run_id": row.get("run_id"),
            "label": row.get("label"),
            "event_ids": list(row.get("event_ids") or []),
            "status": row.get("status"),
            "status_vs_last_week": row.get("status_vs_last_week"),
            "confidence": row.get("confidence"),
            "input_hash": row.get("input_hash"),
        })
    compact.sort(key=lambda item: (str(item.get("topic_key") or ""), str(item.get("run_id") or "")))
    return canonical_hash({"contract": CONTRACT, "topics": compact})


def select_topic_events(
    events: Sequence[Mapping[str, Any]],
    *,
    near_top_n: int = NEAR_THRESHOLD_TOP_N,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Elevated events plus top-N near-threshold non-elevated by score (plan P2.2)."""
    elevated = [dict(event) for event in events if event.get("elevated") and event.get("event_id")]
    non = [
        dict(event) for event in events
        if not event.get("elevated") and event.get("members") and event.get("event_id")
    ]
    non.sort(key=lambda event: (-(event.get("score") if event.get("score") is not None else -1), str(event["event_id"])))
    near = non[: max(0, near_top_n)]
    return elevated, near


def pack_event_for_topics(event: Mapping[str, Any]) -> dict[str, Any]:
    """Compact event payload for the TopicResolver call."""
    lead = event.get("lead") if isinstance(event.get("lead"), Mapping) else {}
    signature = event.get("signature") if isinstance(event.get("signature"), Mapping) else {}
    sources = []
    for row in event.get("reports") or event.get("members") or []:
        if isinstance(row, Mapping) and row.get("publisher"):
            sources.append(str(row["publisher"]))
    return {
        "event_id": str(event.get("event_id") or ""),
        "elevated": bool(event.get("elevated")),
        "score": event.get("score"),
        "band": event.get("band"),
        "risk_label": event.get("risk_label") or "",
        "lead_title": str(lead.get("title") or event.get("lead_title") or ""),
        "first_date": event.get("first_date"),
        "last_date": event.get("last_date"),
        "sources": list(dict.fromkeys(sources))[:8],
        "signature": {
            "actor": list(signature.get("actor") or []),
            "action_class": signature.get("action_class"),
            "action_text": signature.get("action_text") or "",
            "location": list(signature.get("location") or []),
            "date_window": list(signature.get("date_window") or [None, None]),
            "consequence_class": signature.get("consequence_class"),
            "object": list(signature.get("object") or []),
        },
        "ste_path_summary": str(event.get("ste_path_summary") or event.get("common_ste_path") or "")[:STE_PATH_CHARS],
    }


def pack_prior_topic(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "topic_key": str(row.get("topic_key") or ""),
        "label": str(row.get("label") or ""),
        "last_event_ids": list(row.get("event_ids") or row.get("last_event_ids") or []),
        "last_implication": str(row.get("differences") or row.get("last_implication") or row.get("basis") or "")[:DIFF_CHARS],
        "status_vs_last_week": row.get("status_vs_last_week"),
    }


def basis_is_weak(
    basis: str,
    *,
    signatures: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    """Reject topics whose basis relies only on location, a staff record, or one shared word (P2.4)."""
    text = " ".join(str(basis or "").split()).strip()
    if not text:
        return True
    if _STAFF_BASIS.search(text):
        return True
    # One shared word only: tokenize basis and check signature overlap is location-only.
    tokens = {tok for tok in re.findall(r"[a-z0-9]+", text.casefold()) if len(tok) >= 4}
    tokens -= _PLACE_WORDS
    tokens -= _GENERIC_BASIS_WORDS
    folded = text.casefold()
    # Location-only phrasing with no remaining distinctive non-place token.
    if _LOCATION_ONLY_BASIS.search(text) and not _names_shared_mechanism(text) and len(tokens) <= 1:
        return True
    if signatures:
        actors: set[str] = set()
        actions: set[str] = set()
        objects: set[str] = set()
        locations: set[str] = set()
        for sig in signatures:
            if not isinstance(sig, Mapping):
                continue
            actors |= {_norm(item) for item in sig.get("actor") or [] if item}
            objects |= {_norm(item) for item in sig.get("object") or [] if item}
            locations |= {_norm(item) for item in sig.get("location") or [] if item}
            action = sig.get("action_class")
            if action and action != "other":
                actions.add(str(action))
        shared_actor = any(a and a in folded for a in actors)
        shared_object = any(o and o in folded for o in objects)
        loc_norms = {part for loc in locations for part in re.findall(r"[a-z0-9]+", loc) if len(part) >= 3}
        non_place = tokens - loc_norms - _PLACE_WORDS
        # Location-only: no actor/action/object cue in basis or signatures.
        if not non_place and not shared_actor and not shared_object and not actions:
            return True
        if len(non_place) <= 1 and not shared_actor and not shared_object and not actions:
            if locations and not actors and not objects:
                return True
    else:
        if len(tokens) <= 1:
            return True
    return False


def _names_shared_mechanism(text: str) -> bool:
    return bool(re.search(
        r"\b(actor|mechanism|exposure|sanctions?|export.?control|attack|strike|cyber|"
        r"ot\b|compute|chip|ofac|houthi|drone|missile|investment|quota)\b",
        text,
        re.I,
    ))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().casefold())


def is_ambiguous_confidence(confidence: float) -> bool:
    return AMBIGUOUS_LO <= float(confidence) < T_TOPIC


def apply_topic_resolution(
    raw_topics: Sequence[Mapping[str, Any]],
    *,
    elevated_ids: set[str],
    near_ids: set[str],
    prior_keys: set[str],
    signatures_by_event: Mapping[str, Mapping[str, Any]] | None = None,
    run_date: str | None = None,
    critic_verdicts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministic apply (plan P2.4). Returns validated topics, unassigned, themes, rejects.

    Unknown IDs already failed the contract. This step enforces confidence bands, basis rules,
    CONTINUING key presence, and mints NEW keys. Never mutates bands.
    """
    signatures_by_event = signatures_by_event or {}
    critic_verdicts = critic_verdicts or {}
    validated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unassigned: list[str] = []
    seen_elevated: set[str] = set()
    day = str(run_date or "")[:10] or date.today().isoformat()

    for index, topic in enumerate(raw_topics):
        row = dict(topic)
        event_ids = [str(eid) for eid in row.get("event_ids") or []]
        elevated_members = [eid for eid in event_ids if eid in elevated_ids]
        near_members = [eid for eid in event_ids if eid in near_ids]
        # Theme candidates are near-only; handled separately by caller. Skip if no elevated.
        if not elevated_members:
            continue
        for eid in elevated_members:
            seen_elevated.add(eid)
        confidence = float(row.get("confidence") or 0)
        status_vs = str(row.get("status_vs_last_week") or "NEW").upper()
        key = str(row.get("topic_key") or "").strip()
        label = str(row.get("label") or "").strip()
        basis = str(row.get("basis") or "").strip()
        sigs = [signatures_by_event[eid] for eid in elevated_members if eid in signatures_by_event]
        critic = critic_verdicts.get(str(row.get("topic_id") or index)) or critic_verdicts.get(key)

        if status_vs == "CONTINUING" and (not key or key == "NEW" or key not in prior_keys):
            rejected.append({**row, "status": "REJECTED", "reject_reason": "CONTINUING needs an existing prior topic_key"})
            unassigned.extend(elevated_members)
            continue
        if basis_is_weak(basis, signatures=sigs):
            rejected.append({**row, "status": "REJECTED", "reject_reason": "weak basis (location/staff/one word)"})
            unassigned.extend(elevated_members)
            continue

        action = str((critic or {}).get("action") or "").upper()
        if critic and action == "SPLIT":
            rejected.append({**row, "status": "REJECTED", "reject_reason": "TopicCritic SPLIT", "critic_verdict": critic})
            unassigned.extend(elevated_members)
            continue
        if critic and action == "REASSIGN":
            # Critic may supply replacement event_ids / topic_key.
            if critic.get("event_ids"):
                elevated_members = [str(eid) for eid in critic["event_ids"] if str(eid) in elevated_ids]
                event_ids = list(elevated_members) + [eid for eid in event_ids if eid in near_ids]
            if critic.get("topic_key"):
                key = str(critic["topic_key"])
            if critic.get("label"):
                label = str(critic["label"])
            row["basis"] = str(critic.get("basis") or basis)
            confidence = float(critic.get("confidence") or confidence)

        upheld = bool(critic and action == "UPHOLD")
        if confidence < AMBIGUOUS_LO and not upheld:
            rejected.append({**row, "status": "UNASSIGNED", "reject_reason": "confidence below ambiguous band"})
            unassigned.extend(elevated_members)
            continue
        if is_ambiguous_confidence(confidence) and not upheld and critic is None:
            # Caller should have run Critic; treat as still ambiguous → unassigned.
            rejected.append({**row, "status": "UNASSIGNED", "reject_reason": "ambiguous without Critic"})
            unassigned.extend(elevated_members)
            continue
        if confidence < T_TOPIC and not upheld:
            rejected.append({**row, "status": "UNASSIGNED", "reject_reason": "confidence below T_TOPIC"})
            unassigned.extend(elevated_members)
            continue

        if not key or key.upper() == "NEW":
            key = mint_topic_key(label or "topic", first_seen=day)
            status_vs = "NEW"
        elif status_vs == "NEW" and key in prior_keys:
            status_vs = "CONTINUING"

        validated.append({
            "topic_key": key,
            "label": label[:LABEL_CHARS],
            "event_ids": list(dict.fromkeys(elevated_members + near_members)),
            "elevated_event_ids": elevated_members,
            "basis": basis[:BASIS_CHARS],
            "common_ste_path": str(row.get("common_ste_path") or "")[:STE_PATH_CHARS],
            "differences": str(row.get("differences") or "")[:DIFF_CHARS],
            "status_vs_last_week": status_vs if status_vs in STATUSES_VS_LAST else "NEW",
            "confidence": confidence,
            "status": "VALIDATED",
            "critic_verdict": critic,
            # near-threshold members listed but never change band
            "near_event_ids": near_members,
        })

    missing = sorted(elevated_ids - seen_elevated)
    unassigned = list(dict.fromkeys([*unassigned, *missing]))
    return {
        "topics": validated,
        "rejected": rejected,
        "unassigned": unassigned,
    }


def apply_theme_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    near_ids: set[str],
    elevated_ids: set[str],
) -> list[dict[str, Any]]:
    """Validated theme candidates: near-threshold only; never change band (plan P2.4)."""
    out: list[dict[str, Any]] = []
    for row in candidates:
        event_ids = [str(eid) for eid in row.get("event_ids") or []]
        # Drop any elevated IDs — themes must not auto-elevate.
        near_only = [eid for eid in event_ids if eid in near_ids and eid not in elevated_ids]
        if not near_only:
            continue
        confidence = float(row.get("confidence") or 0)
        if confidence < T_TOPIC:
            continue
        basis = str(row.get("basis") or "").strip()
        if basis_is_weak(basis):
            continue
        out.append({
            "event_ids": near_only,
            "basis": basis[:BASIS_CHARS],
            "confidence": confidence,
            "status": "THEME_CANDIDATE",
            "band_unchanged": True,
        })
    return out


# --- P3 render helpers -----------------------------------------------------------------

_STATUS_WORDS = {
    "NEW": "new",
    "CONTINUING": "continuing",
    "ESCALATING": "escalating",
    "EASING": "easing",
}


def format_status_vs_last_week(status: str, *, week: int = 1) -> str:
    """Operator-facing continuity label, e.g. 'Week 3, escalating' (plan P3)."""
    key = str(status or "NEW").strip().upper()
    word = _STATUS_WORDS.get(key, key.casefold() or "new")
    return f"Week {max(1, int(week or 1))}, {word}"


def topic_week_number(
    *,
    topic_key: str,
    run_date: str | None = None,
    prior_run_dates: Sequence[str] | None = None,
) -> int:
    """Continuity week index: distinct prior VALIDATED run_dates for this key + 1, else key first-seen."""
    prior = sorted({str(day)[:10] for day in (prior_run_dates or []) if str(day)[:10]})
    if prior:
        return len(prior) + 1
    # Fallback: mint_topic_key suffix YYYY-MM-DD.
    match = re.search(r"(20\d{2}-\d{2}-\d{2})$", str(topic_key or ""))
    if not match:
        return 1
    try:
        first = date.fromisoformat(match.group(1))
        current = date.fromisoformat(str(run_date or "")[:10]) if run_date else date.today()
    except ValueError:
        return 1
    elapsed = max(0, (current - first).days)
    return max(1, elapsed // 7 + 1)


def worst_reader_band(bands: Sequence[str | None]) -> str:
    """Worst (most severe) reader band among children."""
    from grit.core.overrides import BAND_WATCH, band_sort_key

    cleaned = [str(band) for band in bands if band]
    if not cleaned:
        return BAND_WATCH
    return min(cleaned, key=band_sort_key)


def combined_implication(
    topic: Mapping[str, Any],
    child_rows: Sequence[Mapping[str, Any]],
) -> str:
    """Parent implication: drafted child implications when present, else STE path / differences / basis."""
    parts: list[str] = []
    for row in child_rows:
        drafted = row.get("row") if isinstance(row.get("row"), Mapping) else None
        text = ""
        if drafted and drafted.get("implications"):
            impl = drafted["implications"]
            text = impl.get("plain") if isinstance(impl, Mapping) else str(impl)
        if text and text.strip():
            parts.append(" ".join(text.split()))
    if parts:
        # De-dupe while preserving order; keep the brief short.
        unique = list(dict.fromkeys(parts))
        return " ".join(unique)[:800]
    for key in ("common_ste_path", "differences", "basis"):
        value = str(topic.get(key) or "").strip()
        if value:
            return value[:800]
    return ""


def first_seen_from_key(topic_key: str) -> str | None:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})$", str(topic_key or ""))
    return match.group(1) if match else None
