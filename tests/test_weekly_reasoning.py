import copy
from datetime import date

import pytest

from grit.agents.weekly import ContractError, check_critique, check_selections, time_scope
from grit.agents.reader import critique
from grit.pool import Pool

CHAIN = {"fact": "Alpha airport operations are reported.", "exposure": "not established",
         "transmission": "Operations at a served airport", "impact": "not established",
         "significance": {"exposure": 1, "consequence": 1, "immediacy": 1, "evidence": 1, "change": 0}}


def test_missing_issue_list_is_not_a_clean_review(monkeypatch):
    monkeypatch.setattr("grit.agents.reader._call", lambda *a: {"unexpected": True})
    accepted, rejected, log = critique(None, "stub", drafts=[{"item_id": "I1"}], priors={}, sources={})
    assert log.status == "WITHHELD"
    assert not accepted
    assert log.violations


def test_whole_batch_must_be_accounted_for():
    with pytest.raises(ContractError, match="coverage"):
        check_selections({"selections": []}, {"A": {"signal_id": "A"}}, {"A"})


def test_a_coverage_error_names_the_missing_and_unexpected_ids():
    row = {"decision": "EXCLUDE", "reason": "Not relevant."}
    with pytest.raises(ContractError, match="exactly one selection for each of the 2 candidates. Missing: B. Not in the batch: C."):
        check_selections({"selections": [{**row, "signal_id": "A"}, {**row, "signal_id": "C"}]},
                         {"A": {"signal_id": "A"}, "B": {"signal_id": "B"}}, {"A", "B"})


def test_selection_cannot_write_a_controlled_score():
    with pytest.raises(ContractError, match="controlled"):
        check_selections({"selections": [{"signal_id": "A", "decision": "INCLUDE", "reason": "Named", "source_ids": ["A"], "severity": 3}]}, {"A": {}}, {"A"})


@pytest.mark.parametrize("decision", [None, [], {}, 1])
def test_malformed_editorial_decision_is_rejected(decision):
    with pytest.raises(ContractError, match="decision"):
        check_selections({"selections": [{"signal_id": "A", "decision": decision, "reason": "Named", "source_ids": ["A"]}]}, {"A": {}}, {"A"})


def test_missing_support_issue_does_not_need_a_fabricated_quote():
    raw = {"reviewed_ids": ["A"], "issues": [{"issue_id": "I1", "kind": "UNSUPPORTED", "target": "A", "problem": "No staff confirmation was supplied.", "material": True, "source_ids": [], "missing_evidence": "Staff safety confirmation"}], "resolved_issue_ids": []}
    assert check_critique(raw, {"A"}, {"A": {}}, set()) == raw


def test_positive_correction_needs_verbatim_evidence():
    raw = {"reviewed_ids": ["A"], "issues": [{"issue_id": "I1", "kind": "STALE", "target": "A", "problem": "Reopened", "material": True, "source_ids": ["A"], "quote": "invented quotation about reopening"}], "resolved_issue_ids": []}
    with pytest.raises(ContractError, match="quote"):
        check_critique(raw, {"A"}, {"A": {"text": "The airport is open for commercial operations."}}, set())


def test_date_exceptions_preserve_hindsight():
    assert time_scope("2026-10-01", date(2026, 11, 2), 7) == "EARLIER_CONTEXT"
    assert time_scope("2026-11-03", date(2026, 11, 2), 7) == "HINDSIGHT"
    assert time_scope(None, date(2026, 11, 2), 7) == "DATE_NOT_ESTABLISHED"


def test_reasoned_selection_does_not_change_baseline(tmp_path):
    pool = Pool(tmp_path / "pool.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="test", kind="READER_SCAN")
    signal = pool.add_signal(url="https://news.example/a", title="A", text="Named project", publisher="news.example", published_at="2026-10-01", lane="TEST", content_hash="text")
    pool.save_assessment({"signal_id": signal, "run_id": "R", "pack": "test", "decision": "DROP", "drop_reason": "STALE"})
    from grit.pipeline.weekly import WeeklyStore, WeeklyCoordinator
    store = WeeklyStore(pool, "R", search_limit=0)
    selections = {"selections": [{"signal_id": signal, "decision": "INCLUDE", "reason": "Ongoing impact", "source_ids": [signal], **CHAIN}]}
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=lambda *a, **k: selections)
    _, call_id = coordinator.invoke("Assessment", "test", {}, lambda raw: check_selections(raw, {signal: {}}, {signal}))
    store.select(call_id, selections["selections"])
    assert pool.weekly_assessments("R")[0]["baseline_decision"] == "DROP"
    assert pool.assessments("R")[0]["decision"] == "DROP"
    assert pool.weekly_assessments("R")[0]["editorial"]["time_scope"] == "EARLIER_CONTEXT"


@pytest.fixture
def weekly(tmp_path, request):
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore
    pool = Pool(tmp_path / "pool.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="test", kind="READER_SCAN")
    ids = []
    for name, decision, published in (("Alpha", "DROP", "2026-10-01"), ("Beta", "KEEP", "2026-11-03")):
        signal = pool.add_signal(url=f"https://news.example/{name}", title=f"{name} operations",
                                 text=f"The {name} airport is open for commercial operations.",
                                 publisher="News", published_at=published, lane="TEST", content_hash=name)
        pool.add_sighting(signal, "R", query_id=name, query=name, lane="TEST", target_id=None, rank=1)
        pool.save_assessment({"signal_id": signal, "run_id": "R", "pack": "test", "decision": decision,
                              "harm_json": {"status": "NOT_RUN", "reason": "No structured observations"}})
        ids.append(signal)
    store = WeeklyStore(pool, "R", search_limit=getattr(request, "param", 0))
    def call(_client, *, system, payload, **_kwargs):
        from grit.agents.weekly import PROMPTS
        if system == PROMPTS["Supervisor"]:
            return {"next_action": payload["allowed_actions"][0], "reason": "Required stage"}
        if system == PROMPTS["Researcher"]:
            return {"queries": [], "missing_checks": []}
        if system in (PROMPTS["Assessment"], PROMPTS["Critic"], PROMPTS["Screen"]) and "candidates" in payload:
            selections = [{"signal_id": key, "decision": "INCLUDE" if key == ids[0] else "EXCLUDE",
                           "reason": "Ongoing Alpha exposure; Beta is a different entity", "source_ids": [key], **CHAIN}
                          for key in payload["candidates"]]
            result = {"selections": selections}
            if system in (PROMPTS["Critic"], PROMPTS["Screen"]):
                result.update(reviewed_ids=list(payload["candidates"]), issues=[], resolved_issue_ids=[])
            return result
        if system == PROMPTS["Reporting"]:
            return {"edits": [{"path": path, "text": f"Alpha operations are reported open [{ids[0]}].", "source_ids": [ids[0]]}
                              for path in ("bottom_line.text", "bottom_line.escalation")]}
        if system == PROMPTS["Critic"]:
            return {"reviewed_ids": payload["targets"], "issues": [], "resolved_issue_ids": []}
        if system == PROMPTS["Memory"]:
            return {"proposals": [{"kind": "CORRECTION", "text": "Alpha is open; closure is not established.",
                                    "source_ids": [ids[0]], "profile_id": None}]}
        raise AssertionError(payload)
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=call)
    yield pool, store, coordinator, ids, call
    pool.close()


def _draft(signal):
    return {"meta": {"status": "DRAFT_FOR_REVIEW"}, "bottom_line": {"text": "Unreviewed", "escalation": "Unreviewed"},
            "conditions": [], "qa": [], "method": {}, "membership": [signal]}


def _profile(name="reader"):
    from types import SimpleNamespace
    return SimpleNamespace(profile_id=name, review_role="REVIEWER", label=name)


def test_six_roles_are_real_calls_and_render_uses_frozen_snapshot(weekly):
    from grit.pipeline.weekly import admit_memory, frozen_report, memory_context, run_memory
    from grit.workflow.packages import review_package, submit_package
    pool, store, coordinator, ids, call = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    assert [row["signal_id"] for row in pool.weekly_assessments("R")] == [ids[0]]
    result = coordinator.report(_profile(), _draft(ids[0]), [])
    assert result["status"] == "READY"
    snapshot = frozen_report(pool, "R", "reader")
    assert snapshot["brief"]["membership"] == [ids[0]]
    store.finish("READY")
    pool.finish_run("R")
    package = submit_package(pool, run_id="R", profile=_profile(), members=[ids[0]], brief_hash=result["content_hash"], files={})
    row = pool.one("SELECT * FROM packages WHERE package_id = ?", (package["package_id"],))
    assert row["membership"]["weekly_report_id"] == snapshot["report_id"]
    assert all("weekly-call:" in row["trigger_record_id"] for row in pool.workflow_rows() if row["state"] in {"ASSESSED", "CRITIQUED"})
    with pytest.raises(ValueError, match="human decision"):
        run_memory(pool, package_id=package["package_id"], version=1, client=None, model="offline", call=call)
    review_package(pool, package_id=package["package_id"], version=1, reviewer=_profile(), intent="APPROVE")
    memory = run_memory(pool, package_id=package["package_id"], version=1, client=None, model="offline", call=call)
    assert memory["proposals"] == 1
    memory_row = pool.one("SELECT * FROM weekly_memory")
    assert memory_context(pool, "R") == []
    admit_memory(pool, memory_row["memory_id"], _profile(), "ADMIT")
    assert len(memory_context(pool, "R")) == 1
    assert frozen_report(pool, "R", "reader")["brief"] == snapshot["brief"]
    assert {row["role"] for row in pool.rows("SELECT role FROM weekly_calls WHERE status = 'VALIDATED'")} == {
        "Supervisor", "Researcher", "Assessment", "Critic", "Reporting", "Memory",
    }


def test_budget_survives_resume_and_evidence_revisions(weekly):
    from grit.pipeline.weekly import WeeklyStore
    pool, store, coordinator, ids, _ = weekly
    assert not store.reserve("search")
    assert store.reserve("revision")
    assert store.reserve("revision")
    assert not store.reserve("revision")
    pool.save_assessment({"run_id": "R", "pack": "test", "signal_id": ids[0], "decision": "DROP", "drop_reason": "STALE"})
    resumed = WeeklyStore(pool, "R", search_limit=0)
    assert resumed.state["version"] == 2
    assert resumed.state["revisions_used"] == 2
    assert not resumed.reserve("revision")
    with pytest.raises(ValueError, match="retain|keep"):
        WeeklyStore(pool, "R", search_limit=3)


def test_evidence_refresh_invalidates_completion_without_resetting_budget(weekly):
    from grit.pipeline.weekly import WeeklyStore
    pool, store, _, ids, _ = weekly
    assert store.reserve("revision")
    store.finish("READY")
    pool.finish_run("R", "COMPLETE")
    pool.save_assessment({"run_id": "R", "pack": "test", "signal_id": ids[0], "decision": "DROP", "drop_reason": "STALE"})
    read_only = WeeklyStore(pool, "R", search_limit=0, refresh=False)
    assert read_only.state["version"] == 1
    assert read_only.state["status"] == "READY"
    refreshed = WeeklyStore(pool, "R", search_limit=0)
    assert refreshed.state["version"] == 2
    assert refreshed.state["status"] == "STARTED"
    assert refreshed.state["revisions_used"] == 1
    assert pool.run("R")["status"] == "STARTED"
    assert pool.run("R")["finished_at"] is None


def test_missing_or_changed_critique_cannot_enter_review(weekly):
    from grit.pipeline.weekly import frozen_report
    from grit.workflow.packages import submit_package
    pool, store, coordinator, ids, _ = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    result = coordinator.report(_profile(), _draft(ids[0]), [])
    with pytest.raises(ValueError, match="match"):
        submit_package(pool, run_id="R", profile=_profile(), members=[ids[0]], brief_hash="changed", files={})
    pool.add_signal(url="https://news.example/late", title="Later source", text="New evidence", publisher="News", published_at=None, lane="TEST", content_hash="late")
    signal = pool.one("SELECT signal_id FROM signals WHERE title = 'Later source'")["signal_id"]
    pool.add_sighting(signal, "R", query_id="later", query="later", lane="TEST", target_id=None, rank=1)
    with pytest.raises(ValueError, match="changed"):
        frozen_report(pool, "R", "reader")


