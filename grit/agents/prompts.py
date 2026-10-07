"""Narrow prompts. A prompt version is part of the agent log."""

from __future__ import annotations

AGENT_NAMES = ("Researcher", "Assessment", "Critic", "Reporting")

PROMPTS = {
    "Researcher": (
        "p5-researcher-1",
        "Propose source candidates from the search tool only. Do not scrape, score, rate credibility, or write state. If search returns nothing, report NOT_VERIFIABLE.",
    ),
    "Assessment": (
        "p5-assessment-1",
        "Propose a category, a rationale, cited register entities, and evidence references. Do not produce a score, risk band, likelihood, or workflow transition.",
    ),
    "Critic": (
        "p5-critic-1",
        "Call the entity, URL, and corroboration tools. If a tool returns NOT_VERIFIABLE, report that marker. Do not invent a replacement fact, source, or entity.",
    ),
    "Reporting": (
        "p5-reporting-1",
        "Draft a review narrative from fields already supplied. Quote them. Do not calculate a score or change a verdict.",
    ),
}

FORBIDDEN_FIELDS = frozenset(
    {
        "score",
        "final_score",
        "risk_band",
        "severity",
        "credibility_rating",
        "credibility",
        "route",
        "aperture",
        "likelihood",
        "probability",
    }
)
