"""Hybrid search over the signal pool for the Co-Pilot (spec 40, section 3). No model, apart from the query embedding.

1. Scope first: the reader's packs, the time window, kept signals only unless asked, and completed runs.
2. Two ranked lists: SQLite FTS5 (BM25) for exact names, acronyms and figures, and cosine similarity over the stored
   signal vectors for paraphrase. Reciprocal rank fusion combines them, with no weights to tune.
3. Event collapse: pages that tell the same story (cosine 0.90 or more within 8 days, or the same title) become one event
   that keeps all its outlets.
4. The reader's aperture decides what is shown; matches outside it are counted, not hidden silently. The reader's profile
   then orders the events within a tier, as on the dashboard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable, Mapping, Sequence

from grit.core.routing import aperture_routing
from grit.pool import Pool
from grit.profiles.emphasis import profile_effect

RRF_K = 60
FUSED = 80  # fused hits considered before collapse
SAME_STORY = 0.90
SAME_STORY_DAYS = 8
LEAD = 700
_TERM = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,}|\d[\d.,]*\d|\d")
_STOP = frozenset(
    "the and for with what this that are was were has have any about which from into does how why who when week weekly there their "
    "they them our ste st engineering tell me show give is it its of on in to a an as at be by or do did can could should would will "
    "us we you your latest news last month months year since been being than then also more most".split()
)
_VECTOR_CACHE: dict[str, Any] = {}


def ensure_fts(pool: Pool) -> int:
    """Index every signal not yet in the full-text table. Safe to repeat. Returns how many were added."""
    with pool.transaction() as conn:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS signal_fts USING fts5(signal_id UNINDEXED, title, text, tokenize = 'porter unicode61')")
        added = conn.execute(
            "INSERT INTO signal_fts (signal_id, title, text) SELECT s.signal_id, s.title, s.text FROM signals s "
            "WHERE s.signal_id NOT IN (SELECT signal_id FROM signal_fts)"
        ).rowcount
    return max(0, added or 0)


def fts_query(question: str) -> str | None:
    terms = []
    for word in _TERM.findall(question or ""):
        folded = word.casefold().strip("-")
        if len(folded) < 2 or folded in _STOP or (len(folded) < 3 and not word.isupper()):
            continue
        quoted = '"' + word.replace('"', "") + '"'
        if quoted not in terms:
            terms.append(quoted)
    return " OR ".join(terms[:16]) or None


def candidates(pool: Pool, scope: Any, *, include_dropped: bool = False) -> dict[str, dict[str, Any]]:
    """One row per signal in scope: its best assessment (lowest tier, then the latest run), with every pack it was kept in."""
    if not scope.packs:
        return {}
    marks = ", ".join("?" for _ in scope.packs)
    where = [f"a.pack IN ({marks})", "r.status = 'COMPLETE'"]
    params: list[Any] = list(scope.packs)
    if not include_dropped:
        where.append("a.decision = 'KEEP'")
    if scope.window == "run" and scope.run_id:
        where.append("a.run_id = ?")
        params.append(scope.run_id)
    elif scope.since:
        where.append("r.run_date >= ?")
        params.append(scope.since)
    rows = pool.rows(
        f"""SELECT a.*, r.run_date, s.title, s.url, s.publisher, s.published_at, s.text, s.lane, m.event_id
            FROM assessments a JOIN runs r ON r.run_id = a.run_id JOIN signals s ON s.signal_id = a.signal_id
            LEFT JOIN event_members m ON m.signal_id = a.signal_id
            WHERE {' AND '.join(where)}""",
        params,
    )
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        signal_id = row["signal_id"]
        packs = sorted(set((best.get(signal_id) or {}).get("packs") or []) | {row["pack"]})
        held = best.get(signal_id)
        if held is None or ((row.get("tier") or 9), -_ordinal(row["run_date"])) < ((held.get("tier") or 9), -_ordinal(held["run_date"])):
            best[signal_id] = row
        best[signal_id]["packs"] = packs
    return best


def _ordinal(text: str | None) -> int:
    try:
        return date.fromisoformat(str(text)[:10]).toordinal()
    except ValueError:
        return 0


def _vectors(pool: Pool, ids: Iterable[str]) -> dict[str, Any]:
    import numpy as np

    from grit.retrieval.embedder import DEFAULT_DIMENSIONS, DEFAULT_MODEL

    wanted = [item for item in ids if item not in _VECTOR_CACHE]
    for signal_id, vector in pool.vectors(wanted, model=DEFAULT_MODEL, task="RETRIEVAL_DOCUMENT", dimensions=DEFAULT_DIMENSIONS).items():
        _VECTOR_CACHE[signal_id] = np.asarray(vector, dtype=np.float32)
    return {item: _VECTOR_CACHE[item] for item in ids if item in _VECTOR_CACHE}


def _fts_ranking(pool: Pool, question: str, allowed: set[str]) -> list[str]:
    query = fts_query(question)
    if not query:
        return []
    ensure_fts(pool)
    try:
        rows = pool.rows("SELECT signal_id FROM signal_fts WHERE signal_fts MATCH ? ORDER BY bm25(signal_fts, 0.0, 3.0, 1.0) LIMIT 2000", (query,))
    except Exception:
        return []
    return [row["signal_id"] for row in rows if row["signal_id"] in allowed]


def _vector_ranking(query_vector: Sequence[float] | None, vectors: Mapping[str, Any]) -> list[str]:
    if query_vector is None or not vectors:
        return []
    import numpy as np

    ids = list(vectors)
    matrix = np.vstack([vectors[item] for item in ids])
    scores = matrix @ np.asarray(query_vector, dtype=np.float32)
    order = np.argsort(-scores, kind="stable")
    return [ids[number] for number in order[:400]]


def fuse(*rankings: Sequence[str]) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: 1 / (60 + rank) summed over the lists. Ties keep the first list's order."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank)
    first = {item: number for number, item in enumerate(next((ranking for ranking in rankings if ranking), []))}
    return sorted(scores.items(), key=lambda pair: (-pair[1], first.get(pair[0], len(first))))


@dataclass
class Event:
    row: dict[str, Any]
    score: float
    rank: int
    members: list[dict[str, Any]] = field(default_factory=list)

    @property
    def outlets(self) -> list[str]:
        return list(dict.fromkeys(str(member.get("publisher") or "") for member in [self.row, *self.members] if member.get("publisher")))

    @property
    def dates(self) -> tuple[str | None, str | None]:
        found = sorted(str(member.get("published_at"))[:10] for member in [self.row, *self.members] if member.get("published_at"))
        return (found[0], found[-1]) if found else (None, None)


def _title_key(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip()


def collapse(fused: Sequence[tuple[str, float]], rows: Mapping[str, Mapping[str, Any]], vectors: Mapping[str, Any]) -> list[Event]:
    """Join a page to an earlier, better-ranked event when it tells the same story: the nightly index put both in one
    event, or they share a title, or their vectors are 0.90 or more similar within 8 days."""
    import numpy as np

    events: list[Event] = []
    for rank, (signal_id, score) in enumerate(fused, start=1):
        row = dict(rows[signal_id])
        joined = None
        for event in events:
            same_title = _title_key(row.get("title")) and _title_key(row.get("title")) == _title_key(event.row.get("title"))
            same_event = bool(row.get("event_id")) and any(row["event_id"] == member.get("event_id") for member in [event.row, *event.members])
            near = same_event
            if not near and signal_id in vectors and event.row["signal_id"] in vectors:
                close_in_time = abs(_ordinal(row.get("published_at")) - _ordinal(event.row.get("published_at"))) <= SAME_STORY_DAYS
                near = close_in_time and float(np.dot(vectors[signal_id], vectors[event.row["signal_id"]])) >= SAME_STORY
            if same_title or near:
                joined = event
                break
        if joined:
            joined.members.append(row)
        else:
            events.append(Event(row, score, rank))
    return events


@dataclass
class SearchResult:
    events: list[Event]
    matched: int  # events that matched before the aperture
    outside_aperture: int
    in_scope: int  # signals in scope
    runs: int


def search(
    pool: Pool, scope: Any, question: str, *, embed: Callable[[str], Sequence[float]] | None = None, limit: int = 12,
    include_dropped: bool = False, requests: Sequence[Mapping[str, Any]] | None = None,
) -> SearchResult:
    rows = candidates(pool, scope, include_dropped=include_dropped)
    runs = len({row["run_id"] for row in rows.values()})
    if not rows:
        return SearchResult([], 0, 0, 0, runs)
    vectors = _vectors(pool, rows)
    query_vector = None
    if embed is not None and vectors:
        try:
            query_vector = embed(question)
        except Exception:
            query_vector = None
    fused = fuse(_fts_ranking(pool, question, set(rows)), _vector_ranking(query_vector, vectors))[:FUSED]
    events = collapse(fused, rows, vectors)
    requests = list(requests if requests is not None else pool.management_requests(active_only=True))
    profile = scope.profile
    shown: list[Event] = []
    outside = 0
    for event in events:
        route = aperture_routing(event.row, aperture=profile.aperture, packs=scope.packs, requests=requests)
        if not route.include and not include_dropped:
            outside += 1
            continue
        protected = bool(event.row.get("red")) or route.priority == "P1"
        event.row["route"] = route.as_record()
        event.row["preference"] = profile_effect(event.row, profile, protected=protected).as_record()
        shown.append(event)
    top = shown[:limit]
    top.sort(key=lambda event: ((event.row.get("tier") or 9), -event.row["preference"]["affinity"], event.rank))
    return SearchResult(top, len(events), outside, len(rows), runs)
