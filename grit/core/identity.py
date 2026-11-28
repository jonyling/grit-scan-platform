"""Event identity eligibility, anchors, and cluster guards (topic-collapse plan v2 Phase P0).

Deterministic. Agents do not write band/tier/override/challenge fields from here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Mapping, Sequence

# Cluster guard thresholds (plan P0.4; calibrate on Phase E).
MAX_EVENT_MEMBERS = 25
MAX_EVENT_COUNTRIES = 3
# Anchor: lead + up to this many medoids.
ANCHOR_MEDOIDS = 2
# Per-event internal evidence cap for Assessment batches (P0.6).
INTERNAL_PER_EVENT = 5

IDENTITY_SUSPECT = "IDENTITY_SUSPECT"


def is_identity_eligible(signal: Mapping[str, Any] | None) -> bool:
    """False for approved internal / staff evidence that must never join or bridge events.

    Checks lane, approved-evidence URN, APPROVED_INTERNAL scope on evidence records, and
    `internal_exposure` annotations. Public news that merely *names* staff countries stays eligible.
    """
    if not isinstance(signal, Mapping):
        return False
    lane = str(signal.get("lane") or "").strip().upper()
    if lane == "HUMAN_APPROVED":
        return False
    url = str(signal.get("url") or "")
    if url.startswith("urn:grit:approved-evidence:"):
        return False
    if signal.get("internal_exposure"):
        return False
    for entry in signal.get("evidence_records") or []:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("scope") or "").strip().upper() == "APPROVED_INTERNAL":
            return False
        if str(entry.get("kind") or "").strip().upper() == "HUMAN_APPROVED":
            return False
    return True


def filter_identity_eligible(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Keep only identity-eligible signals, preserving order."""
    return [row for row in rows if is_identity_eligible(row)]


@lru_cache(maxsize=1)
def place_tokens() -> frozenset[str]:
    """Distinctive tokens that name places after headline folding. Never count toward word thresholds."""
    from grit.core.countries import COUNTRIES
    from grit.core.headlines import PLACES, distinctive_terms

    tokens: set[str] = set()
    for _phrase, token in PLACES:
        cleaned = str(token or "").strip()
        if cleaned:
            tokens.add(cleaned)
            tokens |= set(distinctive_terms(cleaned))
    for name, aliases in COUNTRIES.items():
        for part in (name, *aliases):
            tokens |= set(distinctive_terms(part))
    return frozenset(tokens)


def non_place_terms(text: str) -> frozenset[str]:
    """Distinctive headline terms with place-folded country/city tokens removed."""
    from grit.core.headlines import distinctive_terms

    return frozenset(distinctive_terms(text) - place_tokens())


def shared_non_place_terms(left: str, right: str) -> frozenset[str]:
    return non_place_terms(left) & non_place_terms(right)


def curated_overlap(left: str, right: str) -> bool:
    """True when headlines share an R&A entity alias or a mixed letter-digit identifier."""
    from grit.core.headlines import alias_ids, identifiers

    return bool(alias_ids(left) & alias_ids(right) or identifiers(left) & identifiers(right))


def word_join_allowed(left: str, right: str, *, min_terms: int = 2) -> bool:
    """Flag-off word join: ≥min_terms shared non-place distinctive terms, or curated alias/id."""
    if curated_overlap(left, right):
        return True
    return len(shared_non_place_terms(left, right)) >= min_terms


def _member_day(row: Mapping[str, Any]) -> str | None:
    text = str(row.get("published_at") or "")[:10]
    return text or None


def _within_days(first: Mapping[str, Any], second: Mapping[str, Any], days: int) -> bool:
    from datetime import date

    a, b = _member_day(first), _member_day(second)
    if not a or not b:
        return True
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days
    except ValueError:
        return True