def _final_objection(original, *, resolve_on_rewrite):
    from grit.agents.weekly import PROMPTS
    finals = []
    def disputed(*args, system, payload, **kwargs):
        if system == PROMPTS["Critic"] and "targets" in payload:
            finals.append(payload)
            if resolve_on_rewrite and len(finals) > 1:
                return {"reviewed_ids": payload["targets"], "issues": [], "resolved_issue_ids": ["UNSUPPORTED-1"],
                        "resolutions": [{"issue_id": "UNSUPPORTED-1", "response": "The rewrite removed the claim.", "source_ids": []}]}
            return {"reviewed_ids": payload["targets"], "issues": [{
                "issue_id": "UNSUPPORTED-1", "target": payload["targets"][0], "kind": "UNSUPPORTED",
                "problem": "The bottom line overstates exposure", "material": True, "source_ids": [],
                "missing_evidence": "A source linking the event to this business",
            }], "resolved_issue_ids": []}
        return original(*args, system=system, payload=payload, **kwargs)
    return disputed, finals


@pytest.mark.parametrize("resolved", [False, True])
def test_a_report_gets_its_own_rewrite_after_pool_revisions_are_used(weekly, resolved):
    from grit.pipeline.weekly import frozen_report
    pool, store, coordinator, ids, original = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    assert store.reserve("revision") and store.reserve("revision")
    coordinator.call, finals = _final_objection(original, resolve_on_rewrite=resolved)
    result = coordinator.report(_profile(), _draft(ids[0]), [])
    assert len(finals) == 2, "one rewrite even though the pool used both revision rounds"
    assert store.state["revisions_used"] == 2, "a report rewrite does not use the pool's revision budget"
    assert result["status"] == ("READY" if resolved else "INCOMPLETE")
    if not resolved:
        with pytest.raises(ValueError, match="not ready"):
            frozen_report(pool, "R", "reader")
        assert frozen_report(pool, "R", "reader", ready=False)["status"] == "INCOMPLETE"


def test_two_readers_share_facts_but_receive_separate_critiques(weekly):
    pool, store, coordinator, ids, _ = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    a = coordinator.report(_profile("a"), _draft(ids[0]), [])
    b = coordinator.report(_profile("b"), _draft(ids[0]), [])
    assert a["status"] == b["status"] == "READY"
    assert len(pool.rows("SELECT * FROM weekly_reports")) == 2
    calls = pool.rows("SELECT * FROM weekly_calls WHERE task LIKE 'final-%'")
    assert {row["input"]["targets"][0] for row in calls} == {"REPORT:a", "REPORT:b"}


@pytest.mark.parametrize("bad", [None, [], {"reviewed_ids": []}, {"reviewed_ids": [["A"]], "issues": [], "resolved_issue_ids": []}])
def test_malformed_critique_is_rejected(bad):
    with pytest.raises(ContractError):
        check_critique(bad, {"A"}, {"A": {}}, set())


@pytest.mark.parametrize("kind", [None, [], {}, 1])
def test_malformed_issue_kind_is_rejected(kind):
    raw = {"reviewed_ids": ["A"], "issues": [{
        "issue_id": "I1", "kind": kind, "target": "A", "material": True,
        "problem": "Missing support", "source_ids": [], "missing_evidence": "Staff confirmation",
    }], "resolved_issue_ids": []}
    with pytest.raises(ContractError, match="kind or target"):
        check_critique(raw, {"A"}, {"A": {}}, set())


def test_issue_resolution_needs_an_explicit_explanation():
    raw = {"reviewed_ids": ["A"], "issues": [], "resolved_issue_ids": ["I1"]}
    with pytest.raises(ContractError, match="explicit response"):
        check_critique(raw, {"A"}, {"A": {}}, {"I1"})


def test_legacy_invalid_issue_does_not_crash_or_validate(monkeypatch):
    monkeypatch.setattr("grit.agents.reader._call", lambda *a: {"issues": ["not an issue object"]})
    accepted, rejected, log = critique(None, "stub", drafts=[{"item_id": "I1"}], priors={}, sources={})
    assert not accepted and rejected and log.status == "WITHHELD"


def test_critic_can_overrule_assessment_not_just_repeat_it(weekly):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    def assessed(*args, system, payload, **kwargs):
        response = original(*args, system=system, payload=payload, **kwargs)
        if system == PROMPTS["Assessment"]:
            for selection in response["selections"]:
                selection["decision"] = "INCLUDE"
        return response
    coordinator.call = assessed
    coordinator.assess_and_critique(0)
    assert [row["signal_id"] for row in pool.weekly_assessments("R")] == [ids[0]]
    history = pool.rows("SELECT * FROM weekly_selections WHERE signal_id = ? ORDER BY selection_id", (ids[1],))
    assert [row["selection"]["decision"] for row in history] == ["INCLUDE", "EXCLUDE"]
    assert pool.assessments("R")[0]["decision"] in {"KEEP", "DROP"}


def test_large_pool_has_no_silent_candidate_or_issue_cap(weekly):
    pool, store, coordinator, ids, _ = weekly
    for number in range(35):
        signal = pool.add_signal(url=f"https://news.example/extra-{number}", title=f"Extra {number}", text="Unrelated evidence",
                                 publisher="News", published_at=None, lane="TEST", content_hash=str(number))
        pool.save_assessment({"signal_id": signal, "run_id": "R", "pack": "test", "decision": "DROP"})
    from grit.pipeline.weekly import WeeklyStore
    coordinator.store = WeeklyStore(pool, "R", search_limit=0)
    coordinator.assess_and_critique(0)
    reviewed = {key for call in pool.rows("SELECT * FROM weekly_calls WHERE role = 'Critic' AND status = 'VALIDATED'") for key in call["output"]["reviewed_ids"]}
    assert len(reviewed) == 37
    issues = [{"issue_id": f"I{i}", "kind": "UNSUPPORTED", "target": "A", "problem": "Missing support",
               "material": False, "source_ids": [], "missing_evidence": "Confirmation"} for i in range(20)]
    raw = {"reviewed_ids": ["A"], "issues": issues, "resolved_issue_ids": []}
    assert len(check_critique(raw, {"A"}, {"A": {}}, set())["issues"]) == 20


def test_zero_countersearch_is_explicitly_not_searched(weekly):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    def requested(*args, system, payload, **kwargs):
        if system == PROMPTS["Researcher"]:
            return {"queries": [{"query": "Alpha airport reopening official", "reason": "Disconfirm closure", "signal_ids": [ids[0]]}], "missing_checks": []}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = requested
    def no_network(query):
        raise AssertionError("Zero budget must make no network call")
    coordinator.research("countercheck", no_network, lambda: None)
    assert store.state["searches_used"] == 0
    (search,) = [row["issue"] for key, row in store.issues().items() if key.startswith("SEARCH-")]
    assert not search["material"] and "budget was spent or disabled" in search["problem"], "an unrun search is disclosed, not blocking"
    assert pool.one("SELECT * FROM execution_log WHERE actor = 'WeeklyCountersearch'")["status"] == "NOT_SEARCHED"
    brief = {"v2": {"completeness": {}}}
    coordinator.disclose_unrun_searches(brief)
    assert "Alpha airport reopening official" in brief["v2"]["completeness"]["limitations"][0]


@pytest.mark.parametrize("weekly", [1], indirect=True)
def test_acquired_counterevidence_is_assessed_and_critically_accounted_for(weekly):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    def requested(*args, system, payload, **kwargs):
        if system == PROMPTS["Researcher"]:
            return {"queries": [{"query": "Alpha official update", "reason": "Seek contrary evidence", "signal_ids": [ids[0]]}], "missing_checks": []}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = requested
    def reassess():
        for signal in pool.signals_for_run("R"):
            if signal["signal_id"] not in ids:
                pool.save_assessment({"signal_id": signal["signal_id"], "run_id": "R", "pack": "test",
                                      "decision": "DROP", "harm_json": {"status": "NOT_RUN", "reason": "No structured facts"}})
    coordinator.research("countercheck", lambda q: {"status": "SUCCEEDED", "candidates": [{
        "url": "https://official.example/update", "title": "Reopening", "text": "The airport is open for commercial operations.",
        "publisher": "Official", "published_at": "2026-11-03",
    }]}, reassess)
    coordinator.assess_and_critique(0)
    assert len(pool.weekly_assessments("R", include_excluded=True)) == 3
    assert store.state["searches_used"] == 1
    assert not store.reserve("search")


@pytest.mark.parametrize("weekly", [1], indirect=True)
def test_source_verification_records_actual_text_and_retrieval_scope(weekly, monkeypatch):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    def requested(*args, system, payload, **kwargs):
        if system == PROMPTS["Researcher"]:
            return {"queries": [], "missing_checks": [], "verify_source_ids": [ids[0]]}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = requested
    monkeypatch.setattr("grit.agents.weekly_evidence.verify_page", lambda url: {
        "status": "SUCCEEDED", "scope": "EXTRACTED_PAGE", "text": "The airport reopened after inspections.", "body_hash": "verified",
    })
    coordinator.research("verify", None, lambda: None)
    assert "reopened after inspections" in coordinator.sources[ids[0]]["text"]
    assert pool.signal(ids[0])["text"] == "The Alpha airport is open for commercial operations."
    assert store.current()
    assert store.state["searches_used"] == 1


def test_stale_objection_is_preserved_and_resolved_only_in_its_scope(weekly):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    def challenged(*args, system, payload, **kwargs):
        response = original(*args, system=system, payload=payload, **kwargs)
        if system == PROMPTS["Critic"]:
            if payload["task"] == "pool-0-0":
                response["issues"] = [{"issue_id": "REOPEN", "kind": "STALE", "target": ids[0],
                                       "problem": "The closure is no longer supported.", "material": True,
                                       "source_ids": [ids[0]], "quote": "airport is open for commercial operations"}]
            else:
                response["resolved_issue_ids"] = ["REOPEN"]
                response["resolutions"] = [{"issue_id": "REOPEN", "response": "The revised selection no longer asserts closure.", "source_ids": [ids[0]]}]
        return response
    coordinator.call = challenged
    coordinator.assess_and_critique(0)
    assert store.issues()["REOPEN"]["status"] == "OPEN"
    coordinator.assess_and_critique(1)
    assert not store.issues()
    assert len(pool.rows("SELECT * FROM weekly_issues WHERE issue_id = 'REOPEN'")) == 2
    assert pool.qa_log("R"), "Resolution must not delete the original correction."


def test_editing_selection_invalidates_frozen_readiness(weekly):
    from grit.pipeline.weekly import frozen_report
    pool, store, coordinator, ids, _ = weekly
    coordinator.assess_and_critique(0)
    coordinator.report(_profile(), _draft(ids[0]), [])
    selection = {"selections": [{"signal_id": ids[0], "decision": "EXCLUDE", "reason": "Revised selection", "source_ids": [ids[0]]}]}
    coordinator.call = lambda *a, **k: selection
    _, invocation = coordinator.invoke("Assessment", "changed-selection", {}, lambda raw: check_selections(raw, coordinator.sources, {ids[0]}))
    store.select(invocation, selection["selections"])
    with pytest.raises(ValueError, match="selection changed"):
        frozen_report(pool, "R", "reader")


@pytest.mark.parametrize("url", ["http://news.example/a", "https://news.example:bad/a", "https://user:password@news.example/a"])
def test_verification_rejects_unsafe_or_malformed_urls_without_network(url):
    from grit.agents.weekly_evidence import verify_page
    assert verify_page(url)["status"] == "NOT_CHECKED"


@pytest.mark.parametrize(("target", "same_page"), [
    ("https://news.example/a/", True), ("https://www.news.example/a", True), ("http://news.example/a/", False),
    ("https://other.example/a", False), ("https://news.example/b", False), ("https://news.example/a?x=1", False)])
def test_only_a_redirect_to_the_same_page_is_followed(target, same_page):
    from grit.agents.weekly_evidence import canonical_redirect
    assert canonical_redirect("https://news.example/a", target) is same_page


def test_page_verification_never_connects_to_private_addresses(monkeypatch):
    from grit.agents.weekly_evidence import verify_page
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: pytest.fail("A private address was contacted"))
    assert verify_page("https://news.example/")["status"] == "BLOCKED"


def test_human_evidence_and_memory_keep_typed_scope(weekly):
    from grit.pipeline.weekly import add_internal_evidence
    pool, store, coordinator, ids, _ = weekly
    approver = _profile()
    added = add_internal_evidence(pool, "R", [{"title": "Staff confirmation", "text": "No staff were assigned to the named airport.",
                                              "reference": "Approved operational confirmation", "published_at": "2026-11-03"}], approver)
    assert pool.signal(added[0])["lane"] == "HUMAN_APPROVED"
    with pytest.raises(PermissionError):
        add_internal_evidence(pool, "R", [], type("Reader", (), {"review_role": "READER"})())
    assert pool.one("SELECT * FROM weekly_evidence WHERE signal_id = ?", (added[0],))["provenance"]["approved_by"] == approver.profile_id


