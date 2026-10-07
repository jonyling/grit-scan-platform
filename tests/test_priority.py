import json

from grit.ontology.link import COMPETITOR, COUNTERPARTY, DIRECT, SECTOR, ThemeHit
from grit.core.priority import ACTION, FIGURE, OUTLETS, WIRE_OUTLET, PriorityConfig, assess, outlet_groups, theme_rank
from grit.core.scale_cues import Figure, ScaleCues

_KEY = [ThemeHit("URS-KEY-04", "KEY", ("cybersecurity",), "TITLE", True)]
_BODY_ONLY = [ThemeHit("URS-KEY-04", "KEY", ("cybersecurity",), "BODY", False)]
_TIER2 = [ThemeHit("URS-TIER_2-02", "TIER_2", ("typhoon",), "TITLE", True)]
_EMERGING = [ThemeHit("URS-EMERGING-02", "EMERGING", ("semiconductor",), "LEAD", True)]
_TARIFF = [ThemeHit("URS-KEY-02", "KEY", ("tariff",), "TITLE", True)]
_NONE = ScaleCues((), False, (), ())
_ACTION = ScaleCues((), False, ("tariffs",), ("GOV_ACTION",))
_FIGURE = ScaleCues((Figure("$5 million", 5e6, "USD", True, None),), True, (), ())


def test_wire_services_collapse_to_one_outlet_and_duplicates_and_blanks_are_ignored() -> None:
    assert outlet_groups(["www.reuters.com", "reuters.com", "globenewswire.com", "prnewswire.com", None, ""]) == ["reuters.com", WIRE_OUTLET]


_ONE = PriorityConfig(min_cues=1)


def test_a_big_event_needs_a_strong_key_theme_and_at_least_two_other_cues_by_default() -> None:
    assert PriorityConfig().min_cues == 2
    two = ScaleCues(_FIGURE.figures, True, ("tariffs",), ("GOV_ACTION",))
    assert assess(rung=None, themes=_KEY, cues=two, publishers=["a.com"]).big
    assert assess(rung=None, themes=_KEY, cues=_ACTION, publishers=["a.com", "b.com", "c.com"]).big
    assert not assess(rung=None, themes=_KEY, cues=_ACTION, publishers=["a.com"]).big  # one cue is not enough
    assert not assess(rung=None, themes=_KEY, cues=_FIGURE, publishers=["a.com"]).big
    assert assess(rung=None, themes=_KEY, cues=_NONE, publishers=["a.com", "b.com", "c.com"]).cues == (OUTLETS,)


def test_with_one_cue_the_bar_is_lower_and_outlets_are_counted_with_wires_collapsed() -> None:
    assert assess(rung=None, themes=_KEY, cues=_ACTION, publishers=["a.com"], config=_ONE).big
    assert assess(rung=None, themes=_KEY, cues=_NONE, publishers=["a.com", "b.com", "c.com"], config=_ONE).big
    assert not assess(rung=None, themes=_KEY, cues=_NONE, publishers=["a.com", "b.com"], config=_ONE).big  # two outlets are not three
    assert not assess(rung=None, themes=_KEY, cues=_NONE, publishers=["globenewswire.com", "prnewswire.com", "openpr.com"], config=_ONE).big


def test_every_cue_without_the_key_theme_makes_nothing_big() -> None:
    cues = ScaleCues(_FIGURE.figures, True, ("tariffs",), ("GOV_ACTION",))
    for themes in ([], _BODY_ONLY, _TIER2):
        result = assess(rung=COUNTERPARTY, themes=themes, cues=cues, publishers=["a.com", "b.com", "c.com"])
        assert not result.big and result.gate == () and result.cue_count == 3


def test_the_outlet_count_is_a_setting() -> None:
    assert assess(rung=None, themes=_KEY, cues=_NONE, publishers=["a.com", "b.com"], config=PriorityConfig(outlets_needed=2, min_cues=1)).big


def test_tiers_run_big_and_direct_then_big_or_significant_direct_then_linked_then_unlinked() -> None:
    tier = lambda rung, themes, cues: assess(rung=rung, themes=themes, cues=cues, publishers=["a.com"], config=_ONE).tier  # noqa: E731
    assert tier(DIRECT, _KEY, _ACTION) == 1 and tier(COUNTERPARTY, _KEY, _FIGURE) == 1
    assert tier(None, _KEY, _ACTION) == 2  # big, no register link
    assert tier(DIRECT, _TIER2, _NONE) == 2 and tier(COUNTERPARTY, _EMERGING, _NONE) == 2  # direct, and a theme is hit
    assert tier(COUNTERPARTY, [], _ACTION) == 2 and tier(DIRECT, [], _FIGURE) == 2  # direct, and a scale cue is met
    assert tier(DIRECT, [], _NONE) == 3 and tier(COUNTERPARTY, _BODY_ONLY, _NONE) == 3  # a name mention only
    assert tier(COMPETITOR, _KEY, _ACTION) == 2  # a competitor is not direct, but the event is big
    assert tier(COMPETITOR, [], _ACTION) == 3 and tier(SECTOR, _KEY, _NONE) == 3
    assert tier(None, _KEY, _NONE) == 4 and tier(None, [], _ACTION) == 4


def test_a_name_mention_says_so_and_is_not_significant() -> None:
    quiet = assess(rung=COUNTERPARTY, themes=[], cues=_NONE, publishers=["a.com"])
    assert not quiet.significant and quiet.tier == 3 and any("a name mention only" in reason for reason in quiet.reasons)
    assert assess(rung=COUNTERPARTY, themes=_EMERGING, cues=_NONE, publishers=["a.com"]).significant


def test_the_theme_tier_ranks_key_then_tier_2_then_emerging_then_none_and_a_body_mention_does_not_count() -> None:
    assert [theme_rank(themes) for themes in (_KEY, _TIER2, _EMERGING, [], _BODY_ONLY)] == [0, 1, 2, 3, 3]
    assert theme_rank(_EMERGING + _KEY) == 0


def test_a_cue_that_repeats_the_theme_word_does_not_count_twice() -> None:
    repeats = ScaleCues(_FIGURE.figures, True, ("Tariffs",), ("GOV_ACTION",))
    result = assess(rung=None, themes=_TARIFF, cues=repeats, publishers=["a.com"])
    assert result.cues == (FIGURE,) and not result.big  # KEY theme plus one cue, below the default bar of two
    assert any("repeats the theme word" in reason for reason in result.reasons)
    other = ScaleCues(_FIGURE.figures, True, ("export controls",), ("GOV_ACTION",))
    assert assess(rung=None, themes=_TARIFF, cues=other, publishers=["a.com"]).big  # a different word is a second cue


def test_the_record_logs_every_component_and_serialises() -> None:
    result = assess(rung=DIRECT, themes=_KEY, cues=ScaleCues(_FIGURE.figures, True, ("tariffs",), ("GOV_ACTION",)), publishers=["a.com", "b.com", "c.com"])  # default bar
    record = result.as_record()
    assert record["tier"] == 1 and record["cues"] == [OUTLETS, ACTION, FIGURE] and record["cue_count"] == 4 and record["outlets"] == 3
    assert record["gate"] == ["URS-KEY-04"] and len(record["reasons"]) == 5
    assert any("not converted" in reason for reason in record["reasons"])
    json.dumps(record)
