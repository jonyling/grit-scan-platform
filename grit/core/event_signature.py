"""Event signatures: schema, cue-list fallback, deterministic compare (topic-collapse plan v2 Phase P1).

LLM extraction lives in grit.agents.signature. Comparison and fallback stay deterministic here.
Agents do not write band/tier/override/challenge fields from here.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.core.canonical import canonical_hash
from grit.copilot.search import SAME_STORY_DAYS

CONTRACT = "event-signature-1"
PROMPT_VERSION = "event-signature-1"
SIGNATURE_SOURCE_LLM = "llm"
SIGNATURE_SOURCE_FALLBACK = "fallback"
SIGNATURE_SOURCE_CACHE = "cache"

SIGNATURE_FIELDS = (
    "actor",
    "action_class",
    "action_text",
    "location",
    "date_window",
    "consequence_class",
    "object",
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@lru_cache(maxsize=1)
def signature_actions() -> tuple[dict[str, Any], ...]:
    import json

    path = CONFIG_DIR / "signature_actions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(dict(item) for item in data.get("classes") or [])


@lru_cache(maxsize=1)
def signature_consequences() -> tuple[dict[str, Any], ...]:
    import json

    path = CONFIG_DIR / "signature_consequences.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(dict(item) for item in data.get("classes") or [])


def action_class_ids() -> frozenset[str]:
    return frozenset(str(item["id"]) for item in signature_actions() if item.get("id"))


def consequence_class_ids() -> frozenset[str]:
    return frozenset(str(item["id"]) for item in signature_consequences() if item.get("id"))


def empty_signature(*, source: str = SIGNATURE_SOURCE_FALLBACK) -> dict[str, Any]:
    return {
        "actor": [],
        "action_class": "other",
        "action_text": "",
        "location": [],
        "date_window": [None, None],
        "consequence_class": "none_stated",
        "object": [],
        "signature_source": source,
    }


def anchor_input_payload(anchors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Stable payload for input_hash: anchor titles and texts only (plan P1.1)."""
    rows = []
    for row in anchors:
        rows.append({
            "signal_id": str(row.get("signal_id") or ""),
            "title": str(row.get("title") or ""),
            "text": str(row.get("text") or row.get("excerpt") or "")[:800],
            "published_at": str(row.get("published_at") or "")[:10],
        })
    rows.sort(key=lambda item: item["signal_id"])
    return {"contract": CONTRACT, "anchors": rows}


def signature_input_hash(anchors: Sequence[Mapping[str, Any]]) -> str:
    return canonical_hash(anchor_input_payload(anchors))


def _norm_token(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().casefold())