def event_centroid_vector(members: Sequence[Mapping[str, Any]],
                          vectors: Mapping[str, Any] | None) -> Any | None:
    """Mean of member embedding vectors, or None when unavailable."""
    if not vectors or not members:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    found = []
    for row in members:
        vec = vectors.get(row["signal_id"])
        if vec is not None:
            found.append(np.asarray(vec, dtype=np.float32))
    if not found:
        return None
    centroid = sum(found) / len(found)
    norm = float(np.linalg.norm(centroid))
    if norm <= 0:
        return None
    return centroid / norm


def medoid_members(members: Sequence[Mapping[str, Any]], *, limit: int = ANCHOR_MEDOIDS,
                   vectors: Mapping[str, Any] | None = None,
                   exclude: set[str] | None = None) -> list[Mapping[str, Any]]:
    """Up to `limit` members closest to the event centroid (embedding), else term-overlap medoids."""
    exclude = exclude or set()
    candidates = [row for row in members if row.get("signal_id") not in exclude]
    if not candidates or limit <= 0:
        return []
    centroid = event_centroid_vector(members, vectors)
    if centroid is not None and vectors:
        import numpy as np

        ranked = []
        for row in candidates:
            vec = vectors.get(row["signal_id"])
            if vec is None:
                continue
            ranked.append((float(np.asarray(vec, dtype=np.float32) @ centroid), row))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("signal_id"))))
        if ranked:
            return [row for _score, row in ranked[:limit]]
    # Fallback: members whose distinctive terms overlap most with the lead (proxy for centroid).
    from grit.core.headlines import distinctive_terms

    bags = {row["signal_id"]: distinctive_terms(str(row.get("title") or "")) for row in members}
    # anchor_members passes exclude={lead_id}; centre on that lead's terms when available.
    center_id = next(iter(exclude), None) if exclude else None
    if center_id not in bags:
        center_id = members[0]["signal_id"]
    center = bags[center_id]

    def jaccard(row: Mapping[str, Any]) -> float:
        bag = bags.get(row["signal_id"], frozenset())
        union = bag | center
        return (len(bag & center) / len(union)) if union else 0.0

    ranked = sorted(candidates, key=lambda row: (-jaccard(row), str(row.get("signal_id"))))
    return ranked[:limit]


def anchor_members(event: Mapping[str, Any], *, vectors: Mapping[str, Any] | None = None,
                   medoids: int = ANCHOR_MEDOIDS) -> list[Mapping[str, Any]]:
    """Lead report plus up to `medoids` most-central members. Deduped, lead first."""
    members = list(event.get("members") or [])
    if not members:
        lead = event.get("lead")
        return [lead] if isinstance(lead, Mapping) and lead.get("signal_id") else []
    lead = event.get("lead")
    if not isinstance(lead, Mapping) or lead.get("signal_id") not in {row["signal_id"] for row in members}:
        lead = members[0]
    chosen = [lead]
    for row in medoid_members(members, limit=medoids, vectors=vectors, exclude={lead["signal_id"]}):
        if row["signal_id"] not in {item["signal_id"] for item in chosen}:
            chosen.append(row)
    return chosen


def anchor_terms(event: Mapping[str, Any], *, vectors: Mapping[str, Any] | None = None) -> frozenset[str]:
    """Distinctive terms from anchor titles only (P0.5)."""
    from grit.core.headlines import distinctive_terms

    text = " ".join(str(row.get("title") or "") for row in anchor_members(event, vectors=vectors))
    return distinctive_terms(text)


def anchor_aliases(event: Mapping[str, Any], *, vectors: Mapping[str, Any] | None = None) -> frozenset[str]:
    from grit.core.headlines import alias_ids

    text = " ".join(str(row.get("title") or "") for row in anchor_members(event, vectors=vectors))
    return alias_ids(text)


def anchor_text(event: Mapping[str, Any], *, vectors: Mapping[str, Any] | None = None) -> str:
    return " ".join(str(row.get("title") or "") for row in anchor_members(event, vectors=vectors))