def test_cli_exposes_opt_in_and_explicit_budget():
    from grit.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["weekly", "--reasoned", "--countersearch-limit", "0"])
    assert args.reasoned and args.countersearch_limit == 0
    agents = parser.parse_args(["agents", "--reasoned", "--countersearch-limit", "2", "--internal-evidence", "approved.json", "--evidence-approved-by", "reviewer"])
    assert agents.internal_evidence == "approved.json"


def test_dropped_candidates_receive_real_deterministic_enrichment(weekly, tmp_path, monkeypatch):
    from grit.ontology.pack import OntologyPack
    from grit.ontology.risk_cascade import RiskCascade
    from grit.pipeline import reader_scan
    pool, store, coordinator, ids, _ = weekly
    pack = OntologyPack(
        source_register={}, exposure={}, lob_profiles={}, themes={}, baseline={},
        config_dir=tmp_path, pack="test", manifest={"level": "BA", "label": "Synthetic business"},
        scan_plan={"windowDays": 7, "namedItems": {"source": "graph"}},
    )
    monkeypatch.setattr(reader_scan, "_load_pack", lambda name: pack)
    monkeypatch.setattr(reader_scan, "_aliases", lambda: {})
    monkeypatch.setattr(reader_scan, "load_risk_cascade", lambda **k: RiskCascade({}, {}, {}, {}))
    scored = []
    def score(record, **kwargs):
        scored.append(record["signal_id"])
        return {"status": "NOT_ASSESSED", "reason": "No structured observations"}
    reader_scan.assess(pool, reader_scan.ReaderRun("R", date(2026, 11, 2), "test"), red_patterns={}, score_harm_fn=score)
    rows = pool.assessments("R")
    assert set(scored) == set(ids)
    assert all(row["decision"] == "DROP" for row in rows)
    assert all(row["harm"]["status"] == "NOT_ASSESSED" for row in rows)
    assert all(row["tier"] is not None for row in rows)


def test_rescued_candidate_reaches_events_and_dataverse_without_changing_daily(weekly):
    from grit.dataverse.sync import plan_sync
    from grit.pipeline.events import event_register
    pool, store, coordinator, ids, _ = weekly
    coordinator.assess_and_critique(0)
    exported = plan_sync(pool, "R", {})
    assert [signal for signal, _ in exported] == [ids[0]]
    events = event_register(pool, "R")
    assert {signal for event in events for signal in event["signal_ids"]} == {ids[0]}
    pool.start_run("DAILY", run_date="2026-11-02", pack="test", kind="DAILY")
    for signal, decision in zip(ids, ("DROP", "KEEP")):
        pool.save_assessment({"signal_id": signal, "run_id": "DAILY", "pack": "test", "decision": decision})
    assert [row["signal_id"] for row in pool.weekly_assessments("DAILY")] == [ids[1]]


