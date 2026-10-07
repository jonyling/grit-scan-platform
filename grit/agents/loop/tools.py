"""The tool the loop may use: one follow-up web search for a finding whose independent evidence is thin."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from grit.ontology.link import LinkIndex
from grit.agents.loop.context import Context, add_outlet_evidence

Search = Callable[[str, str | None], list[dict[str, Any]]]  # query, and the signal URL the search is for
Embed = Callable[[Sequence[Mapping[str, Any]]], Mapping[str, Sequence[float]]]


class FollowUpTool:
    """The pool and the vectors are shared with every finding, so one search can help a later finding."""

    def __init__(self, *, search: Search | None, embed: Embed | None, index: LinkIndex, pool: list[dict[str, Any]], vectors: dict[str, Sequence[float]], same_event: float, budget: int) -> None:
        self.search, self.embed, self.index, self.pool, self.vectors = search, embed, index, pool, vectors
        self.same_event, self.budget, self.spent = same_event, budget, 0

    def available(self) -> bool:
        return self.search is not None and self.spent < self.budget

    def run(self, query: str, context: Context, *, limit: int = 2) -> dict[str, Any]:
        """Search once, embed what is new, and add other outlets that carry the event. A failure is recorded and never raised."""
        self.spent += 1
        error = None
        try:
            results = self.search(query, context.signal["url"]) if self.search else []
        except Exception as exc:
            results, error = [], type(exc).__name__
        known = {candidate["url"] for candidate in self.pool}
        new = [candidate for candidate in results if candidate["url"] not in known]
        if new and self.embed is not None:
            try:
                self.vectors.update(self.embed(new))
            except Exception as exc:
                error = error or type(exc).__name__  # pages with no vector are not used as evidence
        self.pool.extend(new)
        added = add_outlet_evidence(context, self.pool, self.vectors, self.index, same_event=self.same_event, kind="FOLLOW_UP", limit=limit)
        result = {"query": query, "results": len(results), "new": len(new), "added": added, "independent_outlets": context.independent_outlets()}
        return result | ({"error": error} if error else {})
