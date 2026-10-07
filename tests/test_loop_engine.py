import copy
import json
from types import SimpleNamespace

from loop.card import check
from loop.engine import FindingLoop, LoopConfig, clean_issues
from loop.prompts import DRAFT_SYSTEM
from loop.tools import FollowUpTool
from tests.test_loop_card import _FILLER, _good, _page, _signal, _unit, _world

_TITLE = "LAND TRANSPORT AUTHORITY awards a signalling contract"
_CONFIG = LoopConfig(retries=0, pause=0.0)


class FakeClient:
    """Scripted model. Each call takes the next reply of its kind. A callable reply gets the request payload."""

    def __init__(self, drafts, evaluations=()):
        self.drafts, self.evaluations = list(drafts), list(evaluations)
        self.requests = []
        self.models = SimpleNamespace(generate_content=self._call)

    def _call(self, *, model, contents, config):
        kind = "draft" if config["system_instruction"] == DRAFT_SYSTEM else "evaluate"
        payload = json.loads(contents)
        self.requests.append((kind, payload))
        queue = self.drafts if kind == "draft" else self.evaluations
        reply = queue.pop(0) if queue else ({"issues": []} if kind == "evaluate" else None)
        if isinstance(reply, Exception):
            raise reply
        reply = reply(payload) if callable(reply) else reply
        return SimpleNamespace(text=json.dumps(reply))


def _loop(client, *, pool=(), vectors=None, search=None, embed=None, budget=1, config=_CONFIG, log=None):
    pack, graph, index = _world()
    shared_pool, shared_vectors = list(pool), dict(vectors or {"https://a.com/1": _unit(0)})
    tool = FollowUpTool(search=search, embed=embed, index=index, pool=shared_pool, vectors=shared_vectors, same_event=0.9, budget=budget)
    engine = FindingLoop(client, "fake-model", graph=graph, index=index, themes=pack.themes["themes"], pool=shared_pool, vectors=shared_vectors, tool=tool, config=config, log=log)
    return engine, pack, index, tool


def _signal_for(pack, index, title=_TITLE):
    return _signal(pack, index, title, _FILLER)


def _card(signal_title=_TITLE, **changes):
    card = {
        "same_party": {"verdict": "YES", "why": "The text names the register's party in a Singapore rail context.", "quote": ""},
        "what_happened": "The authority awarded a rail signalling contract.",
        "event_quotes": [signal_title],
        "affected": {"entity_ids": [], "why": "The register links this customer to the business area only."},
        "connection": {"path_ids": ["P1"], "why": "The customer is listed in the register."},
        "evidence": [],
        "hypothesis": {"text": "If the award changes demand, the business area could see more signalling work.", "theme_ids": ["URS-KEY-04"], "path_ids": ["P1"]},
        "follow_up_query": None,
    }
    return card | changes


_CITED = [{"evidence_id": "E1", "quote": "LAND TRANSPORT AUTHORITY named the winner.", "supports": "CONNECTION"}]
_OTHER = [_page("https://b.com/1", "b.com", "Signalling contract awarded", "LAND TRANSPORT AUTHORITY named the winner."), _page("https://c.com/1", "c.com", "Rail contract goes to a new bidder", "LAND TRANSPORT AUTHORITY confirmed the award.")]
_NEAR = {"https://a.com/1": _unit(0), "https://b.com/1": _unit(10), "https://c.com/1": _unit(12)}


def test_a_sound_first_draft_that_the_model_finds_sound_stops_after_one_iteration() -> None:
    client = FakeClient([_card(evidence=_CITED)], [{"issues": []}])
    engine, pack, index, tool = _loop(client, pool=_OTHER, vectors=_NEAR)
    final = engine.run("F01", _signal_for(pack, index))
    assert final["status"] == "COMPLETE" and final["loop"]["stop"] == "CHECKS_PASSED" and final["loop"]["model_calls"] == 2
    assert len(final["loop"]["iterations"]) == 1 and tool.spent == 0  # two other outlets already carry it: no search
    assert final["q4"]["independent_outlets"] == 2 and final["q6"]["level"] == "HIGH"