def test_render_all_formats_from_exact_snapshot_without_provider_or_live_routing(weekly, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from test_batch_d_reports import _brief
    from grit.report.package import write_pack
    pool, store, coordinator, ids, _ = weekly
    coordinator.assess_and_critique(0)
    brief = _brief("Alpha operations are reported open.")
    brief["meta"].update(run_id="R", run_date="2026-11-02", pack="test", profile_id="reader")
    brief.update(membership=[ids[0]], order_book=None, findings=[], other_signals=[], qa=[], red=[])
    for condition in brief["conditions"]:
        condition.update(status="VALIDATED", basis={"plain": "Alpha is open.", "runs": [{"text": "Alpha is open."}], "sources": []})
    routed = [{**row, "shown": True, "route": {"priority": "P3", "matched": [], "suppression_reason": None}}
              for row in pool.weekly_assessments("R")]
    coordinator.report(_profile(), brief, routed)
    profile = SimpleNamespace(profile_id="reader", brief_for=lambda pack: {"formats": ["docx", "pptx", "xlsx"]})
    monkeypatch.setattr("grit.report.package.build_brief", lambda *a: pytest.fail("Rendering rebuilt the model"))
    monkeypatch.setattr("grit.report.package.routed_signals", lambda *a: pytest.fail("Rendering read live routes"))
    monkeypatch.setattr("grit.pipeline.weekly.call_json", lambda *a, **k: pytest.fail("Rendering called a provider"))
    result = write_pack(pool, "R", profile, folder=tmp_path / "pack", submit=False)
    from pathlib import Path
    assert set(result["files"]) == {"docx", "pptx", "xlsx", "md"}
    assert all(Path(path).is_file() and Path(path).stat().st_size > 0 for path in result["files"].values())
    from docx import Document
    from pptx import Presentation
    from openpyxl import load_workbook
    assert Document(result["files"]["docx"]).paragraphs
    assert len(Presentation(result["files"]["pptx"]).slides) > 0
    assert load_workbook(result["files"]["xlsx"]).sheetnames


def test_full_coordinator_uses_actual_section_drafting_and_brief_assembly(weekly, tmp_path, monkeypatch):
    from grit.agents.reader_prompts import REPORTER_BOTTOM_LINE_SYSTEM
    from grit.ontology.pack import OntologyPack
    from grit.ontology.risk_cascade import RiskCascade
    from grit.pipeline import reader_scan
    from grit.pipeline.weekly import run_weekly
    from grit.profiles import Profile
    pool, store, coordinator, ids, call = weekly
    pack = OntologyPack(
        source_register={}, exposure={}, lob_profiles={}, themes={}, baseline={}, config_dir=tmp_path / "test",
        pack="test", manifest={"level": "BA", "label": "Synthetic business"},
        scan_plan={"windowDays": 7, "namedItems": {"source": "graph"}},
    )
    monkeypatch.setattr(reader_scan, "_load_pack", lambda name: pack)
    monkeypatch.setattr(reader_scan, "_aliases", lambda: {})
    monkeypatch.setattr(reader_scan, "load_risk_cascade", lambda **k: RiskCascade({}, {}, {}, {}))
    monkeypatch.setattr("grit.report.brief.load_ontology_pack", lambda *a: pack)
    monkeypatch.setattr("grit.ontology.pack.manifests", lambda *a: {"test": pack.manifest})
    run = reader_scan.ReaderRun("R", date(2026, 11, 2), "test")
    def reassess():
        return reader_scan.assess(pool, run, red_patterns={}, score_harm_fn=lambda *a, **k: {"status": "NOT_ASSESSED", "reason": "No structured observations"})
    reassess()
    profile = Profile(profile_id="reader", name="Reader", role="Risk partner", aperture="WIDE_APERTURE",
                      packs=("test",), business_scope={}, review_role="REVIEWER", red_alerts={}, email=None,
                      briefs={"test": {"sections": ["OTHER_SIGNALS", "QA_LOG", "WATCHPOINTS"], "formats": ["docx"]}})
    def section_response(system, payload):
        assert system == REPORTER_BOTTOM_LINE_SYSTEM
        return {"bottom_line": "Impact is not established.", "escalation": "Confirm operational exposure."}
    client = reader_scan.stub_client(section_response)
    monkeypatch.setattr("grit.pipeline.events.risk_names", lambda *a: {"T-FIN": "Financial volatility", "T-REG": "Trade compliance"})
    for query_id, lane, target in (("q-fin", "CONDITION", "T-FIN"), ("q-reg", "CONDITION", "T-REG"), ("q-sup", "SUPPLIER", "S1"), ("q-com", "COMPETITOR", "C1")):
        pool.record_search("R", query_id=query_id, engine="BRAVE", query=query_id, lane=lane, target_id=target, canonical_lob=None, results=0, fetched_at="2026-11-02T00:00:00Z")
    result = run_weekly(pool, run, client=None, model="offline", search_limit=0, profiles=[profile], call=call,
                        reassess=reassess, draft_sections=lambda: reader_scan.run_agents(pool, run, client=client, model="offline"))
    assert result["status"] == "READY"
    assert pool.run("R")["status"] == "COMPLETE"
    from grit.pipeline.weekly import frozen_report
    snapshot = frozen_report(pool, "R", "reader")
    assert snapshot["brief"]["meta"]["sections"] == ["OTHER_SIGNALS", "QA_LOG", "WATCHPOINTS"]
    assert ids[0] in snapshot["brief"]["membership"]
    assert snapshot["brief"]["method"]["weekly"]["completeness"]["blocking"] == []


def test_a_critical_completeness_gap_blocks_readiness_even_without_critic_issues(weekly):
    from grit.pipeline.weekly import frozen_report
    pool, store, coordinator, ids, _ = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    brief = _draft(ids[0])
    brief["v2"] = {"gate": [{"question": "Checked macro and financial items?", "answer": "No", "critical": True, "note": "No financial search was answered."},
                            {"question": "Checked the named suppliers and customers?", "answer": "Partly", "critical": False, "note": "Rotating."}],
                   "completeness": {"blocking": ["Checked macro and financial items?"], "limitations": ["Checked the named suppliers and customers? Partly: Rotating."]}}
    result = coordinator.report(_profile(), brief, [])
    assert result["status"] == "INCOMPLETE"
    assert not any(row["issue"]["material"] for row in store.issues().values()), "the Critic raised nothing; the gate alone blocks"
    snapshot = frozen_report(pool, "R", "reader", ready=False)
    assert snapshot["brief"]["method"]["weekly"]["completeness"]["blocking"] == ["Checked macro and financial items?"]
    with pytest.raises(ValueError, match="not ready"):
        frozen_report(pool, "R", "reader")


def test_red_exclusion_does_not_cancel_alert_draft(weekly, tmp_path):
    from grit.pipeline.weekly import WeeklyStore
    from grit.report.red import draft_red_alerts
    pool, store, coordinator, ids, _ = weekly
    pool.save_assessment({"signal_id": ids[1], "run_id": "R", "pack": "test", "decision": "KEEP", "red": 1})
    coordinator.store = WeeklyStore(pool, "R", search_limit=0)
    coordinator.assess_and_critique(0)
    assert ids[1] not in {row["signal_id"] for row in pool.weekly_assessments("R")}
    drafts = draft_red_alerts(pool, "R", "test", [], tmp_path / "alerts")
    assert len(drafts) == 1
    assert ids[1] in drafts[0]["alert_id"]


def test_airis_uses_exact_approved_snapshot_and_versioned_execution(weekly):
    from grit.pipeline.weekly import WeeklyStore
    from grit.workflow.packages import airis_ready, hand_off, review_package, submit_package
    pool, store, coordinator, ids, _ = weekly
    pool.save_assessment({"signal_id": ids[0], "run_id": "R", "pack": "test", "decision": "DROP", "tier": 1, "rung": "DIRECT"})
    coordinator.store = WeeklyStore(pool, "R", search_limit=0)
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    head = _profile("head")
    head.review_role = "BA_HEAD"
    result = coordinator.report(head, _draft(ids[0]), pool.weekly_assessments("R"))
    coordinator.store.finish("READY")
    pool.finish_run("R")
    package = submit_package(pool, run_id="R", profile=head, members=[ids[0]], brief_hash=result["content_hash"], files={})
    review_package(pool, package_id=package["package_id"], version=1, reviewer=head, intent="APPROVE")
    pool.save_assessment({"signal_id": ids[0], "run_id": "R", "pack": "test", "tier": 9, "rung": "SECTOR"})
    ready = airis_ready(pool, head)
    assert len(ready) == 1 and ready[0]["execution_id"].endswith("-v1")
    assert hand_off(pool, head, ready)[0]["decision"] == "ALLOW"
    assert airis_ready(pool, head) == []


def test_returned_package_needs_new_report_and_cannot_reuse_old_critique(weekly):
    from grit.workflow.packages import review_package, submit_package
    pool, store, coordinator, ids, _ = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    result = coordinator.report(_profile(), _draft(ids[0]), [])
    store.finish("READY")
    pool.finish_run("R")
    package = submit_package(pool, run_id="R", profile=_profile(), members=[ids[0]], brief_hash=result["content_hash"], files={})
    review_package(pool, package_id=package["package_id"], version=1, reviewer=_profile(), intent="REQUEST_REWORK")
    with pytest.raises(ValueError, match="new report snapshot"):
        submit_package(pool, run_id="R", profile=_profile(), members=[ids[0]], brief_hash=result["content_hash"], files={})
    revised = coordinator.report(_profile(), _draft(ids[0]), [])
    second = submit_package(pool, run_id="R", profile=_profile(), members=[ids[0]], brief_hash=revised["content_hash"], files={})
    assert second["version"] == 2
    states = pool.workflow_rows()
    assert any(row["execution_id"].endswith("-v2") for row in states)


def test_two_apertures_preserve_one_event_spine_and_controlled_facts(weekly):
    from grit.agents.weekly import PROMPTS
    from grit.pipeline.weekly import WeeklyStore
    from grit.pipeline.events import event_register
    from grit.profiles import Profile
    from grit.report.brief import routed_signals
    pool, store, coordinator, ids, original = weekly
    pool.save_assessment({"signal_id": ids[0], "run_id": "R", "pack": "test", "rung": "DIRECT", "tier": 1})
    pool.save_assessment({"signal_id": ids[1], "run_id": "R", "pack": "test", "rung": "SECTOR", "tier": 3})
    coordinator.store = WeeklyStore(pool, "R", search_limit=0)
    def include(*args, system, payload, **kwargs):
        response = original(*args, system=system, payload=payload, **kwargs)
        if system in (PROMPTS["Assessment"], PROMPTS["Critic"]):
            for selection in response["selections"]:
                selection["decision"] = "INCLUDE"
        return response
    coordinator.call = include
    coordinator.assess_and_critique(0)
    base = dict(name="Synthetic reader", role="Risk partner", packs=("test",), business_scope={},
                review_role="READER", red_alerts={}, email=None, briefs={"test": {}})
    wide = Profile(profile_id="wide", aperture="WIDE_APERTURE", **base)
    narrow = Profile(profile_id="narrow", aperture="MATERIAL_APERTURE", **base)
    a = routed_signals(pool, "R", wide)
    b = routed_signals(pool, "R", narrow)
    assert {row["signal_id"] for row in a if row["shown"]} == set(ids)
    assert {row["signal_id"] for row in b if row["shown"]} == {ids[0]}
    assert len(event_register(pool, "R")) == 2
    for key in ("harm", "tier", "risk_level", "red"):
        assert {row["signal_id"]: row.get(key) for row in a} == {row["signal_id"]: row.get(key) for row in b}


def test_model_inputs_stay_bounded_for_a_large_pool(weekly):
    from grit.agents.weekly import PROMPTS
    from grit.pipeline import weekly as module
    pool, store, coordinator, ids, original = weekly
    for number in range(60):
        signal = pool.add_signal(url=f"https://news.example/bulk-{number}", title=f"Bulk {number}", text="Long unrelated text. " * 2000,
                                 publisher="News", published_at="2026-11-01", lane="TEST", content_hash=f"bulk-{number}")
        pool.add_sighting(signal, "R", query_id=f"bulk-{number}", query="bulk", lane="TEST", target_id=None, rank=1)
        pool.save_assessment({"signal_id": signal, "run_id": "R", "pack": "test", "decision": "KEEP",
                              "harm_json": {"status": "NOT_RUN", "detail": "x" * 5000}})
    coordinator.store = store = module.WeeklyStore(pool, "R", search_limit=0)
    seen = []
    def recording(*args, system, payload, **kwargs):
        seen.append((system, payload))
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = recording
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    researcher = next(payload for system, payload in seen if system == PROMPTS["Researcher"])
    assert all(len(row["excerpt"]) <= module.INDEX_TEXT_CHARS and "text" not in row for row in researcher["sources"].values())
    for system, payload in seen:
        if system == PROMPTS["Assessment"]:
            assert len(payload["candidates"]) <= 16 and len(payload["sources"]) <= module.RELATED_LIMIT
            for row in payload["candidates"].values():
                assert len(row["text"]) <= module.FULL_TEXT_CHARS + len(" [truncated]")
                assert row.get("harm") in (None, {"status": "NOT_RUN"})
    covered = {key for system, payload in seen if system == PROMPTS["Critic"] for key in payload.get("candidates", {})}
    assert covered == set(coordinator.sources)
    brief = _draft(ids[0])
    report_sources = coordinator.report_sources(brief, coordinator.sources)
    assert set(report_sources) >= {ids[0]} and all("text" not in row for row in report_sources.values())


def test_oversized_input_fails_before_any_model_call(weekly, monkeypatch):
    from grit.agents.weekly import ContractError
    from grit.pipeline import weekly as module
    pool, store, coordinator, ids, _ = weekly
    monkeypatch.setattr(module, "MAX_PAYLOAD_CHARS", 1000)
    coordinator.call = lambda *a, **k: pytest.fail("the model must not be called")
    with pytest.raises(ContractError, match="exceeds"):
        coordinator.invoke("Researcher", "big", {"sources": {"x": "y" * 2000}}, lambda raw: None)
    assert store.state["status"] == "INCOMPLETE"


def test_revision_round_revisits_only_challenged_candidates(weekly):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    for number in range(20):
        signal = pool.add_signal(url=f"https://news.example/more-{number}", title=f"More {number}", text="Unrelated evidence",
                                 publisher="News", published_at="2026-11-01", lane="TEST", content_hash=f"more-{number}")
        pool.add_sighting(signal, "R", query_id=f"more-{number}", query="more", lane="TEST", target_id=None, rank=1)
        pool.save_assessment({"signal_id": signal, "run_id": "R", "pack": "test", "decision": "DROP"})
    from grit.pipeline.weekly import WeeklyStore
    coordinator.store = store = WeeklyStore(pool, "R", search_limit=0)
    batches = []
    def challenging(*args, system, payload, **kwargs):
        response = original(*args, system=system, payload=payload, **kwargs)
        if system == PROMPTS["Critic"] and "candidates" in payload:
            batches.append(set(payload["candidates"]))
            if ids[0] in payload["candidates"] and len(batches) == 1:
                response["issues"] = [{"issue_id": "ALPHA", "kind": "UNSUPPORTED", "target": ids[0], "material": True,
                                       "problem": "Reopening not confirmed", "source_ids": [], "missing_evidence": "Operator notice"}]
        return response
    coordinator.call = challenging
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    assert set().union(*batches[:2]) == set(coordinator.sources)
    assert all(batch == {ids[0]} for batch in batches[2:]) and len(batches) > 2
    assert store.state["revisions_used"] <= 2


def test_false_positive_needs_evidence_but_not_a_quote():
    issue = {"issue_id": "FP", "kind": "FALSE_POSITIVE", "target": "A", "problem": "Different entity", "material": True}
    assert check_critique({"reviewed_ids": ["A"], "issues": [{**issue, "source_ids": ["A"]}], "resolved_issue_ids": []}, {"A"}, {"A": {"text": "x"}}, set())
    with pytest.raises(ContractError):
        check_critique({"reviewed_ids": ["A"], "issues": [{**issue, "source_ids": []}], "resolved_issue_ids": []}, {"A"}, {"A": {"text": "x"}}, set())
    with pytest.raises(ContractError):
        check_critique({"reviewed_ids": ["A"], "issues": [{**issue, "source_ids": ["A"], "quote": "an invented passage that is long"}], "resolved_issue_ids": []}, {"A"}, {"A": {"text": "x"}}, set())


def test_contract_violation_gets_two_logged_repair_attempts(weekly):
    pool, store, coordinator, ids, _ = weekly
    replies = iter([{"wrong": True}, {"ok": True}, {"wrong": True}, {"wrong": True}, {"wrong": True}])
    payloads = []
    def call(*args, payload, **kwargs):
        payloads.append(payload)
        return next(replies)
    def validate(raw):
        if not raw.get("ok"):
            raise ContractError("needs ok")
    coordinator.call = call
    assert coordinator.invoke("Supervisor", "repair", {}, validate)[0] == {"ok": True}
    assert "needs ok" in payloads[1]["contract_feedback"]
    assert pool.one("SELECT * FROM execution_log WHERE status = 'REJECTED'")
    with pytest.raises(ContractError):
        coordinator.invoke("Supervisor", "repair-again", {}, validate)
    assert len(payloads) == 5


def test_empty_selection_evidence_defaults_to_the_candidate_record():
    raw = {"selections": [{"signal_id": "A", "decision": "EXCLUDE", "reason": "Unrelated", "source_ids": []}]}
    check_selections(raw, {"A": {}}, {"A"})
    assert raw["selections"][0]["source_ids"] == ["A"] and raw["selections"][0]["source_ids_defaulted"]
    with pytest.raises(ContractError):
        check_selections({"selections": [{"signal_id": "A", "decision": "EXCLUDE", "reason": "x", "source_ids": ["Z"]}]}, {"A": {}}, {"A"})


def test_factual_quote_can_come_from_the_headline():
    sources = {"A": {"title": "NCS Opens AI Mission Centre in Singapore", "text": "Body text only."}}
    issue = {"issue_id": "W", "kind": "WRONG_ATTRIBUTION", "target": "A", "problem": "Wrong entity", "material": True,
             "source_ids": ["A"], "quote": "NCS Opens AI Mission Centre in Singapore"}
    assert check_critique({"reviewed_ids": ["A"], "issues": [issue], "resolved_issue_ids": []}, {"A"}, sources, set())
    with pytest.raises(ContractError):
        check_critique({"reviewed_ids": ["A"], "issues": [{**issue, "quote": "A sentence that appears nowhere at all"}],
                        "resolved_issue_ids": []}, {"A"}, sources, set())


def test_reused_issue_id_on_another_target_is_renamed_and_replay_is_idempotent(weekly):
    pool, store, coordinator, ids, _ = weekly
    def issue(target):
        return {"issue_id": "ISSUE-01", "kind": "FALSE_POSITIVE", "target": target, "problem": "Not relevant.",
                "material": False, "source_ids": [target]}
    calls = [pool.insert("weekly_calls", {"run_id": "R", "version": store.state["version"], "role": "Critic",
                                          "task": f"pool-0-{n}", "prompt_version": "test", "model": "offline",
                                          "input_hash": str(n), "input_json": {}, "output_json": {},
                                          "status": "VALIDATED", "created_at": "2026-11-02T00:00:00Z"}) for n in (0, 16)]
    store.record_critique(calls[0], {"issues": [issue(ids[0])], "resolved_issue_ids": []})
    second = {"issues": [issue(ids[1])], "resolved_issue_ids": []}
    store.record_critique(calls[1], second)
    store.record_critique(calls[1], second)
    assert {key: row["issue"]["target"] for key, row in store.issues().items()} == {
        "ISSUE-01": ids[0], f"ISSUE-01@{ids[1]}": ids[1]}
    assert len(pool.rows("SELECT * FROM weekly_issues")) == 2


def _add_drops(pool, count, prefix="drop"):
    added = []
    for number in range(count):
        signal = pool.add_signal(url=f"https://news.example/{prefix}-{number}", title=f"{prefix} {number}", text="Unrelated text. " * 400,
                                 publisher="News", published_at="2026-11-01", lane="TEST", content_hash=f"{prefix}-{number}")
        pool.add_sighting(signal, "R", query_id=f"{prefix}-{number}", query=prefix, lane="TEST", target_id=None, rank=1)
        pool.save_assessment({"signal_id": signal, "run_id": "R", "pack": "test", "decision": "DROP", "drop_reason": "NOT_RELEVANT"})
        added.append(signal)
    return added


def _fast(pool, call, workers=4):
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore
    pool.update_run_config("R", weekly_review="fast")
    store = WeeklyStore(pool, "R", search_limit=0)
    return store, WeeklyCoordinator(store, client=None, model="offline", call=call, fast=True, workers=workers)


def test_fast_review_screens_drops_compactly_and_escalates_doubts(weekly):
    import threading
    from grit.agents.weekly import PROMPTS
    from grit.pipeline import weekly as module
    pool, _, _, ids, original = weekly
    drops = _add_drops(pool, 200)
    seen, threads = [], set()
    lock = threading.Lock()
    def recording(*args, system, payload, **kwargs):
        with lock:
            seen.append((system, payload))
            threads.add(threading.get_ident())
        return original(*args, system=system, payload=payload, **kwargs)
    store, coordinator = _fast(pool, recording)
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    screened = [payload for system, payload in seen if system == PROMPTS["Screen"]]
    assert {key for payload in screened for key in payload["candidates"]} == set(drops) | {ids[0]}
    assert all("text" not in row and len(row["excerpt"]) <= module.SCREEN_TEXT_CHARS
               for payload in screened for row in payload["candidates"].values())
    assessed = {key for system, payload in seen if system == PROMPTS["Assessment"] for key in payload["candidates"]}
    assert assessed == {ids[0], ids[1]}  # the kept signal plus the drop the screen escalated
    assert len(threads) > 1
    editorial = {row["signal_id"]: row["editorial"]["decision"] for row in pool.weekly_assessments("R", include_excluded=True)}
    assert set(editorial) == set(coordinator.sources) and editorial[ids[0]] == "INCLUDE"
    assert {row["role"] for row in pool.rows("SELECT role FROM weekly_calls WHERE task LIKE 'screen-%'")} == {"Critic"}


def test_fast_review_reuses_unchanged_batches_after_the_pool_changes(weekly):
    from grit.agents.weekly import PROMPTS
    pool, _, _, ids, original = weekly
    _add_drops(pool, 400)
    calls = []
    def counting(*args, system, payload, **kwargs):
        calls.append(system)
        return original(*args, system=system, payload=payload, **kwargs)
    store, coordinator = _fast(pool, counting)
    coordinator.review(0)
    first = sum(system == PROMPTS["Screen"] for system in calls)
    assert first > 3
    version = store.state["version"]
    _add_drops(pool, 1, prefix="late")
    calls.clear()
    store, coordinator = _fast(pool, counting)
    assert store.state["version"] == version + 1
    coordinator.review(0)
    assert sum(system == PROMPTS["Screen"] for system in calls) <= 2 < first
    assert pool.one("SELECT COUNT(*) n FROM execution_log WHERE detail_json LIKE '%reused_from%'")["n"] > 0
    assert len(pool.weekly_assessments("R", include_excluded=True)) == len(coordinator.sources)


def test_switching_to_fast_review_starts_a_new_version_but_full_review_hash_is_unchanged(weekly):
    from grit.pipeline.weekly import WeeklyStore, analysis_hash
    pool, store, _, _, _ = weekly
    version = store.state["version"]
    before = analysis_hash(pool, "R")
    pool.update_run_config("R", weekly_review="full")
    assert analysis_hash(pool, "R") == before
    pool.update_run_config("R", weekly_review="fast")
    assert analysis_hash(pool, "R") != before
    assert WeeklyStore(pool, "R", search_limit=0).state["version"] == version + 1


def test_parallel_failure_keeps_completed_batches_for_resume(weekly):
    from grit.agents.loop.llm import LoopError
    from grit.agents.weekly import PROMPTS
    pool, _, _, ids, original = weekly
    drops = _add_drops(pool, 300)
    failing = {"on": True}
    def flaky(*args, system, payload, **kwargs):
        if failing["on"] and system == PROMPTS["Screen"] and sorted(payload["candidates"])[0] == sorted(drops)[0]:
            raise LoopError("the request failed: 503")
        return original(*args, system=system, payload=payload, **kwargs)
    store, coordinator = _fast(pool, flaky)
    with pytest.raises(LoopError):
        coordinator.review(0)
    assert store.state["status"] == "INCOMPLETE"
    validated = pool.one("SELECT COUNT(*) n FROM weekly_calls WHERE status = 'VALIDATED' AND task LIKE 'screen-%'")["n"]
    assert validated >= 1
    failing["on"] = False
    calls = []
    def counting(*args, system, payload, **kwargs):
        calls.append(system)
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = counting
    coordinator.review(0)
    assert sum(system == PROMPTS["Screen"] for system in calls) < pool.one(
        "SELECT COUNT(DISTINCT task) n FROM weekly_calls WHERE task LIKE 'screen-%'")["n"]


def test_out_of_batch_resolution_is_ignored_not_fatal(weekly):
    pool, store, coordinator, ids, _ = weekly
    open_issues = {"RES": {"issue": {"issue_id": "RES", "target": "RESEARCH"}}}
    raw = {"reviewed_ids": [ids[0]], "issues": [], "resolved_issue_ids": ["RES"],
           "resolutions": [{"issue_id": "RES", "response": "Covered elsewhere", "source_ids": [ids[0]]}]}
    coordinator.scoped({ids[0]}, coordinator.sources, open_issues, selections=False)(raw)
    assert raw["resolved_issue_ids"] == [] and raw["ignored_resolutions"] == ["RES"]


def test_cli_fast_review_needs_reasoned_mode():
    from grit.cli import build_parser
    args = build_parser().parse_args(["weekly", "--reasoned", "--fast", "--workers", "4", "--countersearch-limit", "0"])
    assert args.fast and args.workers == 4


def test_reporting_repairs_receive_previous_output_and_all_errors(weekly):
    pool, _, coordinator, _, _ = weekly
    replies = iter([{"problem": "number"}, {"problem": "citation"}, {"ok": True}])
    payloads = []
    def call(*args, payload, **kwargs):
        payloads.append(payload)
        return next(replies)
    def validate(raw):
        if not raw.get("ok"):
            raise ContractError(raw["problem"])
    coordinator.call = call
    result, _ = coordinator.invoke("Reporting", "repair-report", {}, validate)
    assert result == {"ok": True}
    assert payloads[1]["previous_output"] == {"problem": "number"}
    assert payloads[2]["previous_output"] == {"problem": "citation"}
    assert "number" in payloads[2]["contract_feedback"]
    assert "citation" in payloads[2]["contract_feedback"]
    assert pool.one("SELECT COUNT(*) AS n FROM execution_log WHERE actor = 'Reporting' AND status = 'REJECTED'")["n"] == 2


def test_reporting_repair_input_obeys_model_size_limit(weekly, monkeypatch):
    from grit.pipeline import weekly as module
    pool, store, coordinator, _, _ = weekly
    calls = []
    def call(*args, **kwargs):
        calls.append(1)
        return {"draft": "x" * 2000}
    def validate(raw):
        raise ContractError("The draft needs repair.")
    monkeypatch.setattr(module, "MAX_PAYLOAD_CHARS", 1000)
    coordinator.call = call
    with pytest.raises(ContractError, match="repair input"):
        coordinator.invoke("Reporting", "large-repair", {}, validate)
    assert calls == [1]
    assert store.state["status"] == pool.run("R")["status"] == "INCOMPLETE"


def _failed_report_checkpoint(weekly, monkeypatch, *, material=False):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    if material:
        research = pool.one("SELECT call_id FROM weekly_calls WHERE role = 'Researcher' AND status = 'VALIDATED'")
        store._issue(research["call_id"], "PAGE", "OPEN", {
            "issue_id": "PAGE", "target": ids[0], "kind": "COVERAGE", "material": True,
            "problem": "Page verification did not run.", "source_ids": [ids[0]], "missing_evidence": "Budget exhausted",
        })
        assert store.reserve("revision") and store.reserve("revision")
    coordinator.supervise(["DRAFT_REPORT"], {"stage": "pool reconciled", "issues": store.issues()})
    profile = _profile()
    profile.brief_for = lambda pack: {"formats": ["docx"]}
    monkeypatch.setattr("grit.report.brief.build_brief", lambda *args: _draft(ids[0]))
    monkeypatch.setattr("grit.report.brief.routed_signals", lambda *args: [])
    attempts = []
    def invalid(*args, system, payload, **kwargs):
        if system != PROMPTS["Reporting"]:
            return original(*args, system=system, payload=payload, **kwargs)
        attempts.append(payload)
        text = f"Alpha has 28 airports [{ids[0]}]." if len(attempts) == 1 else f"Alpha is open. A second claim is made [{ids[0]}]."
        return {"edits": [{"path": path, "text": text, "source_ids": [ids[0]]}
                          for path in ("bottom_line.text", "bottom_line.escalation")]}
    coordinator.call = invalid
    with pytest.raises(ContractError, match="Cite each factual sentence"):
        coordinator.report(profile, _draft(ids[0]), [])
    assert len(attempts) == 4
    assert store.state["status"] == pool.run("R")["status"] == "INCOMPLETE"
    return profile


@pytest.mark.parametrize("material", [False, True])
def test_resume_reporting_uses_saved_review_and_preserves_material_issues(weekly, monkeypatch, material):
    from types import SimpleNamespace
    from grit.agents.weekly import PROMPTS
    from grit.pipeline.weekly import frozen_report, resume_weekly_reporting
    pool, store, _, _, original = weekly
    profile = _failed_report_checkpoint(weekly, monkeypatch, material=material)
    calls = []
    def report_only(*args, system, payload, **kwargs):
        assert system == PROMPTS["Reporting"] or (system == PROMPTS["Critic"] and "targets" in payload)
        calls.append((system, kwargs["model"]))
        return original(*args, system=system, payload=payload, **kwargs)
    before = pool.one("SELECT COUNT(*) AS n FROM weekly_selections")["n"]
    budget = (store.state["searches_used"], store.state["revisions_used"])
    result = resume_weekly_reporting(pool, SimpleNamespace(run_id="R", pack="test"), client=None, model=None,
                                     profiles=[profile], call=report_only)
    assert result["status"] == ("INCOMPLETE" if material else "READY")
    assert pool.run("R")["status"] == ("INCOMPLETE" if material else "COMPLETE")
    assert (store.state["searches_used"], store.state["revisions_used"]) == budget
    assert pool.one("SELECT COUNT(*) AS n FROM weekly_selections")["n"] == before
    assert calls == [(PROMPTS["Reporting"], "offline"), (PROMPTS["Critic"], "offline")]
    assert frozen_report(pool, "R", "reader", ready=False)["status"] == result["status"]
    if material:
        with pytest.raises(ValueError, match="not ready"):
            frozen_report(pool, "R", "reader")
        assert store.issues()["PAGE"]["issue"]["material"]


def test_final_critic_cannot_resolve_signal_coverage_from_a_report_review(weekly, monkeypatch):
    from types import SimpleNamespace
    from grit.agents.weekly import PROMPTS
    from grit.pipeline.weekly import resume_weekly_reporting
    pool, store, _, _, original = weekly
    profile = _failed_report_checkpoint(weekly, monkeypatch, material=True)
    def report_with_out_of_scope_resolution(*args, system, payload, **kwargs):
        if system == PROMPTS["Critic"] and "targets" in payload:
            return {"reviewed_ids": payload["targets"], "issues": [], "resolved_issue_ids": ["PAGE"],
                    "resolutions": [{"issue_id": "PAGE", "response": "The report mentions it.", "source_ids": []}]}
        return original(*args, system=system, payload=payload, **kwargs)
    result = resume_weekly_reporting(pool, SimpleNamespace(run_id="R", pack="test"), client=None, model=None,
                                     profiles=[profile], call=report_with_out_of_scope_resolution)
    assert result["status"] == "INCOMPLETE"
    assert store.issues()["PAGE"]["issue"]["material"]
    call = pool.one("SELECT * FROM weekly_calls WHERE role = 'Critic' AND task = 'final-reader' ORDER BY call_id DESC")
    assert call["output"]["resolved_issue_ids"] == []
    assert call["output"]["ignored_resolutions"] == ["PAGE"]


@pytest.mark.parametrize("changed", ["evidence", "selection", "draft", "issues", "budget"])
def test_resume_reporting_rejects_changed_checkpoint(weekly, monkeypatch, changed):
    from types import SimpleNamespace
    from grit.pipeline.weekly import resume_weekly_reporting
    pool, store, _, ids, _ = weekly
    profile = _failed_report_checkpoint(weekly, monkeypatch)
    if changed == "evidence":
        pool.save_assessment({"signal_id": ids[0], "run_id": "R", "pack": "test", "decision": "DROP", "drop_reason": "STALE"})
    elif changed == "selection":
        assessment = pool.one("SELECT * FROM weekly_calls WHERE role = 'Assessment' AND status = 'VALIDATED' LIMIT 1")
        store.select(assessment["call_id"], assessment["output"]["selections"])
    elif changed == "draft":
        draft = _draft(ids[0])
        draft["bottom_line"]["text"] = "Changed draft"
        monkeypatch.setattr("grit.report.brief.build_brief", lambda *args: draft)
    elif changed == "issues":
        call_id = pool.one("SELECT call_id FROM weekly_calls WHERE role = 'Researcher'")["call_id"]
        store._issue(call_id, "NEW", "OPEN", {"issue_id": "NEW", "target": ids[0], "kind": "COVERAGE",
                     "problem": "New check", "material": True, "source_ids": [], "missing_evidence": "New evidence"})
    else:
        assert store.reserve("revision")
    with pytest.raises(ValueError, match="changed|validated Critic"):
        resume_weekly_reporting(pool, SimpleNamespace(run_id="R", pack="test"), client=None, model=None, profiles=[profile],
                                call=lambda *args, **kwargs: pytest.fail("Changed checkpoints must not call the model"))
    assert store.state["status"] == "INCOMPLETE"


def test_resume_report_cli_can_render_an_incomplete_preview(weekly, monkeypatch):
    from grit import cli
    pool, _, _, _, _ = weekly
    args = cli.build_parser().parse_args(["resume-report", "--pack", "test", "--date", "2026-11-02", "--no-submit"])
    seen = []
    monkeypatch.setattr(cli, "_pool", lambda: pool)
    monkeypatch.setattr(cli, "_packs", lambda args: ["test"])
    monkeypatch.setattr(cli, "_profiles", lambda names: [])
    monkeypatch.setattr(cli, "_client", lambda: None)
    monkeypatch.setattr("grit.pipeline.weekly.resume_weekly_reporting",
                        lambda pool, run, **kwargs: seen.append((run.run_id, kwargs["model"])) or {"status": "INCOMPLETE"})
    monkeypatch.setattr(cli, "report_step", lambda pool, args, pack: seen.append((pack, args.no_submit)))
    assert args.handler(args) == 2
    assert seen == [("2026-11-02-test", None), ("test", True)]


def test_a_later_text_for_the_same_page_is_kept_as_a_revision_and_read_first(weekly):
    from grit.pipeline.weekly import analysis_hash, evidence_text
    pool, store, coordinator, ids, _ = weekly
    before = analysis_hash(pool, "R")
    url = "https://news.example/Alpha"
    pool.add_signal(url=url, title="Alpha operations", text="The Alpha airport is open for commercial operations.",
                    publisher="News", published_at="2026-10-01", lane="TEST", content_hash="Alpha")
    pool.add_signal(url=url, title="Alpha operations", text="Another query's snippet of the same Alpha page.",
                    publisher="News", published_at="2026-10-01", lane="TEST", content_hash="Alpha-snippet")
    assert analysis_hash(pool, "R") == before, "the same text, or a snippet that varies by query, is not a revision"
    pool.add_signal(url=url, title="Alpha airport closed", text="Update: the Alpha airport closed on 30 October after a strike.",
                    publisher="News", published_at="2026-10-31", lane="TEST", content_hash="Alpha-2", seen_at="2026-11-01T08:00:00Z")
    pool.add_signal(url=url, title="Alpha airport reopens", text="The Alpha airport reopened on 4 November.",
                    publisher="News", published_at="2026-11-04", lane="TEST", content_hash="Alpha-3", seen_at="2026-11-05T08:00:00Z")
    assert pool.signal(ids[0])["text"] == "The Alpha airport is open for commercial operations.", "the first text is not overwritten"
    source = coordinator.sources[ids[0]]
    assert source["text"].startswith("Updated page text (published 2026-10-31, seen 2026-11-01):\nUpdate: the Alpha airport closed on 30 October")
    assert "reopened" not in source["text"], "a revision seen after the run date is hindsight and is not read"
    assert "Text first stored:\nThe Alpha airport is open" in source["text"]
    assert source["event_dates"] == ["2026-10-30"]
    assert analysis_hash(pool, "R") != before, "new text is new evidence"
    verified = {"text": "Stored.", "evidence_records": [{"status": "SUCCEEDED", "scope": "EXTRACTED_PAGE", "text": "Verified page."}]}
    assert evidence_text(verified).startswith("Extracted source page (verified):\nVerified page.")
    assert evidence_text({"text": "Stored."}) == "Stored."


def test_a_page_check_after_the_run_date_labels_the_text_not_the_source():
    from grit.pipeline.weekly import evidence_text, late_evidence
    check = {"status": "SUCCEEDED", "scope": "EXTRACTED_PAGE", "text": "Verified page.", "retrieved_at": "2026-11-03T09:00:00Z"}
    assert late_evidence(check, "2026-11-02") and not late_evidence({**check, "retrieved_at": "2026-11-02T23:00:00Z"}, "2026-11-02")
    assert not late_evidence({**check, "status": "FAILED"}, "2026-11-02")
    text = evidence_text({"text": "Stored.", "evidence_records": [check]}, "2026-11-02")
    assert text.startswith("Extracted source page (verified, retrieved after the run date: hindsight):\nVerified page.")
    assert "Text first stored:\nStored." in text


def test_a_late_page_check_keeps_an_earlier_source_out_of_hindsight(weekly):
    pool, store, coordinator, ids, _ = weekly
    pool.insert("weekly_evidence", {"run_id": "R", "signal_id": ids[0], "kind": "PAGE_VERIFICATION", "created_at": "2026-11-03T09:00:00Z",
                                    "provenance_json": {"status": "SUCCEEDED", "scope": "EXTRACTED_PAGE", "text": "Alpha is open.",
                                                        "retrieved_at": "2026-11-03T09:00:00Z"}})
    source = coordinator.sources[ids[0]]
    assert source["time_scope"] == "EARLIER_CONTEXT", "the publication date sets the scope"
    assert source["hindsight_checks"] is True
    assert "retrieved after the run date: hindsight" in source["text"]
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    editorial = {row["signal_id"]: row["editorial"] for row in pool.weekly_assessments("R", include_excluded=True)}[ids[0]]
    assert editorial["time_scope"] == "EARLIER_CONTEXT" and editorial["hindsight_checks"] is True


def test_event_dates_come_from_the_title_and_lead_not_the_publication_date():
    from grit.pipeline.weekly import event_dates
    row = {"title": "Port strike ends", "text": "Workers at the port ended a strike that began on 12 September. Talks resume on 9 November.", "published_at": "2026-11-01"}
    assert event_dates(row, date(2026, 11, 2)) == ["2026-09-12", "2026-11-09"]
    assert event_dates({"title": "No dates", "text": "Nothing stated."}, date(2026, 11, 2)) == []
    from grit.report.brief import citations
    key = "SIG-0123456789abcdef"
    _, refs = citations(f"Strike ended [{key}].", {key: {"publisher": "P", "published_at": "2026-11-01", "event_dates": ["2026-09-12", "2026-11-01"]}})
    assert refs[0]["when"] == "1 Nov 2026; event dated 12 Sep 2026" and refs[0]["date"] == "1 Nov 2026"


def test_confirmed_exposure_needs_approved_internal_evidence(weekly):
    from grit.pipeline.weekly import add_internal_evidence
    pool, store, coordinator, ids, _ = weekly
    with pytest.raises(ValueError, match="exposure"):
        add_internal_evidence(pool, "R", [{"title": "Site", "text": "Site check.", "reference": "Doc 1", "exposure": {"type": "OFFICE", "status": "CONFIRMED", "entity": "STE A"}}], _profile())
    added = add_internal_evidence(pool, "R", [{"title": "Site register", "text": "STE A operates a depot at the Alpha airport.", "reference": "Site register 2026",
                                              "exposure": {"type": "SITE", "status": "CONFIRMED", "entity": "STE A"}}], _profile())
    sources = coordinator.sources
    assert sources[added[0]]["internal_exposure"] == [{"type": "SITE", "status": "CONFIRMED", "entity": "STE A"}]
    confirmed = {"selections": [{"signal_id": ids[0], "decision": "INCLUDE", "reason": "Depot at the airport", "source_ids": [ids[0], added[0]], "exposure_status": "CONFIRMED", **CHAIN}]}
    assert check_selections(confirmed, sources, {ids[0]})["selections"][0]["exposure_status"] == "CONFIRMED"
    public = {"selections": [{"signal_id": ids[0], "decision": "INCLUDE", "reason": "Airport named", "source_ids": [ids[0]], "exposure_status": "CONFIRMED", **CHAIN}]}
    row = check_selections(public, sources, {ids[0]})["selections"][0]
    assert row["exposure_status"] == "POSSIBLE" and row["exposure_status_downgraded"] == "CONFIRMED"
    with pytest.raises(ContractError, match="exposure_status"):
        check_selections({"selections": [{**public["selections"][0], "exposure_status": "LIKELY"}]}, sources, {ids[0]})


def test_include_needs_the_reasoning_chain_but_the_drop_screen_does_not():
    row = {"signal_id": "A", "decision": "INCLUDE", "reason": "Named", "source_ids": ["A"], "fact": "Port closed", "exposure": " "}
    with pytest.raises(ContractError, match="exposure, transmission, impact"):
        check_selections({"selections": [dict(row)]}, {"A": {}}, {"A"})
    check_selections({"selections": [dict(row)]}, {"A": {}}, {"A"}, chain=False)
    check_selections({"selections": [{**row, **CHAIN}]}, {"A": {}}, {"A"})
    check_selections({"selections": [{"signal_id": "A", "decision": "EXCLUDE", "reason": "Other firm", "source_ids": ["A"]}]}, {"A": {}}, {"A"})


@pytest.mark.parametrize("weekly", [0, 1], indirect=True)
def test_an_unchallenged_top_finding_opens_a_contradiction_issue(weekly):
    pool, store, coordinator, ids, _ = weekly
    coordinator.assess_and_critique(0)
    coordinator.research("countercheck", None, lambda: None)
    issue = store.issues()[f"CONTRA-{ids[0]}"]
    assert issue["issue"]["target"] == "RESEARCH" and issue["issue"]["source_ids"] == [ids[0]]
    # Material only when the operator allowed searches; a zero budget is a disclosed limitation.
    assert issue["issue"]["material"] is (store.state["search_limit"] > 0)
    brief = {"v2": {"gate": [], "completeness": {"blocking": [], "limitations": []}}}
    coordinator.disclose_contradiction(brief, coordinator.sources)
    row = brief["v2"]["gate"][0]
    assert row["answer"] == "No" and not row["critical"] and row["note"].startswith("0 of 1 top findings")
    assert ("disabled" in row["note"]) is (store.state["search_limit"] == 0)
    assert brief["v2"]["completeness"]["limitations"]


@pytest.mark.parametrize("weekly", [2], indirect=True)
def test_a_completed_contradiction_search_resolves_the_issue_and_runs_first(weekly):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    coordinator.assess_and_critique(0)
    coordinator.research("first", None, lambda: None)
    assert f"CONTRA-{ids[0]}" in store.issues()
    def requested(*args, system, payload, **kwargs):
        if system == PROMPTS["Researcher"]:
            assert payload["top_findings"] == [ids[0]]
            return {"queries": [
                {"query": "Alpha wider sector", "reason": "Coverage", "signal_ids": [], "purpose": "COVERAGE"},
                {"query": "Alpha sector peers", "reason": "Coverage", "signal_ids": []},
                {"query": "Alpha airport closed denied", "reason": "Disprove", "signal_ids": [ids[0]], "purpose": "CONTRADICT"}],
                "missing_checks": []}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = requested
    searched = []
    coordinator.research("second", lambda q: searched.append(q) or {"status": "SUCCEEDED", "candidates": []}, lambda: None)
    assert searched[0] == "Alpha airport closed denied" and len(searched) == 2
    assert f"CONTRA-{ids[0]}" not in store.issues()


def test_a_contradiction_search_purpose_must_be_known(weekly):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    calls = []
    def requested(*args, system, payload, **kwargs):
        if system == PROMPTS["Researcher"]:
            calls.append(1)
            return {"queries": [{"query": "Alpha", "reason": "Check", "signal_ids": [ids[0]], "purpose": "DISPROVE"}], "missing_checks": []}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = requested
    with pytest.raises(Exception):
        coordinator.research("bad", None, lambda: None)
    assert len(calls) == 3


def test_screen_excerpt_keeps_the_lead_and_sentences_naming_matched_terms():
    from grit.pipeline.weekly import screen_view
    text = "Lead paragraph about markets. " * 12 + "Nothing here. " * 30 + "Raytheon delays the missile contract. " + "Filler. " * 40
    row = {"signal_id": "A", "title": "T", "text": text,
           "link": {"involves": [{"matched": "Raytheon"}], "reader_hits": [], "lob_keywords": ["missile"], "hazards": []}}
    view = screen_view(row)
    assert view["excerpt"].startswith("Lead paragraph")
    assert "Raytheon delays the missile contract." in view["excerpt"]
    assert len(view["excerpt"]) <= 400
    assert view["matched_terms"] == ["Raytheon", "missile"]


def test_significance_is_required_and_capped_by_the_evidence():
    base = {"signal_id": "A", "decision": "WATCH", "reason": "Named", "source_ids": ["A"], **CHAIN}
    with pytest.raises(ContractError, match="significance"):
        check_selections({"selections": [{**base, "significance": {"exposure": 1}}]}, {"A": {}}, {"A"})
    with pytest.raises(ContractError, match="significance"):
        check_selections({"selections": [{**base, "significance": {**CHAIN["significance"], "change": 3}}]}, {"A": {}}, {"A"})
    high = {"exposure": 3, "consequence": 3, "immediacy": 3, "evidence": 3, "change": 2}
    sources = {"A": {"evidence_confidence": "MEDIUM", "corroboration": 1}}
    row = check_selections({"selections": [{**base, "significance": dict(high)}]}, sources, {"A"})["selections"][0]
    assert row["significance"] == {**high, "exposure": 2, "evidence": 2} and row["significance_capped"] == high
    sources = {"A": {"evidence_confidence": "HIGH", "corroboration": 2}}
    row = check_selections({"selections": [{**base, "significance": {**high, "exposure": 2}}]}, sources, {"A"})["selections"][0]
    assert "significance_capped" not in row


def test_significance_bands_follow_the_methodology():
    from grit.agents.weekly import significance_band
    assert [significance_band(score) for score in (14, 11, 10, 8, 7, 5, 4)] == ["TOP", "TOP", "SIGNIFICANT", "SIGNIFICANT", "SECONDARY", "SECONDARY", "LOW"]
    assert significance_band(2, immediate_threat=True) == "TOP"


def _with_selection(original, **changes):
    from grit.agents.weekly import PROMPTS
    def call(*args, system, payload, **kwargs):
        response = original(*args, system=system, payload=payload, **kwargs)
        if system in (PROMPTS["Assessment"], PROMPTS["Critic"]) and "selections" in response:
            for row in response["selections"]:
                if row["decision"] == "INCLUDE":
                    row.update(copy.deepcopy(changes))
        return response
    return call


def test_status_is_watchpoint_for_a_headline_red_candidate_without_confirmed_impact(weekly):
    pool, store, coordinator, ids, original = weekly
    pool.save_assessment({"signal_id": ids[0], "run_id": "R", "pack": "test", "decision": "DROP", "red": 1,
                          "harm_json": {"status": "NOT_RUN", "reason": "No structured observations"}})
    coordinator.call = _with_selection(original, impact_status="CONFIRMED")
    coordinator.assess_and_critique(0)
    policy = coordinator.status_policy()
    assert policy["status"] == "WATCHPOINT" and policy["red_candidates"] == [ids[0]]
    assert "RED alert candidates matched a headline pattern" in policy["basis"]
    assert pool.weekly_assessments("R")[0]["editorial"]["impact_status"] == "PLAUSIBLE"


def test_status_is_elevated_for_an_immediate_threat_and_red_only_for_confirmed_impact(weekly):
    from grit.pipeline.weekly import add_internal_evidence
    pool, store, coordinator, ids, original = weekly
    coordinator.call = _with_selection(original, immediate_threat=True, exposure_status="POSSIBLE")
    coordinator.assess_and_critique(0)
    policy = coordinator.status_policy()
    assert policy["status"] == "ELEVATED" and policy["signal_ids"] == [ids[0]]
    assert policy["priorities"][0]["band"] == "TOP"
    added = add_internal_evidence(pool, "R", [{"title": "Incident log", "text": "STE A depot at the Alpha airport closed on 1 November.",
                                              "reference": "Incident 7", "exposure": {"type": "SITE", "status": "CONFIRMED", "entity": "STE A"}}], _profile())
    pool.save_assessment({"signal_id": added[0], "run_id": "R", "pack": "test", "decision": "DROP", "drop_reason": "NOT_RELEVANT"})
    coordinator.call = _with_selection(original, impact_status="CONFIRMED", exposure_status="CONFIRMED", source_ids=[ids[0], added[0]])
    coordinator.assess_and_critique(1)
    policy = coordinator.status_policy()
    # Assessed impact with internal evidence still waits for the Risk & Assurance operator.
    assert policy["status"] == "ELEVATED" and policy["red_awaiting_confirmation"] == [ids[0]]
    assert "await R&A confirmation" in policy["basis"]
    pool.record_red_decision(pack="test", signal_id=ids[0], decision="CONFIRMED", impact="Depot closed; deliveries stopped.", decided_by="Jony Ling")
    policy = coordinator.status_policy()
    assert policy["status"] == "RED" and policy["signal_ids"] == [ids[0]] and policy["red_awaiting_confirmation"] == []
    brief = {"v2": {"status": "STABLE", "gate": []}, "bottom_line": {"label": "STABLE"}}
    coordinator.prepare_report(brief, coordinator.sources)
    assert brief["v2"]["status"] == brief["bottom_line"]["label"] == "RED"
    assert brief["v2"]["status_basis"]["basis"].startswith("The Risk & Assurance operator confirmed the impact of 1 findings")
    pool.record_red_decision(pack="test", signal_id=ids[0], decision="NOT_CONFIRMED", impact="Depot reopened the same day.", decided_by="Jony Ling")
    policy = coordinator.status_policy()
    assert policy["status"] == "WATCHPOINT" and "R&A did not confirm RED impact for 1 findings" in policy["basis"]


def test_a_reviewed_selection_can_remove_a_keyword_risk_link_but_never_add_one(weekly):
    pool, store, coordinator, ids, original = weekly
    cyber = [{"risk_id": "KEY-04", "level": "ENTITY", "name": "Cyber", "matched": "KEYWORD_MATCH"},
             {"risk_id": "URS-KEY-04", "level": "BA", "name": "Cyber (BA)", "matched": "link theme hit"},
             {"risk_id": "C1.1", "level": "GROUP", "name": "Cyber terrorism", "matched": "URS-KEY-04 (PROPOSED mapping)"},
             {"risk_id": "URS-KEY-03", "level": "BA", "name": "Project costs", "matched": "link theme hit"}]
    pool.save_assessment({"signal_id": ids[0], "run_id": "R", "pack": "test", "decision": "KEEP", "condition_ids_json": ["URS-KEY-03", "URS-KEY-04"],
                          "risk_cascade_json": cyber, "harm_json": {"status": "NOT_RUN", "reason": "No structured observations"}})
    coordinator.call = _with_selection(original, risk_ids=["URS-KEY-03", "URS-KEY-99"])
    coordinator.assess_and_critique(0)
    (row,) = [row for row in pool.weekly_assessments("R") if row["signal_id"] == ids[0]]
    assert row["editorial"]["risk_ids"] == ["URS-KEY-03"] and row["editorial"]["risk_ids_dropped"] == ["URS-KEY-99"]
    assert row["condition_ids"] == ["URS-KEY-03"] and row["keyword_condition_ids"] == ["URS-KEY-03", "URS-KEY-04"]
    assert [hit["risk_id"] for hit in row["risk_cascade"]] == ["URS-KEY-03"]


@pytest.mark.parametrize("weekly", [3], indirect=True)
def test_a_repeated_contradiction_search_does_not_spend_the_budget(weekly):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    coordinator.assess_and_critique(0)
    queries = iter([[{"query": "Alpha airport closed denied", "reason": "Disprove", "signal_ids": [ids[0]], "purpose": "CONTRADICT"}],
                    [{"query": "Alpha airport closure retracted", "reason": "Disprove again", "signal_ids": [ids[0]], "purpose": "CONTRADICT"},
                     {"query": "Alpha sector peers", "reason": "Coverage", "signal_ids": [], "purpose": "COVERAGE"}]])
    def requested(*args, system, payload, **kwargs):
        if system == PROMPTS["Researcher"]:
            return {"queries": next(queries), "missing_checks": []}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = requested
    searched = []
    search = lambda q: searched.append(q) or {"status": "SUCCEEDED", "candidates": []}
    coordinator.research("first", search, lambda: None)
    coordinator.research("second", search, lambda: None)
    assert searched == ["Alpha airport closed denied", "Alpha sector peers"] and store.state["searches_used"] == 2


@pytest.mark.parametrize("weekly", [3], indirect=True)
def test_a_page_the_source_refused_is_disclosed_and_not_fetched_again(weekly, monkeypatch):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    def requested(*args, system, payload, **kwargs):
        if system == PROMPTS["Researcher"]:
            return {"queries": [], "missing_checks": [], "verify_source_ids": [ids[0]]}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = requested
    fetched = []
    monkeypatch.setattr("grit.agents.weekly_evidence.verify_page", lambda url: fetched.append(url) or {
        "status": "NOT_CHECKED", "http_status": 403, "reason": "The source did not return HTTP 200."})
    coordinator.research("first", None, lambda: None)
    coordinator.research("second", None, lambda: None)
    assert len(fetched) == 1 and store.state["searches_used"] == 1
    issue = store.issues()[f"PAGE-{ids[0]}"]["issue"]
    assert issue["material"] is False and "relies on the retrieved text" in issue["problem"]
    brief = {"v2": {"completeness": {}}}
    coordinator.disclose_unverified_pages(brief)
    assert ids[0] in brief["v2"]["completeness"]["limitations"][0]


def test_a_removed_risk_link_is_no_longer_a_strong_theme():
    from grit.pool.db import reviewed_links
    row = {"condition_ids": ["URS-KEY-04"], "risk_cascade": [], "editorial": {"risk_ids": []},
           "themes": [{"theme_id": "URS-KEY-04", "strong": True}, {"theme_id": "URS-KEY-01", "strong": False}]}
    themes = reviewed_links(row)["themes"]
    assert themes[0]["strong"] is False and themes[0]["reviewed_out"] and themes[1] == {"theme_id": "URS-KEY-01", "strong": False}


def test_a_report_critique_sets_aside_selections_it_was_not_asked_for(weekly):
    pool, store, coordinator, ids, _ = weekly
    validate = coordinator.scoped({"REPORT:a"}, coordinator.sources, {}, selections=False)
    raw = {"reviewed_ids": ["REPORT:a"], "issues": [], "resolved_issue_ids": [], "resolutions": [],
           "selections": [{"signal_id": ids[0], "decision": "INCLUDE", "tier": 1}]}
    validate(raw)
    assert "selections" not in raw and raw["ignored_selections"] == 1


def test_the_final_critic_is_told_which_report_values_are_set_by_rule():
    from grit.pipeline.weekly import fixed_values
    brief = {"v2": {"status": "ELEVATED", "status_basis": {"basis": "2 included findings ..."}},
             "conditions": [{"condition_id": "URS-TIER_2-01", "trend": "Escalating"}, {"name": "no id"}]}
    value = fixed_values(brief)
    assert value["status"] == "ELEVATED" and value["status_basis"] == "2 included findings ..."
    assert value["condition_trends"] == {"URS-TIER_2-01": "Escalating"} and "non-material" in value["rule"]


@pytest.mark.parametrize("text, broken", [
    ("In the Middle East, the U.S. State Department ordered non-emergency staff to leave", False),
    ("J. Smith of ST Engineering Pte. Ltd confirmed the award", False),
    ("Contracts rose (U.K. Ministry figures) after St. Louis talks", False),
    ("Alpha is open. A second claim is made", True),
    ("Order intake was S$5.6B. The urban segment grew", True),
    ("Is the line open? Officials did not say", True),
])
def test_a_sentence_break_ignores_initials_and_abbreviations(text, broken):
    from grit.pipeline.weekly import sentence_break
    assert bool(sentence_break(text)) is broken


def test_a_deferred_candidate_is_revisited_and_blocks_readiness_only_while_deferred(weekly):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    rounds = []
    def deferring(*args, system, payload, **kwargs):
        response = original(*args, system=system, payload=payload, **kwargs)
        if system in (PROMPTS["Assessment"], PROMPTS["Critic"]) and "selections" in response:
            rounds.append(payload["task"])
            for row in response["selections"]:
                if row["signal_id"] == ids[0] and payload["task"].startswith(("pool-0", "pool-1")):
                    row.update(decision="DEFER", reason="Needs the operator notice.")
        return response
    coordinator.call = deferring
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    issue = pool.one("SELECT * FROM weekly_issues WHERE issue_id = ? ORDER BY revision_id LIMIT 1", (f"DEFER-{ids[0]}",))
    assert issue and issue["issue"]["material"] and "Needs the operator notice." in issue["issue"]["problem"]
    assert f"DEFER-{ids[0]}" not in store.issues(), "the revision round decided the candidate, which resolves the issue"
    assert any(task.startswith("pool-2") or task.startswith("pool-3") for task in rounds)


def test_an_incomplete_report_states_what_blocks_it(weekly):
    pool, store, coordinator, ids, call = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    ready = coordinator.report(_profile(), _draft(ids[0]), [])
    assert ready["status"] == "READY" and ready["blocked_by"] == []
    draft = _draft(ids[0])
    draft["evidence_ids"] = [ids[0], "SIG-0000000000000000"]
    blocked = coordinator.report(_profile("other"), draft, [])
    assert blocked["status"] == "INCOMPLETE"
    assert blocked["blocked_by"] == ["Cites signals the review did not cover: SIG-0000000000000000."]


def test_any_material_objection_blocks_every_report_and_is_named(weekly):
    pool, store, coordinator, ids, call = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    coordinator.supervise(["DRAFT_REPORT"], {"stage": "pool reconciled", "issues": store.issues()})
    call_id = pool.one("SELECT MAX(call_id) AS id FROM weekly_calls WHERE run_id = 'R'")["id"]
    store._issue(call_id, "OTHER-REPORT", "OPEN", {"issue_id": "OTHER-REPORT", "target": "REPORT:other", "kind": "NARRATIVE",
                                                   "material": True, "problem": "Overstated.", "source_ids": []})
    blocked = coordinator.report(_profile("reader"), _draft(ids[0]), [])
    assert blocked["status"] == "INCOMPLETE" and blocked["blocked_by"] == ["Open material issue OTHER-REPORT: Overstated."]


def test_a_condition_pack_keeps_its_cascade_and_themes_when_no_link_is_removed():
    from grit.pool.db import reviewed_links
    cascade = [{"risk_id": "C1.1", "level": "GROUP", "name": "Cyber terrorism", "matched": "URS-KEY-04 (PROPOSED mapping)"},
               {"risk_id": "KEY-02", "level": "ENTITY", "name": "Supply", "matched": "KEYWORD_MATCH"}]
    row = {"condition_ids": ["TW-GLOBAL-01"], "risk_cascade": cascade, "editorial": {"risk_ids": ["TW-GLOBAL-01"]},
           "themes": [{"theme_id": "URS-KEY-02", "strong": True}]}
    reviewed = reviewed_links(row)
    assert reviewed["risk_cascade"] == cascade and reviewed["themes"] == row["themes"]
    assert reviewed["condition_ids"] == reviewed["keyword_condition_ids"] == ["TW-GLOBAL-01"]
    removed = reviewed_links({**row, "editorial": {"risk_ids": []}})
    assert removed["condition_ids"] == [] and removed["risk_cascade"] == cascade, "nothing in the cascade traces to TW-GLOBAL-01"


def test_a_null_source_list_on_an_issue_is_an_empty_one():
    from grit.agents.weekly import check_critique
    raw = {"reviewed_ids": ["A"], "resolved_issue_ids": [], "resolutions": [],
           "issues": [{"issue_id": "I", "kind": "INTERNAL_EVIDENCE", "target": "A", "material": False,
                       "problem": "No internal check.", "source_ids": None, "missing_evidence": "Site register"}]}
    assert check_critique(raw, {"A"}, {"A": {"signal_id": "A"}}, set())["issues"][0]["source_ids"] == []
    with pytest.raises(ContractError, match="Unknown source_ids: SIG-X"):
        check_critique({**raw, "issues": [{**raw["issues"][0], "source_ids": ["SIG-X"]}]}, {"A"}, {"A": {"signal_id": "A"}}, set())


def test_history_lookups_and_covered_signals_are_not_searches(tmp_path):
    from grit.pool import Pool
    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="test", kind="READER_SCAN")
    for lane in ("THEME", "HISTORICAL_EVIDENCE", "HUMAN_APPROVED", "COVERED_BY_CHILD"):
        signal = pool.add_signal(url=f"https://news.example/{lane}", title=lane, text="Taiwan typhoon depot", publisher="News",
                                 published_at="2026-11-01", lane=lane, content_hash=lane)
        pool.add_sighting(signal, "R", query_id=f"q-{lane}", query=f"Taiwan {lane}", lane=lane, target_id=None, rank=1)
    assert [row["lane"] for row in pool.answered_searches("R")] == ["THEME"]


