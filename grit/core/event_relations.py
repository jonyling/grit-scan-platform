"""Event relation apply rules: candidate recall, thresholds, union-find for SAME. Pure.

Agents propose labels; this module decides what applies. Never writes band, tier, override, or challenge fields.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from grit.core.canonical import canonical_hash
from grit.core.headlines import NEAR_MISS_LOW, alias_ids, distinctive_terms, identifiers, is_near_miss
from grit.copilot.search import SAME_STORY, SAME_STORY_DAYS

LABELS = frozenset({"SAME", "UPDATE", "CONTRADICTION", "RELATED", "INSUFFICIENT"})
STATUSES = frozenset({"PROPOSED", "VALIDATED", "REJECTED", "SUPERSEDED"})


def enabled(config: Mapping[str, Any] | None) -> bool:
    """Feature flag: run config event_relations=1 (or true/yes)."""
    value = (config or {}).get("event_relations")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
# Apply SAME when confidence meets this, or when Critic upholds.
T_SAME = 0.80
# Ambiguous confidence band for narrow Critic.
AMBIGUOUS_LO, AMBIGUOUS_HI = 0.55, 0.80
# Cost controls (design §7).
EDGES_PER_SIGNAL = 6
LLM_EDGE_BUDGET = 200
EXCERPT_CHARS = 500
REASON_CHARS = 400
QUOTE_CHARS = 120
CONTRACT = "event-relation-1"
PROMPT_VERSION = "event-relation-1"

# Soft near-miss day window for candidate emission (design §4.4 rule 3).
NEAR_MISS_DAYS = 7
# Word-rule day window matches weekly same_event.
WORD_DAYS = 7
WORD_TERMS = 2


def ordered_pair(left: str, right: str) -> tuple[str, str]:
    """Canonical signal pair with left < right."""
    if left == right:
        raise ValueError("A relation needs two distinct signal ids.")
    return (left, right) if left < right else (right, left)


def relation_id_for(run_id: str, left: str, right: str) -> str:
    first, second = ordered_pair(left, right)
    return f"REL-{run_id}-{first}-{second}"


def stable_features(features: Mapping[str, Any] | None) -> dict[str, Any]:
    """Feature subset for input_hash. Drops discovery-only keys (reasons) so prior-relation tags do not bust reuse."""
    raw = dict(features or {})
    raw.pop("reasons", None)
    return raw


def input_hash_for(left: Mapping[str, Any], right: Mapping[str, Any], features: Mapping[str, Any]) -> str:
    """Reuse key over pair excerpts and stable features (P2 daily→weekly; skip re-resolve when unchanged)."""
    return canonical_hash({
        "left": {"signal_id": left.get("signal_id"), "title": left.get("title"), "excerpt": left.get("excerpt")},
        "right": {"signal_id": right.get("signal_id"), "title": right.get("title"), "excerpt": right.get("excerpt")},
        "features": stable_features(features),
    })


def days_apart(first: str | None, second: str | None) -> int | None:
    try:
        a, b = date.fromisoformat(str(first or "")[:10]), date.fromisoformat(str(second or "")[:10])
    except ValueError:
        return None
    return abs((a - b).days)


def auto_same(features: Mapping[str, Any]) -> bool:
    """High-precision zone: cosine ≥ 0.90 and ≥2 terms and same day → SAME without LLM."""
    try:
        cosine = float(features.get("cosine") if features.get("cosine") is not None else -1)
    except (TypeError, ValueError):
        cosine = -1.0
    shared = int(features.get("shared_terms") or 0)
    apart = features.get("days_apart")
    return cosine >= SAME_STORY and shared >= WORD_TERMS and apart == 0


def applies_same(label: str, confidence: float, *, critic_upheld: bool = False) -> bool:
    """Deterministic apply: SAME with confidence ≥ T_SAME, or Critic-upheld SAME."""
    if label != "SAME":
        return False
    return critic_upheld or float(confidence) >= T_SAME


def is_ambiguous(confidence: float, *, label: str | None = None, merges_elevated: bool = False) -> bool:
    """Narrow Critic: mid-band confidence, or SAME that would merge two elevated leads."""
    score = float(confidence)
    if AMBIGUOUS_LO <= score <= AMBIGUOUS_HI:
        return True
    return bool(merges_elevated and label == "SAME")


def union_find(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Map each id to its root under SAME unions. Roots are the lexicographically smaller seed."""
    parent: dict[str, str] = {}

    def root(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for left, right in pairs:
        a, b = root(left), root(right)
        if a == b:
            continue
        if a < b:
            parent[b] = a
        else:
            parent[a] = b
    return {key: root(key) for key in parent}


def same_groups(pairs: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    """Root → sorted member ids for applied SAME pairs."""
    roots = union_find(pairs)
    groups: dict[str, list[str]] = {}
    for key, root in roots.items():
        groups.setdefault(root, []).append(key)
    return {root: sorted(members) for root, members in groups.items() if len(members) > 1}


def validated_same_pairs(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """Applied SAME edges from standing (latest) validated/proposed-upheld rows."""
    found: list[tuple[str, str]] = []
    for row in rows:
        if row.get("status") not in {"VALIDATED"}:
            continue
        if not applies_same(str(row.get("label") or ""), float(row.get("confidence") or 0),
                            critic_upheld=bool((row.get("evidence") or {}).get("critic_upheld"))):
            continue
        left, right = str(row["left_signal_id"]), str(row["right_signal_id"])
        found.append(ordered_pair(left, right))
    return found


def related_peers(rows: Sequence[Mapping[str, Any]], signal_id: str) -> list[str]:
    """Signal ids linked by VALIDATED RELATED or applied SAME to signal_id."""
    peers: list[str] = []
    for row in rows:
        if row.get("status") != "VALIDATED":
            continue
        label = str(row.get("label") or "")
        left, right = str(row["left_signal_id"]), str(row["right_signal_id"])
        if signal_id not in {left, right}:
            continue
        if label == "RELATED" or applies_same(label, float(row.get("confidence") or 0),
                                               critic_upheld=bool((row.get("evidence") or {}).get("critic_upheld"))):
            peers.append(right if signal_id == left else left)
    return sorted(set(peers))


def contradiction_pairs(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """VALIDATED CONTRADICTION edges for Researcher CONTRADICT seeding."""
    found: list[tuple[str, str]] = []
    for row in rows:
        if row.get("status") != "VALIDATED" or str(row.get("label") or "") != "CONTRADICTION":
            continue
        found.append(ordered_pair(str(row["left_signal_id"]), str(row["right_signal_id"])))
    return found


def score_edge(features: Mapping[str, Any]) -> float:
    """Higher = more worth resolving. Prefer high cosine, shared ids/terms, same cluster."""
    cosine = float(features.get("cosine") or 0)
    shared_terms = int(features.get("shared_terms") or 0)
    shared_ids = int(features.get("shared_identifiers") or 0)
    cluster = 1.0 if features.get("same_cluster") else 0.0
    alias = 1.0 if features.get("shared_aliases") else 0.0
    return cosine + 0.05 * shared_terms + 0.15 * shared_ids + 0.2 * cluster + 0.15 * alias


def candidate_edges(
    signals: Sequence[Mapping[str, Any]],
    *,
    clusters: Mapping[str, str] | None = None,
    cosines: Mapping[tuple[str, str], float] | None = None,
    prior_relations: Sequence[Mapping[str, Any]] = (),
    citation_pairs: Sequence[tuple[str, str]] = (),
    per_signal: int = EDGES_PER_SIGNAL,
    budget: int = LLM_EDGE_BUDGET,
) -> list[dict[str, Any]]:
    """Deterministic recall: emit edges when any design §4.4 rule holds. Caps per signal and globally."""
    clusters = clusters or {}
    cosines = cosines or {}
    by_id = {str(row["signal_id"]): row for row in signals if row.get("signal_id")}
    ids = sorted(by_id)
    terms = {key: distinctive_terms(str(by_id[key].get("title") or "")) for key in ids}
    aliases = {key: alias_ids(str(by_id[key].get("title") or "")) for key in ids}
    idents = {key: identifiers(str(by_id[key].get("title") or "")) for key in ids}
    days = {key: str(by_id[key].get("published_at") or "")[:10] or None for key in ids}

    prior_keys = {
        ordered_pair(str(row["left_signal_id"]), str(row["right_signal_id"]))
        for row in prior_relations
        if str(row.get("label") or "") in {"SAME", "UPDATE"} and row.get("status") == "VALIDATED"
    }
    cited = {ordered_pair(a, b) for a, b in citation_pairs if a in by_id and b in by_id and a != b}

    raw: dict[tuple[str, str], dict[str, Any]] = {}

    def remember(left: str, right: str, *, reason: str, **extra: Any) -> None:
        pair = ordered_pair(left, right)
        shared_terms = len(terms[pair[0]] & terms[pair[1]])
        shared_idents = len(idents[pair[0]] & idents[pair[1]])
        shared_alias = sorted(aliases[pair[0]] & aliases[pair[1]])
        cosine = cosines.get(pair)
        if cosine is None:
            cosine = cosines.get((pair[1], pair[0]))
        features = {
            "cosine": round(float(cosine), 4) if cosine is not None else None,
            "shared_terms": shared_terms,
            "shared_identifiers": shared_idents,
            "shared_aliases": shared_alias,
            "same_cluster": bool(clusters.get(pair[0]) and clusters.get(pair[0]) == clusters.get(pair[1])),
            "days_apart": days_apart(days[pair[0]], days[pair[1]]),
            "reasons": [reason],
            **extra,
        }
        existing = raw.get(pair)
        if existing:
            existing["features"]["reasons"] = sorted(set(existing["features"]["reasons"]) | {reason})
            for key, value in features.items():
                if key == "reasons":
                    continue
                if existing["features"].get(key) in (None, [], 0) and value not in (None, [], 0):
                    existing["features"][key] = value
            existing["score"] = score_edge(existing["features"])
            return
        raw[pair] = {
            "left_signal_id": pair[0], "right_signal_id": pair[1],
            "left_event_id": clusters.get(pair[0]), "right_event_id": clusters.get(pair[1]),
            "features": features, "score": score_edge(features),
        }

    # 1. Same event_members cluster
    by_cluster: dict[str, list[str]] = {}
    for key, event_id in clusters.items():
        if key in by_id and event_id:
            by_cluster.setdefault(event_id, []).append(key)
    for members in by_cluster.values():
        for i, left in enumerate(sorted(members)):
            for right in sorted(members)[i + 1:]:
                remember(left, right, reason="cluster")

    # 2–5. Pairwise feature rules (no all-pairs LLM; still O(n²) feature checks, capped later)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            pair = (left, right)
            cosine = cosines.get(pair)
            apart = days_apart(days[left], days[right])
            shared_terms = len(terms[left] & terms[right])
            shared_idents = len(idents[left] & idents[right])
            shared_alias = bool(aliases[left] & aliases[right])
            if cosine is not None and apart is not None and apart <= SAME_STORY_DAYS and cosine >= SAME_STORY:
                remember(left, right, reason="cosine_same")
            if cosine is not None and apart is not None and apart <= NEAR_MISS_DAYS and is_near_miss(
                    cosine, shared_terms=shared_terms, shared_identifiers=shared_idents, high=SAME_STORY):
                remember(left, right, reason="near_miss")
            if shared_terms >= WORD_TERMS and (apart is None or apart <= WORD_DAYS):
                remember(left, right, reason="word_rule")
            if shared_idents or shared_alias:
                remember(left, right, reason="identifier" if shared_idents else "alias")
            if pair in prior_keys:
                remember(left, right, reason="prior_relation")
            if pair in cited:
                remember(left, right, reason="citation")

    # Cap: top K per signal, then global budget by score
    per: dict[str, list[dict[str, Any]]] = {key: [] for key in ids}
    for edge in sorted(raw.values(), key=lambda item: (-item["score"], item["left_signal_id"], item["right_signal_id"])):
        left, right = edge["left_signal_id"], edge["right_signal_id"]
        if len(per[left]) < per_signal:
            per[left].append(edge)
        if len(per[right]) < per_signal and edge not in per[right]:
            per[right].append(edge)
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for edges in per.values():
        for edge in edges:
            chosen[ordered_pair(edge["left_signal_id"], edge["right_signal_id"])] = edge
    ranked = sorted(chosen.values(), key=lambda item: (-item["score"], item["left_signal_id"], item["right_signal_id"]))
    return ranked[:budget]


def needs_llm(edge: Mapping[str, Any]) -> bool:
    """False when auto-SAME applies; those skip the model."""
    return not auto_same(edge.get("features") or {})


def pack_candidate(edge: Mapping[str, Any], signals: Mapping[str, Mapping[str, Any]], *, edge_id: str) -> dict[str, Any]:
    """Resolver payload edge with short excerpts."""
    left_id, right_id = edge["left_signal_id"], edge["right_signal_id"]
    left, right = signals[left_id], signals[right_id]

    def view(row: Mapping[str, Any], event_id: str | None) -> dict[str, Any]:
        text = " ".join(str(row.get("text") or "").split())
        return {
            "signal_id": row["signal_id"], "title": str(row.get("title") or "")[:200],
            "excerpt": text[:EXCERPT_CHARS], "published_at": str(row.get("published_at") or "")[:10] or None,
            "event_id": event_id,
        }

    return {
        "edge_id": edge_id,
        "left": view(left, edge.get("left_event_id")),
        "right": view(right, edge.get("right_event_id")),
        "features": dict(edge.get("features") or {}),
    }