def test_a_violation_sends_the_draft_back_with_the_violation_and_skips_the_evaluation() -> None:
    bad = _card(event_quotes=["a quote that is not in the text"])
    good = _card(evidence=_CITED)
    client = FakeClient([bad, good], [{"issues": []}])
    engine, pack, index, _ = _loop(client, pool=_OTHER, vectors=_NEAR)
    final = engine.run("F01", _signal_for(pack, index))
    kinds = [kind for kind, _ in client.requests]
    assert kinds == ["draft", "draft", "evaluate"]  # no evaluation of a draft that fails the checks
    refine = client.requests[1][1]
    assert refine["previous_card"] == bad and any(item.startswith("QUOTE_NOT_VERBATIM") for item in refine["violations"])
    assert final["status"] == "COMPLETE" and final["loop"]["stop"] == "CHECKS_PASSED" and len(final["loop"]["iterations"]) == 2


def test_the_models_own_evaluation_issues_drive_a_refinement_and_a_clean_re_evaluation_ends_the_loop() -> None:
    first = _card(evidence=_CITED)
    second = _card(evidence=_CITED, connection={"path_ids": ["P1"], "why": "The customer is listed in the register as a customer of the business area."})
    issue = {"issues": [{"question": "Q3", "problem": "The reason is thin.", "fix": "Say what the register lists."}, {"question": "Q9", "problem": "ignored", "fix": ""}]}
    client = FakeClient([first, second], [issue, {"issues": []}])
    engine, pack, index, _ = _loop(client, pool=_OTHER, vectors=_NEAR)
    final = engine.run("F01", _signal_for(pack, index))
    refine = client.requests[2][1]
    assert refine["issues"] == [{"question": "Q3", "problem": "The reason is thin.", "fix": "Say what the register lists."}] and refine["violations"] == []
    assert final["loop"]["stop"] == "CHECKS_PASSED" and final["loop"]["model_calls"] == 4 and final["loop"]["open_issues"] == []


def test_thin_evidence_triggers_one_follow_up_search_whose_outlet_reaches_the_next_draft() -> None:
    searched = []

    def search(query, key=None):
        searched.append((query, key))
        return [_page("https://b.com/1", "b.com", "Signalling contract awarded", "LAND TRANSPORT AUTHORITY named the winner.")]

    def embed(candidates):
        return {candidate["url"]: _unit(10) for candidate in candidates}

    first = _card(follow_up_query="LAND TRANSPORT AUTHORITY signalling contract winner")
    second = lambda payload: _card(evidence=[{"evidence_id": payload["context"]["evidence"][-1]["evidence_id"], "quote": "LAND TRANSPORT AUTHORITY named the winner.", "supports": "CONNECTION"}])  # noqa: E731
    client = FakeClient([first, second], [{"issues": []}, {"issues": []}])
    engine, pack, index, tool = _loop(client, search=search, embed=embed)
    final = engine.run("F01", _signal_for(pack, index))
    assert searched == [("LAND TRANSPORT AUTHORITY signalling contract winner", "https://a.com/1")] and tool.spent == 1  # the search is keyed by the signal's URL
    first_iteration = final["loop"]["iterations"][0]
    assert first_iteration["tool"] == {"query": searched[0][0], "results": 1, "new": 1, "added": 1, "independent_outlets": 1, "query_source": "MODEL"}
    refine_context = client.requests[2][1]["context"]
    assert refine_context["evidence_summary"]["independent_outlets"] == 1
    assert final["status"] == "COMPLETE" and final["loop"]["follow_up_searches"] == 1 and final["q4"]["items"][-1]["publisher"] == "b.com"
    assert final["q4"]["items"][-1]["url"] == "https://b.com/1"


def test_an_unusable_model_query_falls_back_to_the_headline_and_the_run_budget_is_shared() -> None:
    searched = []
    client = FakeClient([_card(follow_up_query='site:example.com "contract"')] * 4, [{"issues": []}] * 4)
    engine, pack, index, tool = _loop(client, search=lambda query, key=None: searched.append(query) or [], embed=None, budget=1)
    first = engine.run("F01", _signal_for(pack, index))
    assert searched == [_TITLE] and first["loop"]["iterations"][0]["tool"]["query_source"] == "HEADLINE"
    second = engine.run("F02", _signal_for(pack, index))
    assert searched == [_TITLE] and tool.spent == 1 and second["loop"]["follow_up_searches"] == 0  # the budget of one search was used