def test_a_resolution_outside_the_reviewed_scope_is_ignored_not_fatal(weekly):
    pool, store, coordinator, ids, original = weekly
    coordinator.assess_and_critique(0)
    call_id = pool.one("SELECT MAX(call_id) AS id FROM weekly_calls WHERE run_id = 'R'")["id"]
    store._issue(call_id, "CONTRA-X", "OPEN", {"issue_id": "CONTRA-X", "target": "RESEARCH", "kind": "COVERAGE", "material": True,
                                               "problem": "No contradiction search.", "source_ids": [ids[0]]})
    store.record_critique(call_id, {"reviewed_ids": [ids[0]], "issues": [], "resolved_issue_ids": ["CONTRA-X"],
                                    "resolutions": [{"issue_id": "CONTRA-X", "response": "Looks fine.", "source_ids": []}]})
    assert "CONTRA-X" in store.issues()


@pytest.mark.parametrize("weekly", [2], indirect=True)
def test_a_finding_that_moves_into_the_top_set_after_research_needs_its_own_search(weekly, monkeypatch):
    from grit.agents.weekly import PROMPTS
    pool, store, coordinator, ids, original = weekly
    coordinator.assess_and_critique(0)
    def requested(*args, system, payload, **kwargs):
        if system == PROMPTS["Researcher"]:
            return {"queries": [{"query": "Alpha airport closed denied", "reason": "Disprove", "signal_ids": [ids[0]], "purpose": "CONTRADICT"}],
                    "missing_checks": []}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = requested
    monkeypatch.setattr(coordinator, "top_findings", lambda sources, included: [ids[0]])
    coordinator.research("first", lambda q: {"status": "SUCCEEDED", "candidates": []}, lambda: None)
    assert f"CONTRA-{ids[0]}" not in store.issues()
    monkeypatch.setattr(coordinator, "top_findings", lambda sources, included: [ids[1], ids[0]])
    coordinator.check_contradiction()
    assert store.issues()[f"CONTRA-{ids[1]}"]["issue"]["material"] and f"CONTRA-{ids[0]}" not in store.issues()
    monkeypatch.setattr(coordinator, "top_findings", lambda sources, included: [ids[0]])
    coordinator.check_contradiction()
    assert f"CONTRA-{ids[1]}" not in store.issues(), "a finding that left the top set no longer needs the search"