def anchor_signal_ids(event: Mapping[str, Any], *, vectors: Mapping[str, Any] | None = None) -> set[str]:
    return {str(row["signal_id"]) for row in anchor_members(event, vectors=vectors) if row.get("signal_id")}


def matches_anchor(candidate: Mapping[str, Any], event: Mapping[str, Any], *,
                   same_pairs: Sequence[tuple[str, str]] | None = None,
                   vectors: Mapping[str, Any] | None = None,
                   min_terms: int = 2,
                   same_story: float | None = None,
                   same_story_days: int | None = None) -> bool:
    """True when candidate matches the event anchor (SAME edge, curated id/alias, terms, or cosine)."""
    from grit.copilot.search import SAME_STORY, SAME_STORY_DAYS
    from grit.core.headlines import alias_ids, distinctive_terms, identifiers

    threshold = SAME_STORY if same_story is None else same_story
    day_window = SAME_STORY_DAYS if same_story_days is None else same_story_days
    anchors = anchor_members(event, vectors=vectors)
    if not anchors:
        return False
    cand_id = str(candidate.get("signal_id") or "")
    linked = set(same_pairs or [])
    for row in anchors:
        aid = str(row.get("signal_id") or "")
        if not aid or not cand_id or aid == cand_id:
            continue
        pair = (aid, cand_id) if aid < cand_id else (cand_id, aid)
        if pair in linked:
            return True
    cand_title = str(candidate.get("title") or "")
    for row in anchors:
        other = str(row.get("title") or "")
        if curated_overlap(cand_title, other):
            return True
        shared = distinctive_terms(cand_title) & distinctive_terms(other)
        shared -= place_tokens()
        if len(shared) >= min_terms and _within_days(candidate, row, day_window):
            return True
        if vectors and cand_id in vectors and row.get("signal_id") in vectors:
            try:
                import numpy as np

                left = np.asarray(vectors[cand_id], dtype=np.float32)
                right = np.asarray(vectors[row["signal_id"]], dtype=np.float32)
                if float(left @ right) >= threshold and _within_days(candidate, row, day_window):
                    return True
            except Exception:
                pass
    # Also accept alias/id overlap against the combined anchor text (Plus Ultra short forms).
    anchor = anchor_text(event, vectors=vectors)
    if alias_ids(cand_title) & alias_ids(anchor) or identifiers(cand_title) & identifiers(anchor):
        return True
    return False


def anchors_match(first: Mapping[str, Any], second: Mapping[str, Any], *,
                  same_pairs: Sequence[tuple[str, str]] | None = None,
                  vectors: Mapping[str, Any] | None = None,
                  min_terms: int = 2) -> bool:
    """Two events merge only when an anchor member of one matches the other's anchor."""
    for row in anchor_members(first, vectors=vectors):
        if matches_anchor(row, second, same_pairs=same_pairs, vectors=vectors, min_terms=min_terms):
            return True
    for row in anchor_members(second, vectors=vectors):
        if matches_anchor(row, first, same_pairs=same_pairs, vectors=vectors, min_terms=min_terms):
            return True
    return False


def event_country_count(event: Mapping[str, Any]) -> int:
    from grit.core.countries import named

    members = list(event.get("members") or [])
    blob = " ".join(f"{row.get('title') or ''} {row.get('text') or ''}" for row in members)
    return len(named(blob))


def is_identity_suspect(event: Mapping[str, Any], *,
                        max_members: int = MAX_EVENT_MEMBERS,
                        max_countries: int = MAX_EVENT_COUNTRIES) -> bool:
    members = list(event.get("members") or [])
    size = len(members) or len(event.get("signal_ids") or [])
    return size > max_members or event_country_count(event) > max_countries