def test_a_pass_that_changes_nothing_stops_the_loop_and_the_failing_part_is_withheld() -> None:
    bad = _card(evidence=_CITED, hypothesis={"text": "The business area will gain more work.", "theme_ids": ["URS-KEY-04"], "path_ids": ["P1"]})
    client = FakeClient([bad, copy.deepcopy(bad)])
    engine, pack, index, _ = _loop(client, pool=_OTHER, vectors=_NEAR)
    final = engine.run("F01", _signal_for(pack, index))
    assert final["loop"]["stop"] == "NO_CHANGE" and len(final["loop"]["iterations"]) == 2
    assert final["status"] == "PARTIAL" and final["withheld"] == ["hypothesis"] and final["q5"]["hypothesis"] is None and final["violations"]


def test_the_loop_stops_at_the_iteration_limit_and_reports_what_stayed_open() -> None:
    drafts = [_card(event_quotes=[f"quote {n}"]) for n in range(3)]
    client = FakeClient(drafts)
    engine, pack, index, _ = _loop(client, pool=_OTHER, vectors=_NEAR)
    final = engine.run("F01", _signal_for(pack, index))
    assert final["loop"]["stop"] == "MAX_ITERATIONS" and len(final["loop"]["iterations"]) == 3 and final["status"] == "WITHHELD"
    assert final["loop"]["model_calls"] == 3 and final["q6"]["level"] in {"HIGH", "MEDIUM", "LOW", "NOT_ESTABLISHED"}


def test_a_model_failure_withholds_the_card_and_the_next_finding_still_runs() -> None:
    client = FakeClient([RuntimeError("503 service unavailable {secret}")])
    engine, pack, index, _ = _loop(client)
    failed = engine.run("F01", _signal_for(pack, index))
    assert failed["status"] == "WITHHELD" and failed["loop"]["stop"] == "LLM_ERROR" and "secret" not in json.dumps(failed)
    client.drafts.append(_card())
    assert engine.run("F02", _signal_for(pack, index))["status"] in {"COMPLETE", "PARTIAL"}


def test_every_stage_is_logged_and_the_log_reconstructs_the_loop() -> None:
    entries = []
    client = FakeClient([_card(event_quotes=["not in the text"]), _card()], [{"issues": []}])
    engine, pack, index, _ = _loop(client, log=entries.append)
    final = engine.run("F01", _signal_for(pack, index))
    assert [(entry["iteration"], entry["stage"]) for entry in entries] == [(1, "draft"), (2, "refine")]
    assert entries[0]["violations"] and entries[1]["violations"] == [] and entries[0]["response_hash"] != entries[1]["response_hash"]
    assert final["loop"]["iterations"][0]["card"]["event_quotes"] == ["not in the text"]
    json.dumps(final)


def test_clean_issues_keeps_only_known_questions_and_caps_the_list() -> None:
    raw = {"issues": [{"question": f"Q{n}", "problem": "x", "fix": "y"} for n in (1, 2, 3, 4, 5, 1, 2)] + [{"question": "Q6", "problem": "no"}, "text", {"question": "Q1", "problem": ""}]}
    assert len(clean_issues(raw)) == 5 and clean_issues({"issues": "none"}) == [] and clean_issues(None) == []


def test_a_doubted_link_stops_the_follow_up_search_and_is_flagged_as_a_namesake() -> None:
    searched = []
    doubt = _card(same_party={"verdict": "NO", "why": "The text is about a different organisation.", "quote": "LAND TRANSPORT AUTHORITY awards"}, follow_up_query="LAND TRANSPORT AUTHORITY signalling contract winner")
    client = FakeClient([doubt], [{"issues": []}])
    engine, pack, index, tool = _loop(client, search=lambda query, key=None: searched.append(query) or [])
    final = engine.run("F01", _signal_for(pack, index))
    assert searched == [] and tool.spent == 0 and final["namesake"] is True and final["doubted"] is True and final["q6"]["level"] == "NOT_ESTABLISHED"