def test_the_status_basis_counts_red_candidates_the_review_excluded(weekly):
    pool, store, coordinator, ids, original = weekly
    pool.save_assessment({"signal_id": ids[1], "run_id": "R", "pack": "test", "decision": "KEEP", "red": 1,
                          "harm_json": {"status": "NOT_RUN", "reason": "No structured observations"}})
    coordinator.assess_and_critique(0)
    policy = coordinator.status_policy()
    assert policy["red_excluded"] == [ids[1]] and policy["red_candidates"] == []
    assert "The review excluded 1 further RED alert candidates" in policy["basis"]


def test_a_report_refresh_rebuilds_reports_from_the_finished_review_after_an_r_and_a_decision(weekly, monkeypatch):
    from types import SimpleNamespace
    from grit.agents.weekly import PROMPTS
    from grit.pipeline.weekly import _finish_weekly_reports, resume_weekly_reporting
    pool, store, coordinator, ids, original = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    coordinator.supervise(["DRAFT_REPORT"], {"stage": "pool reconciled", "issues": store.issues()})
    profile = _profile()
    profile.brief_for = lambda pack: {"formats": ["docx"]}
    monkeypatch.setattr("grit.report.brief.build_brief", lambda *args: {**_draft(ids[0]), "v2": {"status": "STABLE", "gate": []}})
    monkeypatch.setattr("grit.report.brief.routed_signals", lambda *args: [])
    run = SimpleNamespace(run_id="R", pack="test")
    assert _finish_weekly_reports(pool, run, [profile], coordinator)["status"] == "READY"
    with pytest.raises(ValueError, match="incomplete"):
        resume_weekly_reporting(pool, run, client=None, model=None, profiles=[profile], call=original)
    pool.record_red_decision(pack="test", signal_id=ids[0], decision="CONFIRMED", impact="Depot closed.", decided_by="Jony Ling")
    calls = []
    def report_only(*args, system, payload, **kwargs):
        assert system == PROMPTS["Reporting"] or (system == PROMPTS["Critic"] and "targets" in payload)
        calls.append(system)
        return original(*args, system=system, payload=payload, **kwargs)
    before = pool.one("SELECT COUNT(*) AS n FROM weekly_selections")["n"]
    result = resume_weekly_reporting(pool, run, client=None, model=None, profiles=[profile], call=report_only, refresh=True)
    assert result["status"] == "READY" and calls == [PROMPTS["Reporting"], PROMPTS["Critic"]]
    assert pool.one("SELECT COUNT(*) AS n FROM weekly_selections")["n"] == before, "no signal review ran"
    latest = pool.one("SELECT brief_json FROM weekly_reports WHERE run_id = 'R' ORDER BY rowid DESC LIMIT 1")
    assert latest["brief"]["v2"]["status"] == "RED"


