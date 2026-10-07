"""Briefing priority for one candidate: what to read first. Pure: no I/O, no model.

This orders the briefing. It is not a severity, a likelihood, or a harm rating, and it never decides whether a candidate is linked.

A big event hits a KEY STE theme (required) and meets at least min_cues of three cues:
  OUTLETS  three or more outlets carry the event. Every wire service counts as one outlet, because a wire repeats one text.
  ACTION   a regulator or government action, a war, or a pandemic, in the title or lead.
  FIGURE   a stated amount of at least one million in a comparable currency.
A cue must not repeat the word that opened the theme gate: "tariff" is a KEY theme keyword and a government-action cue, so it counts once.
A direct signal is linked on the DIRECT or COUNTERPARTY rung: it names an STE entity, a customer, or a supplier. A name alone is not significant.
A direct signal is significant when it hits a theme in the title or lead, or meets a scale cue.

Tier 1 is big and direct. Tier 2 is big, or direct and significant. Tier 3 is linked and neither. Tier 4 is not linked.
A big event with no register link is promoted to the THEME rung, because the KEY theme is the link the register states for it.
Inside a tier the order is: the best theme tier hit (KEY, TIER_2, EMERGING), more cues met, a higher rung, then the filter's other keys.
The themes are how the register states what matters to STE. Every component is logged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from grit.core.doc_type import is_wire
from grit.ontology.link import COUNTERPARTY, DIRECT, ThemeHit
from grit.core.scale_cues import ScaleCues

OUTLETS = "OUTLETS"
ACTION = "ACTION"
FIGURE = "FIGURE"
WIRE_OUTLET = "wire services"


@dataclass(frozen=True)
class PriorityConfig:
    outlets_needed: int = 3
    min_cues: int = 2  # cues, besides the KEY theme, that make an event big. One cue let a campaign launch and a distribution deal through.
    same_event: float = 0.90  # cosine at or above which pages from different outlets are one event


@dataclass(frozen=True)
class Priority:
    tier: int
    big: bool
    gate: tuple[str, ...]  # KEY theme ids that hit in the title or lead, or twice
    cues: tuple[str, ...]
    cue_count: int  # the KEY theme counts as one
    theme_rank: int  # the best theme tier hit in the title or lead: 0 KEY, 1 TIER_2, 2 EMERGING, 3 none
    significant: bool
    outlets: int
    outlet_names: tuple[str, ...]
    direct: bool
    reasons: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        for key in ("gate", "cues", "outlet_names", "reasons"):
            record[key] = list(record[key])
        return record


def outlet_groups(publishers: Iterable[str | None]) -> list[str]:
    groups: list[str] = []
    for publisher in publishers:
        if not publisher:
            continue
        group = WIRE_OUTLET if is_wire(publisher) else publisher.casefold().removeprefix("www.")
        if group not in groups:
            groups.append(group)
    return groups


def key_gate(themes: Sequence[ThemeHit]) -> tuple[str, ...]:
    return tuple(hit.theme_id for hit in themes if hit.tier == "KEY" and hit.strong)


_TIER_RANK = {"KEY": 0, "TIER_2": 1, "EMERGING": 2}


def theme_rank(themes: Sequence[ThemeHit]) -> int:
    return min((_TIER_RANK[hit.tier] for hit in themes if hit.strong), default=3)


def _repeats_the_gate(cues: ScaleCues, themes: Sequence[ThemeHit]) -> bool:
    """True when every government-action match is a word that also opened the KEY theme gate."""
    words = {keyword.casefold() for hit in themes if hit.tier == "KEY" and hit.strong for keyword in hit.matched}
    return bool(cues.action) and all(any(word in match.casefold() or match.casefold() in word for word in words) for match in cues.action)


def assess(*, rung: str | None, themes: Sequence[ThemeHit], cues: ScaleCues, publishers: Iterable[str | None], config: PriorityConfig = PriorityConfig()) -> Priority:
    gate = key_gate(themes)
    groups = outlet_groups(publishers)
    met: list[str] = []
    reasons: list[str] = []
    if len(groups) >= config.outlets_needed:
        met.append(OUTLETS)
        reasons.append(f"{len(groups)} outlets carry it: {', '.join(groups[:5])}.")
    if cues.action_cue and gate and _repeats_the_gate(cues, themes):
        reasons.append(f"The action cue '{cues.action[0]}' repeats the theme word, so it does not count again.")
    elif cues.action_cue:
        met.append(ACTION)
        reasons.append(f"{'/'.join(cues.action_classes).lower().replace('_', ' ')}: '{cues.action[0]}'.")
    if cues.figure_cue:
        met.append(FIGURE)
        figure = next(item for item in cues.figures if item.counted)
        reasons.append(f"Stated figure: {figure.text} ({figure.currency}, as stated, not converted).")
    if gate:
        reasons.insert(0, f"Hits KEY theme {', '.join(gate)}.")
    big = bool(gate) and len(met) >= config.min_cues
    direct = rung in {DIRECT, COUNTERPARTY}
    rank = theme_rank(themes)
    significant = rank < 3 or bool(met)
    tier = 1 if big and direct else 2 if big or (direct and significant) else 3 if rung else 4
    if direct:
        reasons.append(f"Direct: {rung.lower()} link." + ("" if significant else " No theme hit and no scale cue: a name mention only."))
    if rank in (1, 2) and not gate:
        reasons.append(f"Hits a {'TIER_2' if rank == 1 else 'EMERGING'} theme.")
    return Priority(tier, big, gate, tuple(met), len(met) + (1 if gate else 0), rank, significant, len(groups), tuple(groups), direct, tuple(reasons))