def split_suspect_to_premerge(event: Mapping[str, Any],
                              premerge: Sequence[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Deterministic split for IDENTITY_SUSPECT when RelationCritic cannot emit partitions.

    Restores pre-merge event partitions when provided; otherwise keeps the lead/anchor as one
    event and peels remaining members into singleton events keyed by their signal id.
    """
    parts = [dict(item) for item in (premerge or []) if item.get("members")]
    if len(parts) >= 2:
        restored = []
        for part in parts:
            row = dict(part)
            row["identity_status"] = IDENTITY_SUSPECT
            row["identity_split"] = "premerge"
            restored.append(row)
        return restored
    members = list(event.get("members") or [])
    if len(members) <= 1:
        row = dict(event)
        row["identity_status"] = IDENTITY_SUSPECT
        return [row]
    anchors = {row["signal_id"] for row in anchor_members(event)}
    keep = [row for row in members if row["signal_id"] in anchors]
    peeled = [row for row in members if row["signal_id"] not in anchors]
    from grit.core.headlines import distinctive_terms
    from grit.core.priority import outlet_groups

    def as_event(seed: Mapping[str, Any], group: list[Mapping[str, Any]], event_id: str) -> dict[str, Any]:
        dates = sorted(str(row.get("published_at") or "")[:10] for row in group if row.get("published_at"))
        return {
            **{key: seed.get(key) for key in seed if key not in {
                "members", "reports", "signal_ids", "lead", "event_id", "publishers",
                "first_date", "last_date", "merged_events", "cited", "identity_status", "identity_split",
            }},
            "event_id": event_id,
            "lead": group[0],
            "members": group,
            "reports": group,
            "signal_ids": [row["signal_id"] for row in group],
            "publishers": len(outlet_groups([str(row.get("publisher") or row.get("url") or "") for row in group])),
            "first_date": dates[0] if dates else seed.get("first_date"),
            "last_date": dates[-1] if dates else seed.get("last_date"),
            "merged_events": [event_id],
            "identity_status": IDENTITY_SUSPECT,
            "identity_split": "anchor_partition",
            "anchor_terms": sorted(distinctive_terms(str(group[0].get("title") or ""))),
        }

    out = [as_event(event, keep, str(event.get("event_id")))]
    for row in peeled:
        sid = str(row["signal_id"])
        out.append(as_event(event, [row], f"EVT-{sid[4:]}" if sid.startswith("SIG-") else f"EVT-{sid}"))
    return out


def select_internal_evidence(
    batch: Sequence[str],
    sources: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = INTERNAL_PER_EVENT,
) -> list[str]:
    """Pick internal evidence rows that match the batch's exposure graph (P0.6).

    Match on named entity, country-of-operation, or exposure type entity string appearing in
    batch titles/text. Cap at `limit`. Order is stable by signal id.
    """
    from grit.core.countries import named

    batch_rows = [sources[key] for key in batch if key in sources]
    if not batch_rows:
        return []
    blob = " ".join(
        f"{row.get('title') or ''} {row.get('text') or ''} {row.get('excerpt') or ''}"
        for row in batch_rows
    )
    countries = {item.casefold() for item in named(blob)}
    haystack = blob.casefold()
    scored: list[tuple[int, str]] = []
    for key, row in sources.items():
        if key in batch or not row.get("internal_exposure"):
            continue
        score = 0
        for entry in row.get("internal_exposure") or []:
            if not isinstance(entry, Mapping):
                continue
            entity = str(entry.get("entity") or "").strip()
            if entity and entity.casefold() in haystack:
                score += 3
            # Country cues from the internal title/text.
            own = f"{row.get('title') or ''} {row.get('text') or ''}"
            own_countries = {item.casefold() for item in named(own)}
            if own_countries & countries:
                score += 2
            if entry.get("type") in {"SITE", "TRAVEL", "CONTRACT", "OTHER"} and score == 0:
                # Weak presence: same country token in titles only.
                if own_countries & countries:
                    score += 1
        if score > 0:
            scored.append((score, key))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [key for _score, key in scored[:limit]]