def test_reports_are_decided_after_every_final_critic_so_order_does_not_matter(weekly, monkeypatch):
    from types import SimpleNamespace
    from grit.agents.weekly import PROMPTS
    from grit.pipeline.weekly import _finish_weekly_reports
    pool, store, coordinator, ids, original = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    coordinator.supervise(["DRAFT_REPORT"], {"stage": "pool reconciled", "issues": store.issues()})
    call_id = pool.one("SELECT MAX(call_id) AS id FROM weekly_calls WHERE run_id = 'R'")["id"]
    store._issue(call_id, "STALE", "OPEN", {"issue_id": "STALE", "target": "REPORT:second", "kind": "NARRATIVE", "material": True,
                                            "problem": "Old objection to the second report.", "source_ids": []})
    def resolving(*args, system, payload, **kwargs):
        response = original(*args, system=system, payload=payload, **kwargs)
        if system == PROMPTS["Critic"] and payload.get("targets") == ["REPORT:second"]:
            response.update(resolved_issue_ids=["STALE"], resolutions=[{"issue_id": "STALE", "response": "Rewritten.", "source_ids": []}])
        return response
    coordinator.call = resolving
    profiles = [_profile("first"), _profile("second")]
    for profile in profiles:
        profile.brief_for = lambda pack: {"formats": ["docx"]}
    monkeypatch.setattr("grit.report.brief.build_brief", lambda *args: _draft(ids[0]))
    monkeypatch.setattr("grit.report.brief.routed_signals", lambda *args: [])
    result = _finish_weekly_reports(pool, SimpleNamespace(run_id="R", pack="test"), profiles, coordinator)
    assert [row["status"] for row in result["reports"]] == ["READY", "READY"] and result["status"] == "READY"