def _unique_preserve(items: Sequence[str], *, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        cleaned = str(item or "").strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def _overlap(left: Sequence[str], right: Sequence[str]) -> set[str]:
    """Case-folded phrase overlap, including token containment (Houthi ⊂ Houthi rebels)."""
    left_items = [(_norm_token(item), item) for item in left if _norm_token(item)]
    right_items = [(_norm_token(item), item) for item in right if _norm_token(item)]
    found: set[str] = set()
    for lkey, _ in left_items:
        for rkey, _ in right_items:
            if lkey == rkey or lkey in rkey.split() or rkey in lkey.split() or lkey in rkey or rkey in lkey:
                found.add(lkey)
                break
    return found


def _parse_day(value: Any) -> str | None:
    text = str(value or "")[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    return None


def date_windows_within(left: Sequence[Any] | None, right: Sequence[Any] | None,
                        *, days: int = SAME_STORY_DAYS) -> bool:
    """True when the date windows overlap or their nearest edges are within `days`."""
    from datetime import date

    def bounds(window: Sequence[Any] | None) -> tuple[date | None, date | None]:
        if not window or len(window) < 2:
            return None, None
        start, end = _parse_day(window[0]), _parse_day(window[1])
        try:
            a = date.fromisoformat(start) if start else None
            b = date.fromisoformat(end) if end else None
        except ValueError:
            return None, None
        if a and b and b < a:
            a, b = b, a
        return a, b

    a0, a1 = bounds(left)
    b0, b1 = bounds(right)
    if not any((a0, a1, b0, b1)):
        return True  # missing dates do not block actor+action matches
    # Fill missing edge with the known edge.
    a0 = a0 or a1
    a1 = a1 or a0
    b0 = b0 or b1
    b1 = b1 or b0
    if not a0 or not b0:
        return True
    # Interval distance: 0 if overlap.
    if a0 <= b1 and b0 <= a1:
        return True
    gap = (b0 - a1).days if b0 > a1 else (a0 - b1).days
    return gap <= days


def _cue_class(text: str, classes: Sequence[Mapping[str, Any]], *, default: str) -> tuple[str, str]:
    """Return (class_id, matched_cue_phrase) from the first cue hit; else default."""
    haystack = text.casefold()
    best: tuple[int, str, str] | None = None
    for item in classes:
        class_id = str(item.get("id") or "")
        if not class_id or class_id in {"other", "none_stated"}:
            continue
        for cue in item.get("cues") or []:
            phrase = str(cue or "").strip()
            if not phrase:
                continue
            if phrase.casefold() in haystack:
                rank = len(phrase)
                if best is None or rank > best[0]:
                    best = (rank, class_id, phrase)
    if best is None:
        return default, ""
    return best[1], best[2]


def _actor_candidates(text: str) -> list[str]:
    """Heuristic actors: multi-word Proper Names and known country/org tokens from titles."""
    from grit.core.countries import named
    from grit.core.headlines import alias_ids

    found: list[str] = []
    # Alias canonicals first (Plus Ultra etc.).
    for alias in sorted(alias_ids(text)):
        found.append(alias)
    for country in named(text):
        found.append(country)
    # Capitalised multi-word spans (Houthi rebels, Taiwan defence ministry — take first two tokens).
    for match in re.finditer(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\b", text):
        phrase = match.group(1).strip()
        if phrase.casefold() in {item.casefold() for item in found}:
            continue
        # Drop pure place already captured and very generic heads.
        if phrase.casefold() in {"the", "a", "an"}:
            continue
        found.append(phrase)
    return _unique_preserve(found, limit=6)


def _object_candidates(text: str, *, action_cue: str, actors: Sequence[str], locations: Sequence[str]) -> list[str]:
    from grit.core.headlines import distinctive_terms
    from grit.core.identity import place_tokens

    drop = place_tokens() | {_norm_token(item) for item in actors} | {_norm_token(item) for item in locations}
    if action_cue:
        drop |= {_norm_token(part) for part in action_cue.split()}
    terms = [term for term in distinctive_terms(text) if term not in drop]
    return _unique_preserve(terms, limit=6)


def fallback_extract(anchors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Cue-list extractor when the model call fails (plan P1.1). Marked signature_source=fallback."""
    from grit.core.countries import named

    titles = [str(row.get("title") or "") for row in anchors]
    bodies = [str(row.get("text") or row.get("excerpt") or "")[:400] for row in anchors]
    blob = " ".join(f"{title} {body}" for title, body in zip(titles, bodies)).strip()
    title_blob = " ".join(titles)
    action_class, action_cue = _cue_class(blob, signature_actions(), default="other")
    consequence_class, _ = _cue_class(blob, signature_consequences(), default="none_stated")
    actors = _actor_candidates(title_blob or blob)
    locations = _unique_preserve(list(named(blob)), limit=6)
    dates = sorted({d for d in (_parse_day(row.get("published_at")) for row in anchors) if d})
    date_window = [dates[0], dates[-1]] if dates else [None, None]
    action_text = action_cue or (title_blob.split(".")[0][:80].strip() if title_blob else "")
    objects = _object_candidates(title_blob or blob, action_cue=action_cue, actors=actors, locations=locations)
    return {
        "actor": actors,
        "action_class": action_class,
        "action_text": action_text,
        "location": locations,
        "date_window": date_window,
        "consequence_class": consequence_class,
        "object": objects,
        "signature_source": SIGNATURE_SOURCE_FALLBACK,
    }


def normalise_signature(raw: Mapping[str, Any] | None, *, source: str | None = None) -> dict[str, Any]:
    """Clamp a model or fallback payload to the contract shape and closed vocabularies."""
    base = empty_signature(source=source or SIGNATURE_SOURCE_FALLBACK)
    if not isinstance(raw, Mapping):
        return base
    actions = action_class_ids()
    consequences = consequence_class_ids()
    action_class = str(raw.get("action_class") or "other").strip()
    if action_class not in actions:
        action_class = "other"
    consequence_class = str(raw.get("consequence_class") or "none_stated").strip()
    if consequence_class not in consequences:
        consequence_class = "none_stated"
    window = raw.get("date_window")
    if isinstance(window, (list, tuple)) and len(window) >= 2:
        date_window = [_parse_day(window[0]), _parse_day(window[1])]
    else:
        date_window = [None, None]
    out = {
        "actor": _unique_preserve([str(item) for item in (raw.get("actor") or []) if item], limit=8),
        "action_class": action_class,
        "action_text": str(raw.get("action_text") or "").strip()[:120],
        "location": _unique_preserve([str(item) for item in (raw.get("location") or []) if item], limit=8),
        "date_window": date_window,
        "consequence_class": consequence_class,
        "object": _unique_preserve([str(item) for item in (raw.get("object") or []) if item], limit=8),
        "signature_source": str(source or raw.get("signature_source") or SIGNATURE_SOURCE_FALLBACK),
    }
    return out


def signature_for_event(event: Mapping[str, Any], *, vectors: Mapping[str, Any] | None = None,
                        cached: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Prefer attached/cached signature; else cue-list fallback from anchors."""
    if isinstance(event.get("signature"), Mapping):
        return normalise_signature(event["signature"], source=str(event["signature"].get("signature_source") or ""))
    if isinstance(cached, Mapping):
        return normalise_signature(cached, source=SIGNATURE_SOURCE_CACHE)
    from grit.core.identity import anchor_members

    anchors = anchor_members(event, vectors=vectors)
    if not anchors and isinstance(event.get("lead"), Mapping):
        anchors = [event["lead"]]
    return fallback_extract(anchors)


def same_event_candidate(left: Mapping[str, Any], right: Mapping[str, Any], *,
                         same_story_days: int = SAME_STORY_DAYS) -> bool:
    """Deterministic signature compare (plan P1.2).

    True only if:
      - actor overlap ≥1 and action_class equal and date windows within SAME_STORY_DAYS; or
      - object overlap ≥1 and action_class equal.
    Location-only, actor-only and consequence-only overlap is never sufficient.
    action_class "other" never satisfies equality (fallback unknown — avoids country-only bridges).
    """
    a = normalise_signature(left)
    b = normalise_signature(right)
    if a["action_class"] != b["action_class"]:
        return False
    if a["action_class"] in {"", "other"}:
        return False
    actor_hit = bool(_overlap(a["actor"], b["actor"]))
    object_hit = bool(_overlap(a["object"], b["object"]))
    if object_hit:
        return True
    if actor_hit and date_windows_within(a["date_window"], b["date_window"], days=same_story_days):
        return True
    return False


def signature_match_basis(left: Mapping[str, Any], right: Mapping[str, Any], *,
                          same_story_days: int = SAME_STORY_DAYS) -> dict[str, Any]:
    """Explain a same_event_candidate decision (for logs / learning)."""
    a = normalise_signature(left)
    b = normalise_signature(right)
    actors = sorted(_overlap(a["actor"], b["actor"]))
    objects = sorted(_overlap(a["object"], b["object"]))
    locations = sorted(_overlap(a["location"], b["location"]))
    same_action = a["action_class"] == b["action_class"]
    window_ok = date_windows_within(a["date_window"], b["date_window"], days=same_story_days)
    same_consequence = a["consequence_class"] == b["consequence_class"]
    matched = same_event_candidate(a, b, same_story_days=same_story_days)
    return {
        "matched": matched,
        "action_class_equal": same_action,
        "actor_overlap": actors,
        "object_overlap": objects,
        "location_overlap": locations,
        "date_window_within": window_ok,
        "consequence_equal": same_consequence,
        "rule": (
            "object+action" if matched and objects else
            "actor+action+window" if matched and actors else
            "none"
        ),
    }


def signature_near_miss(left: Mapping[str, Any], right: Mapping[str, Any], *,
                        same_story_days: int = SAME_STORY_DAYS) -> dict[str, Any] | None:
    """Exactly one field short of same_event_candidate — topic candidates for P2 (plan P1.3)."""
    if same_event_candidate(left, right, same_story_days=same_story_days):
        return None
    a = normalise_signature(left)
    b = normalise_signature(right)
    actors = sorted(_overlap(a["actor"], b["actor"]))
    objects = sorted(_overlap(a["object"], b["object"]))
    locations = sorted(_overlap(a["location"], b["location"]))
    # "other" never counts as a real shared action for near-miss topic candidates either.
    same_action = (
        a["action_class"] == b["action_class"]
        and a["action_class"] not in {"", "other"}
    )
    window_ok = date_windows_within(a["date_window"], b["date_window"], days=same_story_days)
    same_consequence = a["consequence_class"] == b["consequence_class"]

    # Candidate paths that are one fix away from a match.
    # Path 1: actor+action+window — missing exactly one of {actor, action, window}.
    path1_missing: list[str] = []
    if not actors:
        path1_missing.append("actor")
    if not same_action:
        path1_missing.append("action_class")
    if not window_ok:
        path1_missing.append("date_window")
    # Path 2: object+action — missing exactly one of {object, action}.
    path2_missing: list[str] = []
    if not objects:
        path2_missing.append("object")
    if not same_action:
        path2_missing.append("action_class")

    short: list[str] | None = None
    rule = None
    if len(path1_missing) == 1 and (actors or same_action or window_ok):
        # Require that the other two ingredients are present.
        if path1_missing == ["actor"] and same_action and window_ok:
            short, rule = path1_missing, "actor+action+window"
        elif path1_missing == ["action_class"] and actors and window_ok:
            short, rule = path1_missing, "actor+action+window"
        elif path1_missing == ["date_window"] and actors and same_action:
            short, rule = path1_missing, "actor+action+window"
    if short is None and len(path2_missing) == 1:
        if path2_missing == ["object"] and same_action:
            short, rule = path2_missing, "object+action"
        elif path2_missing == ["action_class"] and objects:
            short, rule = path2_missing, "object+action"
    if short is None:
        return None
    return {
        "near_miss": True,
        "missing_fields": short,
        "rule": rule,
        "actor_overlap": actors,
        "object_overlap": objects,
        "location_overlap": locations,
        "action_class_equal": same_action,
        "date_window_within": window_ok,
        "consequence_equal": same_consequence,
        "note": "Signature near-miss (exactly one field short). Topic candidate for P2; does not merge.",
    }


def location_only_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """True when the only shared signature field is location (never sufficient to merge)."""
    a, b = normalise_signature(left), normalise_signature(right)
    return bool(_overlap(a["location"], b["location"])) and not _overlap(a["actor"], b["actor"]) and not _overlap(
        a["object"], b["object"]
    ) and a["action_class"] != b["action_class"]
