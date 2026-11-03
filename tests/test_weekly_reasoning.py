from datetime import date

import pytest

from grit.agents.weekly import ContractError, check_critique, check_selections, time_scope
from grit.agents.reader import critique
from grit.pool import Pool


def test_missing_issue_list_is_not_a_clean_review(monkeypatch):
    monkeypatch.setattr("grit.agents.reader._call", lambda *a: {"unexpected": True})
    accepted, rejected, log = critique(None, "stub", drafts=[{"item_id": "I1"}], priors={}, sources={})
    assert log.status == "WITHHELD"
    assert not accepted
    assert log.violations


def test_whole_batch_must_be_accounted_for():
    with pytest.raises(ContractError, match="coverage"):
        check_selections({"selections": []}, {"A": {"signal_id": "A"}}, {"A"})


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
    selections = {"selections": [{"signal_id": signal, "decision": "INCLUDE", "reason": "Ongoing impact", "source_ids": [signal]}]}
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
                           "reason": "Ongoing Alpha exposure; Beta is a different entity", "source_ids": [key]}
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


def test_material_final_objection_exhausts_shared_budget_and_blocks_review(weekly):
    from grit.agents.weekly import PROMPTS
    from grit.pipeline.weekly import frozen_report
    pool, store, coordinator, ids, original = weekly
    coordinator.reconcile_pool(search=None, reassess=lambda: None)
    def disputed(*args, system, payload, **kwargs):
        if system == PROMPTS["Critic"] and "targets" in payload:
            return {"reviewed_ids": payload["targets"], "issues": [{
                "issue_id": "INTERNAL-1", "target": payload["targets"][0], "kind": "INTERNAL_EVIDENCE",
                "problem": "No staff-presence confirmation", "material": True, "source_ids": [],
                "missing_evidence": "Approved staff-presence confirmation",
            }], "resolved_issue_ids": []}
        return original(*args, system=system, payload=payload, **kwargs)
    coordinator.call = disputed
    result = coordinator.report(_profile(), _draft(ids[0]), [])
    assert result["status"] == "INCOMPLETE"
    assert store.state["revisions_used"] == 2
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
    assert any(row["issue"]["material"] for row in store.issues().values())
    assert pool.one("SELECT * FROM execution_log WHERE actor = 'WeeklyCountersearch'")["status"] == "NOT_SEARCHED"


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
    result = run_weekly(pool, run, client=None, model="offline", search_limit=0, profiles=[profile], call=call,
                        reassess=reassess, draft_sections=lambda: reader_scan.run_agents(pool, run, client=client, model="offline", condition_critique=False))
    assert result["status"] == "READY"
    assert pool.run("R")["status"] == "COMPLETE"
    from grit.pipeline.weekly import frozen_report
    snapshot = frozen_report(pool, "R", "reader")
    assert snapshot["brief"]["meta"]["sections"] == ["OTHER_SIGNALS", "QA_LOG", "WATCHPOINTS"]
    assert ids[0] in snapshot["brief"]["membership"]


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


def test_contract_violation_gets_one_logged_repair_attempt(weekly):
    pool, store, coordinator, ids, _ = weekly
    replies = iter([{"wrong": True}, {"ok": True}, {"wrong": True}, {"wrong": True}])
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
    assert len(payloads) == 4


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