def test_the_same_issue_raised_twice_stops_the_loop_instead_of_spending_another_iteration() -> None:
    issue = {"issues": [{"question": "Q3", "problem": "The reason is thin.", "fix": "Say more."}]}
    drafts = [_card(evidence=_CITED, connection={"path_ids": ["P1"], "why": f"The customer is listed in the register, version {n}."}) for n in range(3)]
    client = FakeClient(drafts, [issue, issue, issue])
    engine, pack, index, _ = _loop(client, pool=_OTHER, vectors=_NEAR)
    final = engine.run("F01", _signal_for(pack, index))
    assert final["loop"]["stop"] == "NO_PROGRESS" and len(final["loop"]["iterations"]) == 2 and final["loop"]["model_calls"] == 4
    assert final["loop"]["open_issues"] == [{"question": "Q3", "problem": "The reason is thin.", "fix": "Say more."}]


def test_a_search_or_embedding_failure_is_recorded_and_the_loop_still_finishes_the_card() -> None:
    def broken_search(query, key=None):
        raise ConnectionError("the network is down {secret}")

    client = FakeClient([_card(), _card()], [{"issues": []}, {"issues": []}])
    engine, pack, index, tool = _loop(client, search=broken_search)
    final = engine.run("F01", _signal_for(pack, index))
    assert final["status"] in {"COMPLETE", "PARTIAL"} and final["loop"]["iterations"][0]["tool"]["error"] == "ConnectionError" and "secret" not in json.dumps(final)
    assert tool.spent == 1

    def good_search(query, key=None):
        return [_page("https://b.com/1", "b.com", "Signalling contract awarded", "LAND TRANSPORT AUTHORITY named the winner.")]

    def broken_embed(candidates):
        raise RuntimeError("quota")

    client = FakeClient([_card(), _card()], [{"issues": []}, {"issues": []}])
    engine, pack, index, tool = _loop(client, search=good_search, embed=broken_embed)
    final = engine.run("F01", _signal_for(pack, index))
    assert final["loop"]["iterations"][0]["tool"]["error"] == "RuntimeError" and final["loop"]["iterations"][0]["tool"]["added"] == 0


def test_a_failed_refine_keeps_the_last_card_that_passed_its_checks() -> None:
    first = _card(evidence=_CITED)
    issue = {"issues": [{"question": "Q3", "problem": "The reason is thin.", "fix": "Say more."}]}
    client = FakeClient([first, RuntimeError("503 unavailable")], [issue])
    engine, pack, index, _ = _loop(client, pool=_OTHER, vectors=_NEAR)
    final = engine.run("F01", _signal_for(pack, index))
    assert final["loop"]["stop"] == "LLM_ERROR" and final["status"] == "COMPLETE" and final["violations"] == []
    assert final["q1"]["what_happened"] and final["loop"]["open_issues"] == [{"question": "Q3", "problem": "The reason is thin.", "fix": "Say more."}]


def test_an_unexpected_failure_withholds_that_card_with_its_reason_and_the_next_finding_still_runs() -> None:
    client = FakeClient([_card(evidence=_CITED)], [{"issues": []}])
    engine, pack, index, _ = _loop(client, pool=_OTHER, vectors=_NEAR)
    bad_signal = _signal_for(pack, index)
    del bad_signal["link"]  # a record the context cannot read
    failed = engine.run("F01", bad_signal)
    assert failed["status"] == "WITHHELD" and failed["loop"]["stop"] == "LOOP_ERROR" and failed["violations"][0].startswith("LOOP_ERROR: shape: the loop failed (KeyError)")
    assert failed["q6"]["level"] == "NOT_ESTABLISHED" and failed["same_party"] is None and failed["doubted"] is False
    assert engine.run("F02", _signal_for(pack, index))["status"] == "COMPLETE"
    from report.findings import render_card

    assert "Withheld" in "\n".join(render_card(failed))  # the renderer reads the failure card without a key error


def test_a_check_that_raises_is_a_shape_violation_and_not_a_crash() -> None:
    import loop.engine as engine_module

    original = engine_module.check
    engine_module.check = lambda card, context: (_ for _ in ()).throw(ValueError("boom"))
    try:
        client = FakeClient([_card(), _card(), _card()])
        engine, pack, index, _ = _loop(client, pool=_OTHER, vectors=_NEAR)
        final = engine.run("F01", _signal_for(pack, index))
    finally:
        engine_module.check = original
    assert final["status"] == "WITHHELD" and any("the checks failed on this draft (ValueError)" in item for item in final["violations"])
