"""Keep rule for one run's candidates. Pure: it ranks and drops with a logged reason, and it never scores harm.

Order of the rule:
1. A page older than the window is stale. A missing date is not stale. A market-research listing or a ranked list reports no event.
2. A linked candidate stays. A candidate with a strong KEY theme hit stays for the next steps. Any other unlinked one stays only if it ranks near the top of a query that found it.
3. Near-duplicates collapse to the best copy, which records the outlets that carried the story. A candidate that could be big or direct also
   absorbs pages from other outlets at the same-event cosine, so one event is one finding.
4. Each survivor gets a briefing priority (grit_scan/priority.py). A big event with no register link is promoted to the THEME rung.
   An unlinked survivor that is not big must still rank near the top of a query that found it.
5. The order is tier, best theme tier, cues met, rung, outlets, topic similarity, a registered source, recency, and URL.
6. Each group is capped. The unbound group also keeps one slot per event. A candidate that is cut is logged, not forgotten.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from grit_scan.doc_type import NEWS, NON_EVENT_TYPES, DocType
from grit_scan.link import COMPETITOR, COUNTERPARTY, DIRECT, THEME, Link, rung_rank
from grit_scan.priority import PriorityConfig, assess, key_gate, outlet_groups
from grit_scan.scale_cues import ScaleCues
from retrieval.similarity import dot

KEEP_BOUND = "KEEP_BOUND"
KEEP_UNBOUND = "KEEP_UNBOUND"
DROP = "DROP"
STALE = "STALE"
NOT_AN_EVENT = "NOT_AN_EVENT"
SIMILAR_TO_SELECTED = "SIMILAR_TO_SELECTED"
NOT_TOP_IN_QUERY = "NOT_TOP_IN_QUERY"
NEAR_DUPLICATE = "NEAR_DUPLICATE"
SAME_EVENT = "SAME_EVENT"
BOUND_OVER_CAP = "BOUND_OVER_CAP"
UNBOUND_OVER_CAP = "UNBOUND_OVER_CAP"


@dataclass(frozen=True)
class FilterConfig:
    window_days: int = 8
    near_duplicate: float = 0.95
    per_query_top: int = 5
    unbound_cap: int = 10
    bound_cap: int = 40
    diverse_similarity: float = 0.90  # an unbound slot is one event. A page this close to a kept one is cut.
    priority: PriorityConfig = PriorityConfig()


def legacy_bind_fields(link: Link, rung: str | None = None) -> dict[str, Any]:
    """The fields the first report reads. They come from the link, and they go when the report reads the link directly."""
    rung = rung or link.rung
    named = [item for item in link.involves if item.strong and item.cls != "Place"]
    names = [item.name for item in named]
    kinds = ["REGISTER_EXACT" if item.matched.casefold() == item.name.casefold() else "REGISTER_ALIAS" for item in named]
    spans = [item.matched for item in named]
    if rung is not None and rung != THEME and link.canonical_lob:
        names.append(link.canonical_lob)
        kinds.append("LOB_KEYWORD")
        spans.extend(link.lob_keywords)
    return {
        "link_level": "ENTITY" if rung in {DIRECT, COUNTERPARTY, COMPETITOR} else "THEME" if rung == THEME else "LOB" if rung else None,
        "entity_names": names,
        "match_types": kinds,
        "canonical_lob": link.canonical_lob,
        "evidence_spans": list(dict.fromkeys(spans))[:5],
    }


def _published(candidate: Mapping[str, Any]) -> datetime | None:
    text = str(candidate.get("published_at") or "")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def filter_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    run_date: date,
    links: Mapping[str, Link],
    doc_types: Mapping[str, DocType] | None = None,
    scales: Mapping[str, ScaleCues] | None = None,
    best_anchors: Mapping[str, Sequence[Mapping[str, Any]]],
    query_scores: Mapping[str, Sequence[Mapping[str, Any]]],
    vectors: Mapping[str, Sequence[float]],
    source_registered: Mapping[str, bool],
    config: FilterConfig = FilterConfig(),
) -> dict[str, Any]:
    by_url = {candidate["url"]: candidate for candidate in candidates}
    decisions: dict[str, dict[str, Any]] = {}
    for url, candidate in by_url.items():
        link = links[url]
        doc = (doc_types or {}).get(url) or DocType(NEWS, ())
        decisions[url] = {
            "url": url,
            "title": candidate.get("title"),
            "publisher": candidate.get("publisher"),
            "published_at": candidate.get("published_at"),
            "decision": None,
            "reason": None,
            "bound": link.rung is not None,
            "rung": link.rung,
            "doc_type": doc.doc_type,
            "doc_cues": list(doc.cues),
            "scale": ((scales or {}).get(url) or ScaleCues((), False, (), ())).as_record(),
            "priority": None,
            "promoted": False,
            "link": link.as_record(),
            **legacy_bind_fields(link),
            "best_anchors": list(best_anchors.get(url) or []),
            "query_rank": None,
            "source_registered": bool(source_registered.get(url)),
            "duplicate_of": None,
            "also_reported_by": [],
        }

    def drop(url: str, reason: str) -> None:
        decisions[url]["decision"], decisions[url]["reason"] = DROP, reason

    alive = []
    for url, candidate in by_url.items():
        published = _published(candidate)
        if published is not None and (run_date - published.date()).days > config.window_days:
            drop(url, STALE)
        elif decisions[url]["doc_type"] in NON_EVENT_TYPES:
            drop(url, NOT_AN_EVENT)
        else:
            alive.append(url)

    # Rank each result inside every query that found it. A result near the top of one query is on topic for it.
    per_query: dict[str, list[tuple[float, str]]] = {}
    for url in alive:
        for hit in query_scores.get(url) or []:
            per_query.setdefault(hit["query_id"], []).append((hit["score"], url))
    rank_in_query: dict[str, int] = {}
    for rows in per_query.values():
        for position, (_score, url) in enumerate(sorted(rows, key=lambda row: (-row[0], row[1])), start=1):
            rank_in_query[url] = min(rank_in_query.get(url, position), position)
    for url in alive:
        decisions[url]["query_rank"] = rank_in_query.get(url)

    def key(url: str) -> tuple[Any, ...]:
        anchors = best_anchors.get(url) or []
        published = _published(by_url[url])
        return (
            -rung_rank(links[url].rung),
            -(anchors[0]["score"] if anchors else 0.0),
            0 if source_registered.get(url) else 1,
            -(published.timestamp() if published else 0.0),
            url,
        )

    def on_topic(url: str) -> bool:
        return rank_in_query.get(url) is not None and rank_in_query[url] <= config.per_query_top

    candidates_left = []
    for url in alive:
        if links[url].rung is not None or key_gate(links[url].themes) or on_topic(url):
            candidates_left.append(url)
        else:
            drop(url, NOT_TOP_IN_QUERY)

    representatives: list[str] = []
    for url in sorted(candidates_left, key=key):
        match = next(
            (rep for rep in representatives if url in vectors and rep in vectors and dot(vectors[url], vectors[rep]) >= config.near_duplicate),
            None,
        )
        if match is None:
            representatives.append(url)
            continue
        drop(url, NEAR_DUPLICATE)
        decisions[url]["duplicate_of"] = match
        decisions[match]["also_reported_by"].append({"url": url, "publisher": by_url[url].get("publisher")})

    # Pages from different outlets that report one event become one finding. A linked candidate, or one with a strong KEY theme hit, absorbs
    # the pages of the same event. They are taken in rung order, so the page with the strongest link is always the one that stays.
    # The merged outlets count toward the OUTLETS cue and show as "also carried by".
    def could_matter(url: str) -> bool:
        return bool(key_gate(links[url].themes)) or links[url].rung is not None

    absorbed: set[str] = set()
    rep_set = set(representatives)
    for url in sorted((rep for rep in representatives if could_matter(rep)), key=key):
        if url in absorbed or url not in vectors:
            continue
        listed = {item["url"] for item in decisions[url]["also_reported_by"]}
        for other in alive:
            if other == url or other in absorbed or other in listed or other not in vectors:
                continue
            if dot(vectors[url], vectors[other]) < config.priority.same_event:
                continue
            if other in rep_set:
                absorbed.add(other)
                drop(other, SAME_EVENT)
                decisions[other]["duplicate_of"] = url
                decisions[url]["also_reported_by"] += [{"url": other, "publisher": by_url[other].get("publisher")}, *decisions[other]["also_reported_by"]]
                decisions[other]["also_reported_by"] = []
            elif decisions[other]["reason"] == NOT_TOP_IN_QUERY:
                decisions[url]["also_reported_by"].append({"url": other, "publisher": by_url[other].get("publisher")})
    representatives = [rep for rep in representatives if rep not in absorbed]

    def corroboration(url: str) -> int:
        """Outlets that carry the story, the page's own included, with every wire service counted once."""
        return len(outlet_groups([by_url[url].get("publisher") or url, *(item["publisher"] or item["url"] for item in decisions[url]["also_reported_by"])]))

    priorities = {}
    for url in representatives:
        link = links[url]
        publishers = [by_url[url].get("publisher"), *(item["publisher"] for item in decisions[url]["also_reported_by"])]
        priority = assess(rung=link.rung, themes=link.themes, cues=(scales or {}).get(url) or ScaleCues((), False, (), ()), publishers=publishers, config=config.priority)
        priorities[url] = priority
        decisions[url]["priority"] = priority.as_record()
        if link.rung is None and priority.big:
            decisions[url].update({"rung": THEME, "bound": True, "promoted": True, **legacy_bind_fields(link, THEME)})

    def final_key(url: str) -> tuple[Any, ...]:
        first = key(url)
        priority = priorities[url]
        return (priority.tier, priority.theme_rank, -priority.cue_count, -rung_rank(decisions[url]["rung"]), -priority.outlets, *first[1:])

    bound_kept: list[str] = []
    unbound_kept: list[str] = []
    for url in sorted(representatives, key=final_key):
        decisions[url]["corroboration"] = corroboration(url)
        if decisions[url]["rung"] is not None:
            if len(bound_kept) < config.bound_cap:
                bound_kept.append(url)
                decisions[url]["decision"] = KEEP_BOUND
            else:
                drop(url, BOUND_OVER_CAP)
            continue
        if not on_topic(url):
            drop(url, NOT_TOP_IN_QUERY)
            continue
        if len(unbound_kept) >= config.unbound_cap:
            drop(url, UNBOUND_OVER_CAP)
            continue
        similar = next(
            (kept for kept in unbound_kept if url in vectors and kept in vectors and dot(vectors[url], vectors[kept]) >= config.diverse_similarity),
            None,
        )
        if similar is None:
            unbound_kept.append(url)
            decisions[url]["decision"] = KEEP_UNBOUND
            continue
        drop(url, SIMILAR_TO_SELECTED)
        decisions[url]["duplicate_of"] = similar
        decisions[similar]["also_reported_by"].append({"url": url, "publisher": by_url[url].get("publisher")})

    for url in (*bound_kept, *unbound_kept):
        decisions[url]["corroboration"] = corroboration(url)  # a page added by SIMILAR_TO_SELECTED after the ranking is counted too

    dropped: dict[str, int] = {}
    for record in decisions.values():
        if record["decision"] == DROP:
            dropped[record["reason"]] = dropped.get(record["reason"], 0) + 1
    return {
        "config": asdict(config),
        "summary": {
            "candidates": len(by_url),
            "keep_bound": len(bound_kept),
            "keep_unbound": len(unbound_kept),
            "dropped": dict(sorted(dropped.items())),
            "tiers": {str(tier): sum(1 for url in bound_kept if priorities[url].tier == tier) for tier in (1, 2, 3)},
            "big": sum(1 for url in bound_kept if priorities[url].big),
            # How many survivors would be big with one cue besides the KEY theme. It shows what a stricter bar costs.
            "big_with_one_cue": sum(1 for url in representatives if priorities[url].gate and priorities[url].cues),
        },
        "decisions": [decisions[candidate["url"]] for candidate in candidates],
        "bound": bound_kept,
        "unbound": unbound_kept,
    }
