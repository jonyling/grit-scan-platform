"""The loop for one finding: draft, check, evaluate, use a tool, refine, and stop.

  draft     the model writes the card from the ontology context.
  check     deterministic functions test every claim against the context (grit/agents/loop/card.py). They decide what is valid.
  evaluate  when the checks pass, the model reviews its own draft against the same context and lists soft issues.
  tool      when independent evidence is thin, one follow-up web search adds other outlets to the context.
  refine    the model revises the card with the violations, the issues, and the new evidence.
Stops: CHECKS_PASSED (no violation, no issue, no new evidence), NO_CHANGE (a pass changed nothing), NO_PROGRESS (the model raises the same
issues again), MAX_ITERATIONS, LLM_ERROR, or LOOP_ERROR (an unexpected failure withholds this card, and the run goes on).
A part that still fails its checks at the end is withheld. Confidence is computed from logged facts, never by the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from grit.core.canonical import canonical_hash
from grit.ontology.link import LinkIndex
from grit.ontology.graph import OntologyGraph
from grit.agents.loop.card import check, default_follow_up, finalize, valid_follow_up
from grit.agents.loop.context import Context, build_context
from grit.agents.loop.llm import LoopError, call_json
from grit.agents.loop.prompts import DRAFT_SYSTEM, DRAFT_VERSION, EVALUATE_SYSTEM, EVALUATE_VERSION
from grit.agents.loop.tools import FollowUpTool

QUESTIONS = frozenset({"Q1", "Q2", "Q3", "Q4", "Q5"})
MAX_ISSUES = 5


@dataclass(frozen=True)
class LoopConfig:
    max_iterations: int = 3
    same_event: float = 0.90
    thin_evidence: int = 2  # a follow-up search runs when fewer independent outlets than this carry the event
    followups_per_finding: int = 1  # follow-up searches one finding may use
    retries: int = 1  # model call retries
    pause: float = 2.0


def clean_issues(raw: Any) -> list[dict[str, str]]:
    """The model's issues, kept to the known shape. They are advice for the next draft, never a decision."""
    issues = []
    for item in (raw.get("issues") if isinstance(raw, dict) else None) or []:
        if isinstance(item, dict) and item.get("question") in QUESTIONS and isinstance(item.get("problem"), str) and item["problem"].strip():
            issues.append({"question": item["question"], "problem": item["problem"].strip()[:300], "fix": str(item.get("fix") or "").strip()[:300]})
    return issues[:MAX_ISSUES]