def test_a_number_in_a_cited_source_s_title_is_grounded(weekly, monkeypatch):
    from grit.agents.weekly import PROMPTS
    from grit.pipeline.weekly import WeeklyCoordinator
    pool, store, coordinator, ids, original = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    real = WeeklyCoordinator.sources.fget(coordinator)
    titled = {**real, ids[0]: {**real[ids[0]], "title": "Alpha airport reopens 4 gates"}}
    monkeypatch.setattr(WeeklyCoordinator, "sources", property(lambda self: titled))
    def reporting(*args, system, payload, **kwargs):
        if system == PROMPTS["Reporting"]:
            return {"edits": [{"path": path, "text": f"Alpha reopened 4 gates [{ids[0]}].", "source_ids": [ids[0]]}
                              for path in ("bottom_line.text", "bottom_line.escalation")]}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = reporting
    assert coordinator.report(_profile(), _draft(ids[0]), [])["status"] == "READY"


def test_a_report_refresh_also_recovers_a_report_stage_that_stopped(weekly, monkeypatch):
    from types import SimpleNamespace
    from grit.pipeline.weekly import resume_weekly_reporting
    pool, store, _, _, original = weekly
    profile = _failed_report_checkpoint(weekly, monkeypatch)
    result = resume_weekly_reporting(pool, SimpleNamespace(run_id="R", pack="test"), client=None, model=None,
                                     profiles=[profile], call=original, refresh=True)
    assert result["status"] == "READY"


def test_a_role_may_add_a_link_from_the_register_and_the_report_follows():
    from grit.agents.weekly import check_risk_ids
    from grit.pool.db import reviewed_links

    sources = {"A": {"condition_ids": ["URS-KEY-03"]}}
    row = {"signal_id": "A", "risk_ids": ["URS-KEY-03", "URS-EMERGING-01", "MADE-UP-9"]}
    check_risk_ids(row, sources, register={"URS-KEY-03", "URS-EMERGING-01", "URS-KEY-04"})
    assert row["risk_ids"] == ["URS-KEY-03", "URS-EMERGING-01"] and row["risk_ids_added"] == ["URS-EMERGING-01"]
    assert row["risk_ids_dropped"] == ["MADE-UP-9"]
    reviewed = reviewed_links({"condition_ids": ["URS-KEY-03"], "risk_cascade": [], "editorial": {"risk_ids": row["risk_ids"]}})
    assert reviewed["condition_ids"] == ["URS-KEY-03", "URS-EMERGING-01"] and reviewed["keyword_condition_ids"] == ["URS-KEY-03"]