class FindingLoop:
    def __init__(
        self,
        client: Any,
        model: str,
        *,
        graph: OntologyGraph,
        index: LinkIndex,
        themes: Sequence[Mapping[str, Any]],
        pool: list[dict[str, Any]],
        vectors: dict[str, Sequence[float]],
        tool: FollowUpTool,
        config: LoopConfig = LoopConfig(),
        log: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.client, self.model, self.graph, self.index, self.themes = client, model, graph, index, themes
        self.pool, self.vectors, self.tool, self.config = pool, vectors, tool, config
        self._log = log or (lambda entry: None)

    def run(self, finding_id: str, signal: Mapping[str, Any]) -> dict[str, Any]:
        """One finding. Nothing a model reply or a tool does can raise out of this method: a failure withholds this card and the run goes on."""
        progress: dict[str, Any] = {"iterations": [], "calls": 0, "searches": 0}
        try:
            return self._run(finding_id, signal, progress)
        except Exception as exc:
            return _failure(finding_id, signal, self.model, f"the loop failed ({type(exc).__name__})", progress)

    def _run(self, finding_id: str, signal: Mapping[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        context = build_context(finding_id, signal, graph=self.graph, index=self.index, themes=self.themes, pool=self.pool, vectors=self.vectors, same_event=self.config.same_event)
        iterations: list[dict[str, Any]] = progress["iterations"]
        card: dict[str, Any] | None = None
        violations: list[str] = []
        issues: list[dict[str, str]] = []
        stop = "MAX_ITERATIONS"
        previous_hash = None
        previous_issues: list[dict[str, str]] = []
        for number in range(1, self.config.max_iterations + 1):
            stage = "draft" if card is None else "refine"
            request: dict[str, Any] = {"context": context.view}
            if card is not None:
                request |= {"previous_card": card, "violations": violations, "issues": issues}
            try:
                draft = call_json(self.client, model=self.model, system=DRAFT_SYSTEM, payload=request, retries=self.config.retries, pause=self.config.pause)
                progress["calls"] += 1
            except LoopError as exc:
                stop = "LLM_ERROR"
                if card is None:
                    violations = ["LLM_ERROR: shape: " + str(exc)]  # no draft exists
                # A refine that fails leaves the last checked card and its violations as they were.
                self._record(iterations, finding_id, number, stage, error=str(exc))
                break
            card = draft
            digest = canonical_hash(card)
            try:
                violations = check(card, context)
            except Exception as exc:
                violations = [f"BAD_SHAPE: shape: the checks failed on this draft ({type(exc).__name__})"]
            issues = []
            evaluated = None
            if not violations:
                try:
                    evaluated = call_json(
                        self.client, model=self.model, system=EVALUATE_SYSTEM, payload={"context": context.view, "card": card}, retries=self.config.retries, pause=self.config.pause
                    )
                    progress["calls"] += 1
                    issues = clean_issues(evaluated)
                except LoopError as exc:
                    self._record(iterations, finding_id, number, "evaluate", error=str(exc))
            tool_result = None
            party = card.get("same_party") if isinstance(card.get("same_party"), dict) else {}
            doubted = not any("same_party" in item for item in violations) and (party.get("verdict") == "NO" or (party.get("verdict") == "UNSURE" and str(party.get("quote") or "").strip()))
            if (
                number < self.config.max_iterations
                and progress["searches"] < self.config.followups_per_finding
                and self.tool.available()
                and context.independent_outlets() < self.config.thin_evidence
                and not doubted
            ):
                progress["searches"] += 1
                query = valid_follow_up(card.get("follow_up_query"), context)
                source = "MODEL" if query else "HEADLINE"
                try:
                    tool_result = self.tool.run(query or default_follow_up(context), context) | {"query_source": source}
                except Exception as exc:
                    tool_result = {"query": query or "", "results": 0, "new": 0, "added": 0, "independent_outlets": context.independent_outlets(), "query_source": source, "error": type(exc).__name__}
            self._record(
                iterations, finding_id, number, stage, response_hash=digest, violations=violations, issues=issues, tool=tool_result, card=card,
                evaluated=evaluated is not None, independent_outlets=context.independent_outlets(),
            )
            if not violations and not issues and not (tool_result and tool_result["added"]):
                stop = "CHECKS_PASSED"
                break
            if number == self.config.max_iterations:
                stop = "MAX_ITERATIONS"
                break
            if digest == previous_hash and not (tool_result and tool_result["added"]):
                stop = "NO_CHANGE"
                break
            if issues and not violations and issues == previous_issues and not (tool_result and tool_result["added"]):
                stop = "NO_PROGRESS"
                break
            previous_hash, previous_issues = digest, issues
        final = finalize(card, context, violations)
        final["loop"] = {
            "model": self.model, "prompt_versions": {"draft": DRAFT_VERSION, "evaluate": EVALUATE_VERSION}, "iterations": iterations, "stop": stop,
            "model_calls": progress["calls"], "follow_up_searches": progress["searches"], "open_issues": issues,
        }
        return final

    def _record(self, iterations: list[dict[str, Any]], finding_id: str, number: int, stage: str, **fields: Any) -> None:
        entry = {"finding_id": finding_id, "iteration": number, "stage": stage, **fields}
        self._log(entry)
        if stage != "evaluate":
            iterations.append({key: value for key, value in entry.items() if key != "finding_id"})


def _failure(finding_id: str, signal: Mapping[str, Any], model: str, reason: str, progress: Mapping[str, Any]) -> dict[str, Any]:
    """A card with nothing in it but the reason. It keeps the shape the renderer reads."""
    return {
        "finding_id": finding_id, "title": signal.get("title"), "publisher": signal.get("publisher"), "published_at": signal.get("published_at"), "url": signal.get("url"),
        "also_reported_by": [], "priority": signal.get("priority"), "rung": signal.get("rung"), "doc_type": signal.get("doc_type"), "lob": None,
        "status": "WITHHELD", "withheld": ["shape"], "violations": [f"LOOP_ERROR: shape: {reason}"], "themes": [],
        "q6": {"level": "NOT_ESTABLISHED", "reasons": [f"No card was made: {reason}."], "inputs": {}},
        "same_party": None, "namesake": False, "doubted": False,
        "loop": {
            "model": model, "prompt_versions": {"draft": DRAFT_VERSION, "evaluate": EVALUATE_VERSION}, "iterations": list(progress.get("iterations") or []), "stop": "LOOP_ERROR",
            "model_calls": progress.get("calls", 0), "follow_up_searches": progress.get("searches", 0), "open_issues": [],
        },
    }
