"""The weekly adverse news and lessons learnt scan (spec 46). No network, no model: every dependency is a fake."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from grit.acquisition.brave_plan import BraveResponse
from grit.adverse import checks, render
from grit.adverse.registers import Lexicon, Registers, watchlist_graph
from grit.adverse.scan import reporting_window, run_scan
from grit.pool import Pool

WATCHLIST = {
    "basis": "test", "recordedBy": "Test",
    "entries": [
        {"id": "ADV-STE-VTHM", "cls": "STE", "kind": "ENTITY", "name": "VT Halter Marine", "aliases": ["VT Halter Marine"], "businessArea": "Defence & Public Security"},
        {"id": "ADV-PRG-PSC", "cls": "Site", "kind": "PROGRAMME", "name": "Polar Security Cutter programme", "aliases": ["Polar Security Cutter"], "businessArea": "Defence & Public Security"},
        {"id": "ADV-CUS-ACME", "cls": "Customer", "kind": "AIRLINE", "name": "Acme Airways", "aliases": ["Acme Airways"], "relationship": "Customer", "businessArea": "Commercial Aerospace"},
        {"id": "ADV-PEER-RHEINMETALL", "cls": "Org", "kind": "PEER", "name": "Rheinmetall", "aliases": ["Rheinmetall"]},
    ],
}
LEXICON = [
    {"id": "PROJECT", "label": "Project", "sectionA": "A2", "terms": ["delay*", "cancel*"]},
    {"id": "INTEGRITY", "label": "Integrity", "sectionA": "A6", "terms": ["brib*", "fined"]},
    {"id": "CYBER", "label": "Cyber", "sectionA": "A4", "terms": ["data breach*"]},
    {"id": "EMERGING", "label": "Emerging", "sectionA": None, "terms": ["tighten*"]},
]
PLAN = {"queryLimit": 3, "queries": [{"id": "Q1", "lane": "SUBSIDIARY", "query": "\"VT Halter Marine\""}, {"id": "Q2", "lane": "COMPETITOR", "query": "Rheinmetall bribery"}]}

PSC_TEXT = ("The US Coast Guard said on 3 November 2026 that the first Polar Security Cutter built by VT Halter Marine will be delivered in 2030, "
            "a delay of two more years, after design changes raised costs. VT Halter Marine is a subsidiary of ST Engineering.")
RHM_TEXT = "German prosecutors fined Rheinmetall 12 million euros on 2 November 2026 after an agent paid bribes to win an ammunition contract in Asia."
ACME_TEXT = "Acme Airways disclosed a data breach on 1 November 2026 affecting the records of 300,000 passengers held by its booking system."


def registers() -> Registers:
    return Registers(WATCHLIST, Lexicon(LEXICON), PLAN)


def graph_bundle() -> tuple[Any, dict[str, Any]]:
    return watchlist_graph(WATCHLIST), {"lobs": []}


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModel:
    """Answers by role. update decides whether a repeat of an earlier report counts as a material update."""

    def __init__(self, *, fail_screen: bool = False, update: str = "NEW", critic: dict[str, Any] | None = None) -> None:
        self.fail_screen, self.update, self.critic = fail_screen, update, critic or {}
        self.models = self
        self.calls: list[str] = []

    def generate_content(self, *, model: str, contents: str, config: dict[str, Any]) -> _Response:
        system, payload = config["system_instruction"], json.loads(contents)
        if "screen candidate news" in system:
            self.calls.append("screen")
            if self.fail_screen:
                raise RuntimeError("model offline")
            return _Response(json.dumps({"decisions": [{"id": c["id"], "section": c["provisional_section"], "reason": "test"} for c in payload["candidates"]]}))
        if "independent Critic" in system:
            self.calls.append("critic")
            return _Response(json.dumps({"verdicts": [{"id": i["id"], **self.critic.get(i["section"], {"verdict": "KEEP"})} for i in payload["items"]]}))
        self.calls.append(f"assess-{payload['section']}")
        text = payload["sources"]["S1"]["text"]
        status = self.update if payload.get("earlier_report") else "NEW"
        base = {"include": True, "update_status": status,
                "tests": {k: "Answered from the source." for k in ("adverse_consequence", "affected_party", "management_relevance", "impact_pathway", "materiality")},
                "event_date": "not stated", "unverified": [], "assessment": "The source states the facts below."}
        if payload["section"] == "A":
            draft = {**base, "headline": "Polar Security Cutter delivery slips again", "category": "A2", "event": "The first cutter slips two more years.",
                     "affected_entity": "VT Halter Marine", "relationship": "Customer", "why_adverse": "A further delay on a major defence programme.",
                     "facts": [{"statement": "Delivery moves later.", "source_id": "S1", "quote": "will be delivered in 2030, a delay of two more years"}],
                     "exposure": checks.DIRECT_EXPOSURE, "impacts": [{"area": "Project delivery", "status": "CONFIRMED", "basis": "The delay is stated."}],
                     "severity": "High", "action": checks.EXCO, "owner": "Group Legal", "action_rationale": "A delayed major programme.",
                     "escalation_triggers": ["A major defence programme", "Material project delay"], "exco_triggers": ["Material project delay or cost overrun"]}
            if status == "MATERIAL_UPDATE":
                draft["new_development_quote"] = "after design changes raised costs"
            return _Response(json.dumps(draft))
        if payload["section"] == "B":
            return _Response(json.dumps({**base, "headline": "Rheinmetall fined over agent bribery", "event": "Prosecutors fined Rheinmetall.", "affected_entity": "Rheinmetall",
                                         "why_material": "A corruption penalty on a defence peer.",
                                         "facts": [{"statement": "A fine was imposed.", "source_id": "S1", "quote": "fined Rheinmetall 12 million euros"}],
                                         "control_weakness": "Oversight of a sales agent failed.", "control_weakness_quote": "an agent paid bribes to win an ammunition contract",
                                         "risk_categories": ["Fraud and Integrity", "Third-Party Risk"],
                                         "lessons": ["Review due diligence and payment controls over sales agents in Asia."],
                                         "existing_exposure": checks.EXPOSURE_B[0], "coverage": "Yes", "internal_review": ["Targeted thematic review"], "severity": "Medium"}))
        return _Response(json.dumps({**base, "include": False, "exclusion_reason": "Not material."}))


def brave_getter(url: str, params: dict[str, str]) -> BraveResponse:
    result = {"title": "VT Halter Marine Polar Security Cutter delivery delayed to 2030", "url": "https://news.example.com/psc", "description": PSC_TEXT,
              "age": "2026-11-03T08:00:00Z", "page_age": "2026-11-03T08:00:00Z", "meta_url": {"hostname": "news.example.com"}}
    payload = {"results": [result] if "Halter" in params["q"] else []}
    return BraveResponse(200, payload, {"x-ratelimit-limit": "50"})


def verifier(texts: dict[str, str]):
    filler = " Further background on the programme and the market follows in the article." * 8

    def verify(url: str) -> dict[str, Any]:
        if url in texts:
            return {"status": "SUCCEEDED", "url": url, "text": texts[url] + filler}
        return {"status": "NOT_CHECKED", "reason": "HTTP 403"}

    return verify


def test_a_bot_check_page_does_not_count_as_verified() -> None:
    from grit.adverse.scan import readable

    assert not readable("Client Challenge. JavaScript is disabled in your browser. " * 3)
    assert not readable("Too short.")
    assert readable(PSC_TEXT * 3)


def _member(url: str, text: str, score: float) -> Any:
    from grit.adverse.screen import Candidate

    return Candidate(f"sig-{score}", url, "Acme Airways data breach", text, "Outlet", "2026-11-01T00:00:00Z", "2026-11-01T01:00:00Z",
                     "COUNTERPARTY", [], [], [], "T2", "A", score)


def test_search_text_stands_in_for_a_page_that_does_not_open() -> None:
    from grit.adverse.scan import MIN_EXTRACT_CHARS, verified_sources
    from grit.adverse.screen import Item

    long_text = ACME_TEXT + " The airline said it had notified regulators and affected passengers." * 3
    assert len(long_text) >= MIN_EXTRACT_CHARS > len(ACME_TEXT)
    item = Item("A", [_member("https://blocked.example.org/a", long_text, 9), _member("https://open.example.org/b", ACME_TEXT, 5),
                      _member("https://blocked.example.org/short", ACME_TEXT, 7)])
    table, refs, failures = verified_sources(item, verifier({"https://open.example.org/b": ACME_TEXT}), {}, {"T2": 0})
    assert [(r["url"], r["basis"]) for r in refs] == [("https://open.example.org/b", "PAGE"), ("https://blocked.example.org/a", "SEARCH_EXTRACT")]
    assert table["S2"]["text"] == long_text and table["S2"]["basis"] == "SEARCH_EXTRACT"
    assert len(failures) == 2, "both pages that did not open are still reported, including the one with too little search text"


def test_search_text_sources_are_labelled_in_the_email_and_listed_for_the_reviewer() -> None:
    refs = [{"source_id": "S1", "outlet": "Blocked", "url": "https://blocked.example.org/a", "published": "2026-11-01", "basis": "SEARCH_EXTRACT"}]
    doc = render._Doc(False)
    render._sources(doc, {"sources": refs})
    assert render.EXTRACT_NOTE in "\n".join(doc.lines)
    report = {"run_date": "2026-11-09", "status": "READY", "window": {"start": "2026-11-03", "end": "2026-11-09"}, "counts": {},
              "items": {"A": [{"item_id": "A-1", "headline": "Acme breach", "sources": refs}]}}
    assert "- [A-1] Acme breach: Blocked https://blocked.example.org/a" in render.worksheet(report)


@pytest.fixture(autouse=True)
def no_internal_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A test reads internal context only from the file it writes, never from secrets/."""
    import grit.settings

    monkeypatch.setattr(grit.settings, "SECRETS_DIR", tmp_path / "no-secrets")


@pytest.fixture
def pool(tmp_path: Path) -> Pool:
    pool = Pool(tmp_path / "grit.sqlite")
    pool.add_signal(url="https://wire.example.org/rheinmetall", title="Rheinmetall fined over bribery by agent", text=RHM_TEXT, publisher="Wire",
                    published_at="2026-11-02T10:00:00Z", lane="BRAVE", content_hash="h1", seen_at="2026-11-02T11:00:00Z")
    pool.add_signal(url="https://blocked.example.org/acme", title="Acme Airways data breach hits passengers", text=ACME_TEXT, publisher="Blocked",
                    published_at="2026-11-01T10:00:00Z", lane="BRAVE", content_hash="h2", seen_at="2026-11-01T11:00:00Z")
    pool.add_signal(url="https://wire.example.org/quiet", title="VT Halter Marine opens a training centre", text="A ribbon was cut.", publisher="Wire",
                    published_at="2026-11-02T10:00:00Z", lane="BRAVE", content_hash="h3", seen_at="2026-11-02T11:00:00Z")
    yield pool
    pool.close()


def scan(pool: Pool, tmp_path: Path, day: date, model: FakeModel, **options: Any) -> dict[str, Any]:
    texts = {"https://news.example.com/psc": PSC_TEXT, "https://wire.example.org/rheinmetall": RHM_TEXT}
    return run_scan(day, pool=pool, client=model, registers=registers(), graph_bundle=graph_bundle(), getter=brave_getter, verify=verifier(texts),
                    folder=tmp_path / day.isoformat(), usage_path=tmp_path / "usage.json", ratings={}, sleep=lambda _s: None, workers=1, log=lambda _m: None,
                    **options)


# Pure rules ----------------------------------------------------------------------------------------------------------------


def test_the_lexicon_matches_stems_and_records_where() -> None:
    hits = Lexicon(LEXICON).hits("Programme delayed", "x " * 300 + "a bribe was paid")
    assert {(h.category, h.where) for h in hits} == {("PROJECT", "TITLE"), ("INTEGRITY", "BODY")}
    assert not Lexicon(LEXICON).hits("Cancellation-free", "") or Lexicon(LEXICON).hits("Cancellation-free", "")[0].term == "cancel*"
    assert not Lexicon(LEXICON).hits("Unfined", "")


def test_the_first_period_is_seven_days_and_the_next_starts_where_the_last_ready_scan_ended(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "w.sqlite")
    first = reporting_window(pool, date(2026, 11, 9))
    assert (first["start"], first["previous"]) == (date(2026, 11, 3), None)
    pool.insert("adverse_scans", {"scan_id": "x", "run_date": "2026-11-09", "window_start": "2026-11-03", "window_end": "2026-11-09", "status": "INCOMPLETE", "created_at": "t"})
    assert reporting_window(pool, date(2026, 11, 16))["start"] == date(2026, 11, 10), "an incomplete scan does not move the period"
    pool.insert("adverse_scans", {"scan_id": "y", "run_date": "2026-11-10", "window_start": "2026-11-04", "window_end": "2026-11-10", "status": "READY", "created_at": "t"})
    assert reporting_window(pool, date(2026, 11, 16))["start"] == date(2026, 11, 10)
    pool.close()


def test_facts_must_quote_a_supplied_source_verbatim() -> None:
    sources = {"S1": {"title": "t", "text": "The contract was terminated on Friday by the ministry."}}
    assert not checks.facts_problems([{"statement": "s", "source_id": "S1", "quote": "contract was terminated on Friday"}], sources)
    assert checks.facts_problems([{"statement": "s", "source_id": "S1", "quote": "contract was cancelled on Friday"}], sources)[0].startswith("QUOTE_NOT_VERBATIM")
    assert checks.facts_problems([{"statement": "s", "source_id": "S2", "quote": "contract was terminated on Friday"}], sources)[0].startswith("UNKNOWN_SOURCE")
    assert checks.facts_problems([], sources)[0].startswith("NO_EVIDENCE")


def test_exposure_needs_ste_named_and_exco_needs_its_conditions() -> None:
    draft = {"relationship": "Supplier", "exposure": checks.DIRECT_EXPOSURE, "severity": "High", "action": checks.EXCO, "owner": "Group Legal",
             "exco_triggers": ["Material adverse development involving a major customer"], "escalation_triggers": ["A major customer"]}
    item, notes = checks.decide_a(draft, {"classes": ["Customer"], "ste_named": False, "publication_stated": True})
    assert item["relationship"] == "Customer", "a registered customer is a customer"
    assert item["exposure"] == checks.NOT_CONFIRMED
    assert item["action"] == checks.FOLLOW_GF, "no direct or indirect exposure, so no EXCO escalation"
    assert item["escalate"] is True, "High with an escalation trigger still heads the summary"
    assert len(notes) == 3


def test_critical_goes_to_exco_and_ratings_need_a_stated_publication_date() -> None:
    draft = {"relationship": "Other relevant stakeholder", "exposure": checks.DIRECT_EXPOSURE, "severity": "Critical", "action": checks.MONITOR, "escalation_triggers": []}
    item, _ = checks.decide_a(draft, {"classes": ["STE"], "ste_named": True, "publication_stated": True})
    assert (item["severity"], item["action"], item["relationship"]) == ("Critical", checks.EXCO, "ST Engineering entity")
    item, _ = checks.decide_a(draft, {"classes": ["STE"], "ste_named": True, "publication_stated": False})
    assert item["severity"] == "Medium"


def test_section_b_never_implies_an_ste_weakness_and_coverage_needs_internal_material() -> None:
    sources = {"S1": {"title": "t", "text": "The company's agent paid bribes to officials in two countries."}}
    draft = {"include": True, "headline": "h", "event": "e", "affected_entity": "x", "severity": "Medium", "why_material": "w",
             "tests": {k: "a" for k in ("adverse_consequence", "affected_party", "management_relevance", "impact_pathway", "materiality")},
             "facts": [{"statement": "s", "source_id": "S1", "quote": "agent paid bribes to officials"}], "control_weakness": checks.NO_CONTROL_WEAKNESS,
             "risk_categories": ["Fraud and Integrity"], "existing_exposure": checks.EXPOSURE_B[3], "internal_review": [],
             "lessons": ["ST Engineering has weak controls over agents."]}
    assert any(p.startswith("STE_DEFICIENCY_IMPLIED") for p in checks.check_draft(draft, "B", sources))
    draft["lessons"] = ["Consider reviewing due diligence on sales agents."]
    assert checks.check_draft(draft, "B", sources) == []
    item, notes = checks.decide_b({**draft, "coverage": "Yes"}, {"publication_stated": True})
    assert item["coverage"] == "Unable to determine" and item["existing_exposure_note"] == checks.NO_INTERNAL_EXPOSURE and notes


def test_a_horizon_and_a_material_update_need_quotes() -> None:
    sources = {"S1": {"title": "t", "text": "The ministry will publish the new export rules by March 2027, officials said."}}
    draft = {"include": True, "headline": "h", "event": "e", "affected_entity": "x", "severity": "Low", "why_it_matters": "w", "how_it_could_evolve": "h",
             "connection": "Affects Commercial Aerospace exports.", "trigger_points": ["Rules published"], "response": checks.MONITOR,
             "tests": {k: "a" for k in ("adverse_consequence", "affected_party", "management_relevance", "impact_pathway", "materiality")},
             "facts": [{"statement": "s", "source_id": "S1", "quote": "publish the new export rules"}], "time_horizon": "Near term"}
    assert any(p.startswith("HORIZON_NOT_QUOTED") for p in checks.check_draft(draft, "C", sources, anchors=["Commercial Aerospace"]))
    draft["horizon_quote"] = "new export rules by March 2027"
    assert checks.check_draft(draft, "C", sources, anchors=["Commercial Aerospace"]) == []
    assert any(p.startswith("NO_CONNECTION") for p in checks.check_draft({**draft, "connection": "Affects exporters."}, "C", sources, anchors=["Commercial Aerospace"]))
    update = {**draft, "update_status": "MATERIAL_UPDATE", "new_development_quote": "publish the new export rules"}
    assert any(p.startswith("UPDATE_NOT_NEW") for p in checks.check_draft(update, "C", sources, previous={"evidence": "publish the new export rules"},
                                                                       anchors=["Commercial Aerospace"]))


def test_the_critic_can_withdraw_or_downgrade_only_under_a_named_rule() -> None:
    item = {"severity": "High", "exposure": checks.DIRECT_EXPOSURE, "action": checks.EXCO, "escalate": True}
    assert checks.apply_critic(item, {"verdict": "WITHDRAW", "rule": "NOT_ADVERSE", "reason": "positive"})[0] is None
    assert checks.apply_critic(item, {"verdict": "WITHDRAW", "rule": "I_DISAGREE"})[0] is item
    changed, _ = checks.apply_critic(item, {"verdict": "DOWNGRADE", "rule": "ASSUMED_EXPOSURE", "reason": "r"})
    assert (changed["severity"], changed["exposure"], changed["action"], changed["escalate"]) == ("Medium", checks.NOT_CONFIRMED, checks.FOLLOW_GF, False)


def test_an_empty_scan_prints_every_prescribed_nil_statement() -> None:
    report = {"run_date": "2026-11-09", "window": {"start": "2026-11-03", "end": "2026-11-09", "previous": None}, "items": {"A": [], "B": [], "C": []},
              "counts": {"pages": 0, "verified_pages": 0}, "attention": {"immediate": [], "monitoring": [], "lessons": []},
              "overall": checks.overall({"A": [], "B": [], "C": []}), "validation": ["ok"], "repeats": []}
    text = render.compose(report)
    assert text.splitlines()[:2] == ["To: wilsonweimeng.yeo@stengg.com", "Subject: Adverse News & Lessons Learnt Scan | 9 November 2026"]
    for sentence in (checks.NO_ESCALATION, checks.NO_IMMEDIATE, checks.NO_MONITORING, checks.NO_LESSONS, "No Material Concern"):
        assert sentence in text
    assert text.count(checks.NO_DEVELOPMENTS) == 3
    assert render.email_problems(text, date(2026, 11, 9)) == []
    assert render.email_problems(text.replace("Dear Wilson,", "Hi"), date(2026, 11, 9)) == ["The greeting must be 'Dear Wilson,'."]
    assert b"X-Unsent: 1" in render.eml(text)


# The whole scan ---------------------------------------------------------------------------------------------------------


def test_a_scan_searches_screens_verifies_assesses_and_writes_the_email(pool: Pool, tmp_path: Path) -> None:
    model = FakeModel()
    report = scan(pool, tmp_path, date(2026, 11, 5), model)
    assert report["status"] == "READY", report["blockers"]
    assert report["search"]["queries_sent"] == 2 and report["search"]["corroboration"] == 1, "the third query of the limit corroborates the High item"
    assert report["search"]["currency"] == 2 and report["items"]["A"][0]["currency"]["entity"] == "Polar Security Cutter programme", "each item's main entity is re-searched"
    a, b = report["items"]["A"], report["items"]["B"]
    assert [i["headline"] for i in a] == ["Polar Security Cutter delivery slips again"]
    assert a[0]["relationship"] == "ST Engineering entity" and a[0]["exposure"] == checks.DIRECT_EXPOSURE
    assert a[0]["action"] == checks.EXCO and a[0]["escalate"]
    assert [i["affected_entity"] for i in b] == ["Rheinmetall"] and b[0]["coverage"] == "Unable to determine"
    assert any("No source page could be opened" in w["reason"] for w in report["withheld"]), "the blocked Acme page is withheld, not reported"
    assert report["overall"][0] == "Immediate Escalation Recommended"
    email = Path(report["paths"]["email"]).read_text(encoding="utf-8")
    assert email.startswith("To: wilsonweimeng.yeo@stengg.com\nSubject: Adverse News & Lessons Learnt Scan | 5 November 2026\n\nDear Wilson,")
    assert "https://news.example.com/psc" in email and "[A-1] Polar Security Cutter delivery slips again" in email
    assert email.rstrip().endswith("Kind Regards,\nJony")
    assert "blocked.example.org" not in email
    assert pool.one("SELECT status FROM adverse_scans WHERE scan_id = '2026-11-05-adverse'")["status"] == "READY"
    assert len(pool.rows("SELECT * FROM adverse_items WHERE status = 'INCLUDED'")) == 2


def test_a_repeat_with_no_material_update_is_left_out_next_week(pool: Pool, tmp_path: Path) -> None:
    scan(pool, tmp_path, date(2026, 11, 5), FakeModel())
    pool.add_signal(url="https://wire.example.org/psc-again", title="VT Halter Marine Polar Security Cutter delay confirmed", text=PSC_TEXT, publisher="Wire",
                    published_at="2026-11-08T10:00:00Z", lane="BRAVE", content_hash="h4", seen_at="2026-11-08T11:00:00Z")
    texts = {"https://wire.example.org/psc-again": PSC_TEXT, "https://news.example.com/psc": PSC_TEXT, "https://wire.example.org/rheinmetall": RHM_TEXT}
    report = run_scan(date(2026, 11, 12), pool=pool, client=FakeModel(update="NO_MATERIAL_UPDATE"), registers=registers(), graph_bundle=graph_bundle(),
                      verify=verifier(texts), folder=tmp_path / "next", ratings={}, workers=1, log=lambda _m: None, no_search=True)
    assert report["window"]["start"] == "2026-11-05"
    assert report["items"]["A"] == [] and [r["headline"] for r in report["repeats"]] == ["Polar Security Cutter delivery slips again"]
    assert checks.NO_UPDATE in Path(report["paths"]["email"]).read_text(encoding="utf-8")


def test_a_screener_outage_makes_the_scan_incomplete_not_empty(pool: Pool, tmp_path: Path) -> None:
    report = scan(pool, tmp_path, date(2026, 11, 5), FakeModel(fail_screen=True), no_search=True)
    assert report["status"] == "INCOMPLETE" and any("Screener failed" in b for b in report["blockers"])
    assert Path(report["paths"]["email"]).name == "email-DRAFT-INCOMPLETE.txt"
    assert not (tmp_path / "2026-11-05" / "email.txt").exists()
    assert pool.one("SELECT status FROM adverse_scans")["status"] == "INCOMPLETE"


def test_a_dry_run_spends_nothing(pool: Pool, tmp_path: Path) -> None:
    model = FakeModel()
    report = scan(pool, tmp_path, date(2026, 11, 5), model, dry_run=True)
    assert report["status"] == "DRY_RUN" and model.calls == []
    assert pool.rows("SELECT * FROM adverse_scans") == [] and not (tmp_path / "usage.json").exists()


def test_grit_scheduled_runs_the_adverse_scan_on_thursdays_only() -> None:
    from grit.schedule import adverse_due

    assert adverse_due(date(2026, 11, 5)) and adverse_due(date(2026, 11, 12))
    assert not adverse_due(date(2026, 11, 9))


def test_same_event_items_merge_with_their_sources_and_quotes() -> None:
    cathay = {"headline": "Cathay Pacific customer records exposed in Amadeus data breach", "severity": "Low", "affected_entity": "Cathay Pacific",
              "sources": [{"source_id": "S1", "url": "https://a.example/1", "outlet": "A"}], "facts": [{"statement": "s", "source_id": "S1", "quote": "q one is long enough here"}],
              "outlets": ["a.example"], "signal_ids": ["SIG-1"]}
    sia = {"headline": "Singapore Airlines customer records exposed in Amadeus breach", "severity": "Medium", "affected_entity": "Singapore Airlines",
           "sources": [{"source_id": "S1", "url": "https://b.example/2", "outlet": "B"}], "facts": [{"statement": "t", "source_id": "S1", "quote": "q two is long enough here"}],
           "outlets": ["b.example"], "signal_ids": ["SIG-2"]}
    other = {"headline": "Rheinmetall fined over agent bribery", "severity": "Low", "sources": [], "facts": [], "outlets": []}
    kept, merged = checks.merge_duplicates([cathay, sia, other])
    assert [k["headline"] for k in kept] == [sia["headline"], other["headline"]], "the higher-rated item keeps its text"
    assert merged == [(cathay, sia)]
    assert [s["source_id"] for s in sia["sources"]] == ["S1", "S2"] and sia["facts"][1]["source_id"] == "S2"
    assert sia["affected_entity"] == "Singapore Airlines; Cathay Pacific" and sia["corroborated"] is True


def test_quotes_with_line_breaks_render_on_one_line() -> None:
    doc = render._Doc(False)
    doc.bullet('A fact ("launched by\nScott+Scott\nCA ULC", outlet)', 1)
    assert doc.lines == ['  - A fact ("launched by Scott+Scott CA ULC", outlet)']


def test_the_critics_same_event_groups_merge_within_a_section_only() -> None:
    def entry(item_id: str, section: str, severity: str, url: str) -> dict[str, Any]:
        return {"item_id": item_id, "section": section, "severity": severity, "headline": item_id, "affected_entity": item_id,
                "sources": [{"source_id": "S1", "url": url, "outlet": url}], "facts": [{"statement": "s", "source_id": "S1", "quote": f"quote from {url} long"}],
                "outlets": [url]}
    a1, a2, a3 = entry("A?1", "A", "Low", "u1"), entry("A?2", "A", "Medium", "u2"), entry("A?3", "A", "Low", "u3")
    kept, merged = checks.merge_groups([a1, a2, a3], [["A?1", "A?2", "A?3"], ["A?1", "B?1"]])
    assert [k["item_id"] for k in kept] == ["A?2"] and len(merged) == 2
    assert [s["source_id"] for s in a2["sources"]] == ["S1", "S2", "S3"] and a2["corroborated"]
    b1 = entry("B?1", "B", "Low", "u4")
    assert checks.merge_groups([a1, b1], [["A?1", "B?1"]])[0] == [a1, b1], "Section B stays separate from Section A"


def test_merged_affected_entities_are_listed_once() -> None:
    assert checks.entity_names("Cathay Pacific Airways Limited, Japan Airlines Co., Ltd., ANA Holdings Inc., and Korean Air Lines Co., Ltd.") == [
        "Cathay Pacific Airways Limited", "Japan Airlines Co., Ltd", "ANA Holdings Inc", "Korean Air Lines Co., Ltd"]
    home = {"affected_entity": "Singapore Airlines Limited; Japan Airlines Co., Ltd.", "sources": [], "facts": []}
    checks.merge_into(home, {"affected_entity": "Cathay Pacific Airways Limited, Japan Airlines Co., Ltd., and ANA Holdings Inc.", "sources": [], "facts": []})
    assert home["affected_entity"] == "Singapore Airlines Limited; Japan Airlines Co., Ltd; Cathay Pacific Airways Limited; ANA Holdings Inc"


def _item(item_id: str, section: str, severity: str, headline: str, entity_ids: list[str], **fields: Any) -> dict[str, Any]:
    return {"item_id": item_id, "section": section, "severity": severity, "headline": headline, "affected_entity": headline.split()[0], "entity_ids": entity_ids,
            "sources": [{"source_id": "S1", "url": f"https://{item_id}.example", "outlet": item_id, "basis": "PAGE"}],
            "facts": [{"statement": "s", "source_id": "S1", "quote": f"a quote from {item_id} that is long"}], "outlets": [item_id], **fields}


def test_items_on_one_topic_merge_across_sections_and_the_group_is_no_topic() -> None:
    names = {"SUP-NV": "NVIDIA Corporation", "ORG-AMA": "Amadeus", "STE-GROUP": "ST Engineering"}
    items = {
        "A": [_item("A?1", "A", "Medium", "NVIDIA faces FBI probe", ["SUP-NV"]), _item("A?2", "A", "Low", "Thai Airways booking data exposed", ["ORG-AMA"]),
              _item("A?3", "A", "Medium", "ST Engineering wins a contract dispute", ["STE-GROUP"])],
        "B": [_item("B?1", "B", "Medium", "Amadeus breach hits seven airlines", ["ORG-AMA"], lessons=["Review supplier access to booking data."])],
        "C": [_item("C?1", "C", "Medium", "Nvidia chips diverted via Singapore", []), _item("C?2", "C", "Low", "Lawmakers tighten NVIDIA export rules", ["SUP-NV"]),
              _item("C?3", "C", "Low", "ST Engineering named in a tender row", ["STE-GROUP"])],
    }
    kept, merged = checks.merge_topics(items, names, ignore={"STE-GROUP"})
    assert [e["item_id"] for e in kept["A"]] == ["A?1", "A?2", "A?3"] and kept["B"] == [] and [e["item_id"] for e in kept["C"]] == ["C?3"]
    nvidia, amadeus = kept["A"][0], kept["A"][1]
    assert [r["headline"] for r in nvidia["related"]] == ["Nvidia chips diverted via Singapore", "Lawmakers tighten NVIDIA export rules"], "a headline naming the entity joins it"
    assert len(nvidia["sources"]) == 3 and amadeus["lessons"] == ["Review supplier access to booking data."], "a Section B lesson travels with its report"
    assert len(merged) == 3


def test_immediate_attention_needs_confirmed_exposure_and_holds_at_most_four() -> None:
    def a(n: int, severity: str, exposure: str, action: str = checks.FOLLOW_BA) -> dict[str, Any]:
        return {"item_id": f"A-{n}", "headline": f"Item {n}", "severity": severity, "exposure": exposure, "action": action}

    items = {"A": [a(1, "Low", checks.DIRECT_EXPOSURE), *(a(n, "Medium", checks.INDIRECT_EXPOSURE) for n in (2, 3, 4, 5)), a(6, "High", checks.NOT_CONFIRMED)],
             "B": [], "C": [{"item_id": "C-1", "headline": "A generic threat", "severity": "Medium", "response": checks.FOLLOW_GF}]}
    found = checks.attention(items)
    assert [line.split()[0] for line in found["immediate"]] == ["A-2", "A-3", "A-4", "A-5"], "four, highest severity first"
    assert any(line.startswith("A-1 ") and "over the Immediate Attention limit" in line for line in found["monitoring"])
    assert any(line.startswith("A-6 ") and "no confirmed ST Engineering exposure" in line for line in found["monitoring"])
    assert any(line.startswith("C-1 ") for line in found["monitoring"])
    assert checks.overall(items)[0] == "Management Action Recommended"
    assert checks.overall({"A": [a(6, "High", checks.NOT_CONFIRMED)], "B": [], "C": []})[0] == "Elevated Monitoring Required"


def test_the_final_validation_counts_what_rests_on_search_engine_text() -> None:
    from grit.adverse.scan import validation_lines

    page = _item("A-1", "A", "Medium", "Opened page item", [])
    extract = _item("A-2", "A", "Low", "Extract item", [])
    extract["sources"][0]["basis"] = "SEARCH_EXTRACT"
    lines = validation_lines({"A": [page, extract], "B": [], "C": []}, high=[], earlier=0, repeats=0, withheld=0, search_result={},
                             merges=0, currency={"ran": False}, internal=None)
    assert lines[0] == "Sources: 2 items cite 2 sources; 1 pages were opened and checked, and 1 rest on search-engine text only because the page did not open."
    assert lines[1].startswith("Facts: 2 facts are quoted verbatim from their sources; 1 of 2 come from search-engine text only.") and lines[1].endswith("A-2.")
    assert any("Currency check" in line and "not run" in line for line in lines)
    assert any("no staff-by-country data was supplied" in line for line in lines)


def test_the_currency_check_lists_a_newer_report_on_the_main_entity(pool: Pool, tmp_path: Path) -> None:
    from grit.adverse.scan import currency_check

    def getter(url: str, params: dict[str, str]) -> BraveResponse:
        result = {"title": "Rheinmetall chief executive resigns after bribery fine", "url": "https://wire.example.org/ceo", "description": "He resigned.",
                  "age": "2026-11-11T08:00:00Z", "page_age": "2026-11-11T08:00:00Z", "meta_url": {"hostname": "wire.example.org"}}
        return BraveResponse(200, {"results": [result] if "Rheinmetall" in params["q"] else []}, {"x-ratelimit-limit": "50"})

    pool.start_run("2026-11-12-adverse", run_date="2026-11-12", pack="adverse", kind="ADVERSE_SCAN", config={})
    entry = _item("B?1", "B", "Medium", "Rheinmetall fined over agent bribery", ["ADV-PEER-RHEINMETALL"])
    result = currency_check(pool, "2026-11-12-adverse", {"A": [], "B": [entry], "C": []}, {"ADV-PEER-RHEINMETALL": "Rheinmetall"}, set(), date(2026, 11, 12),
                            tmp_path, registers=registers(), band_of=lambda _u, _p: "T2", limit=8, getter=getter, usage_path=tmp_path / "usage.json",
                            sleep=lambda _s: None, log=lambda _m: None)
    assert result["queries_sent"] == 1 and result["newer"] == 1
    assert entry["currency"]["latest"][0]["title"] == "Rheinmetall chief executive resigns after bribery fine"
    assert [e for e, _rows in result["triggers"]] == [entry], "a T2 headline saying the chief executive resigns re-runs the item"
    doc = render._Doc(False)
    render._extras(doc, entry, "B")
    assert any("chief executive resigns" in line for line in doc.lines)


def test_the_staff_log_is_read_by_country_and_its_age_is_reported(tmp_path: Path) -> None:
    from grit.adverse.people import countries_named, load_people, presence

    assert load_people(tmp_path / "none.json") is None
    path = tmp_path / "internal-context.json"
    path.write_text(json.dumps({
        "middle_east_local_staff": {"as_at": "2026-11-04", "entries": [{"entity": "URS", "country": "Qatar", "headcount": 52},
                                                                       {"entity": "iDirect", "country": "UAE (Dubai)", "headcount": 9}], "caveat": "bulk read"},
        "middle_east_travellers_and_assignees": {"as_at": "2026-10-01", "by_country": {"Qatar": 21, "UAE": 4}},
    }), encoding="utf-8")
    people = load_people(path, today=date(2026, 11, 17))
    assert set(people["countries"]) == {"Qatar", "United Arab Emirates"} and people["age_days"] == 47 and people["stale"]
    assert presence("Qatar", people) == "Qatar: 52 local staff (URS), as at 4 November 2026; 21 travellers and assignees, as at 1 October 2026."
    assert countries_named("Missiles intercepted over Doha and Dubai", people) == ["Qatar", "United Arab Emirates"]
    assert countries_named("A quaestor in a guaestor", people) == [], "an abbreviation matches only as a word, in capitals"


def test_a_threat_to_people_where_ste_staff_are_is_section_a_with_indirect_exposure(tmp_path: Path) -> None:
    from grit.adverse.people import PEOPLE, load_people
    from grit.adverse.screen import screen
    from grit.ontology.link import LinkIndex

    path = tmp_path / "internal-context.json"
    path.write_text(json.dumps({"middle_east_local_staff": {"as_at": "2026-11-04", "entries": [{"entity": "URS", "country": "Qatar", "headcount": 52}]}}),
                    encoding="utf-8")
    people = load_people(path, today=date(2026, 11, 17))
    lexicon = Lexicon([*LEXICON, {"id": "PEOPLE_RISK", "label": "Threat to people", "sectionA": "A5", "terms": ["missile*", "evacuat*"]}])
    rows = [{"signal_id": "SIG-1", "title": "Missiles intercepted over Doha as Gulf tension rises", "text": "Air defences engaged.", "url": "https://n.example/1",
             "publisher": "N", "published_at": "2026-11-15T08:00:00Z", "first_seen_at": "2026-11-15"},
            {"signal_id": "SIG-2", "title": "Missiles intercepted over Kyiv", "text": "Air defences engaged.", "url": "https://n.example/2",
             "publisher": "N", "published_at": "2026-11-15T08:00:00Z", "first_seen_at": "2026-11-15"}]
    found, _counts = screen(rows, index=LinkIndex(watchlist_graph(WATCHLIST), {"lobs": []}), lob_profiles={"lobs": []}, lexicon=lexicon,
                            lanes_by_signal={}, band_of=lambda _u, _p: "T2", people=people)
    assert [(c.signal_id, c.rung, c.node_ids) for c in found["A"]] == [("SIG-1", PEOPLE, ["PEOPLE-qatar"])], "no staff in Ukraine, so Kyiv is not Section A"
    from dataclasses import replace

    from grit.adverse.screen import group

    other = replace(found["A"][0], signal_id="SIG-3", title="Doha residents told to shelter as drones are downed")
    assert len(group([found["A"][0], other], "A")) == 1, "threats to people in one staffed country are one item"
    draft = {"relationship": "Other relevant stakeholder", "exposure": checks.EXPOSURE_UNKNOWN, "severity": "High", "action": checks.MONITOR, "impacts": []}
    decided, notes = checks.decide_a(draft, {"classes": [], "people": ["Qatar: 52 local staff (URS)."], "people_event": True, "publication_stated": True})
    assert (decided["exposure"], decided["exposure_basis"], decided["action"], decided["owner"]) == (
        checks.INDIRECT_EXPOSURE, checks.STAFF_LOG, checks.FOLLOW_GF, checks.PEOPLE_OWNER)
    assert decided["relationship"] == "ST Engineering entity" and any(i["area"] == "Safety" for i in decided["impacts"]) and notes
    unrelated, _ = checks.decide_a({**draft, "category": "A1"}, {"classes": [], "people": ["Qatar: 52 local staff (URS)."], "people_event": False})
    assert unrelated["exposure"] == checks.EXPOSURE_UNKNOWN, "staff in the country do not make a financial story an exposure"


def test_a_page_naming_ste_is_pinned_unless_it_is_a_namesake() -> None:
    from grit.adverse.scan import own_aliases
    from grit.adverse.screen import screen
    from grit.ontology.link import LinkIndex

    graph = watchlist_graph(WATCHLIST)
    own = {**own_aliases(graph), "TransCore, LP": ("TransCore",)}
    rows = [{"signal_id": "SIG-1", "title": "VT Halter Marine warns as shipyard rules tighten", "text": "", "url": "https://n.example/1",
             "publisher": "N", "published_at": "2026-11-15T08:00:00Z", "first_seen_at": "2026-11-15"},
            {"signal_id": "SIG-2", "title": "Vilas Transcore warns as margins tighten", "text": "", "url": "https://n.example/2",
             "publisher": "N", "published_at": "2026-11-15T08:00:00Z", "first_seen_at": "2026-11-15"}]
    found, counts = screen(rows, index=LinkIndex(graph, {"lobs": []}), lob_profiles={"lobs": []}, lexicon=Lexicon(LEXICON), lanes_by_signal={},
                           band_of=lambda _u, _p: "T2", own=own)
    assert [(c.signal_id, c.pinned) for c in found["A"]] == [("SIG-1", ["VT Halter Marine"])], "an emerging term is enough for an own entity"
    assert counts["pinned"] == 1 and "SIG-2" not in {c.signal_id for cs in found.values() for c in cs}, "Vilas Transcore is a namesake"
    pinned = _item("A?1", "A", "Low", "VT Halter Marine NVIDIA chip audit", ["SUP-NV"], pinned=["VT Halter Marine"])
    other = _item("C?1", "C", "Low", "NVIDIA export rules tighten", ["SUP-NV"])
    kept, merged = checks.merge_topics({"A": [pinned], "B": [], "C": [other]}, {"SUP-NV": "NVIDIA Corporation"})
    assert merged == [] and kept["A"] == [pinned] and kept["C"] == [other], "a pinned item is never merged"
    changed, reason = checks.apply_critic({**pinned, "exposure": checks.DIRECT_EXPOSURE, "action": checks.MONITOR}, {"verdict": "WITHDRAW", "rule": "NOT_MATERIAL", "reason": "minor"})
    assert changed is not None and "always reported" in reason


def test_a_merged_item_keeps_its_best_sources_and_their_facts() -> None:
    bands = ["T3", "UNRATED", "T1", "T2", "T3", "T2", "UNRATED"]
    entry = {"sources": [{"source_id": f"S{n}", "url": f"u{n}", "outlet": f"o{n}", "band": band, "basis": "SEARCH_EXTRACT" if n == 4 else "PAGE"}
                         for n, band in enumerate(bands, start=1)],
             "facts": [{"statement": f"f{n}", "source_id": f"S{n % 7 + 1}", "quote": f"q{n}"} for n in range(12)],
             "lessons": ["l1", "l2", "l3", "l4"], "trigger_points": [f"t{n}" for n in range(7)]}
    trimmed = checks.cap_item(entry)
    assert [ref["url"] for ref in entry["sources"]] == ["u3", "u1", "u4", "u5", "u6"], "T1, T2 pages, T2 search text and T3 kept; the T1 page leads"
    assert [ref["source_id"] for ref in entry["sources"]] == ["S1", "S2", "S3", "S4", "S5"]
    assert len(entry["facts"]) == 8 and {f["source_id"] for f in entry["facts"]} <= {"S1", "S2", "S3", "S4", "S5"}
    assert entry["facts"][0]["statement"] == "f0" and any(f["statement"] == "f2" and f["source_id"] == "S1" for f in entry["facts"]), "facts follow their source"
    assert entry["lessons"] == ["l1", "l2", "l3"] and len(entry["trigger_points"]) == 5 and trimmed


def test_a_newer_top_tier_report_re_runs_the_item(pool: Pool) -> None:
    from grit.adverse.scan import reassess_items

    old_url = "https://wire.example.org/rheinmetall"
    new_id = pool.add_signal(url="https://wire.example.org/ceo", title="Rheinmetall chief executive resigns", text="The chief executive resigned on 11 November 2026. " * 10,
                             publisher="Wire", published_at="2026-11-11T08:00:00Z", lane="BRAVE", content_hash="h9", seen_at="2026-11-11T09:00:00Z")
    entry = _item("B?1", "B", "Medium", "Rheinmetall fined over agent bribery", ["ADV-PEER-RHEINMETALL"], _item=None, _facts={"publication_stated": True})
    entry["sources"] = [{"source_id": "S1", "url": old_url, "outlet": "Wire", "basis": "PAGE", "band": "T2"}]
    cache = {old_url: {"status": "SUCCEEDED", "text": RHM_TEXT * 5}}
    rows = [{"title": "Rheinmetall chief executive resigns", "url": "https://wire.example.org/ceo", "outlet": "Wire", "published": "2026-11-11",
             "signal_id": new_id, "band": "T2"}]
    seen: dict[str, Any] = {}

    def assess(section, item, table, previous):
        seen.update(table=table, previous=previous)
        payload = json.dumps({"section": "B", "sources": {"S1": {"text": RHM_TEXT}}})
        draft = json.loads(FakeModel().generate_content(model="m", contents=payload, config={"system_instruction": "assess"}).text)
        return {**draft, "update_status": "MATERIAL_UPDATE"}, None

    items = {"A": [], "B": [entry], "C": []}
    counts = reassess_items(items, [(entry, rows)], verify=lambda _u: {"status": "NOT_CHECKED"}, cache=cache, pool=pool, run_date=date(2026, 11, 12),
                            assess=assess, record=lambda _c: None, notes={})
    assert counts == {"triggered": 1, "updated": 1} and set(seen["table"]) == {"S1", "S2"} and seen["previous"]["headline"] == entry["headline"]
    updated = items["B"][0]
    assert updated["reassessed"]["outcome"] == "UPDATED" and updated["item_id"] == "B?1" and [r["url"] for r in updated["sources"]][-1] == "https://wire.example.org/ceo"


def test_the_lead_source_is_the_one_about_the_headline() -> None:
    entry = {"headline": "Houthi missiles target Riyadh", "facts": [],
             "sources": [{"source_id": "S1", "url": "https://abc.example/news/israel-kills-top-hamas-commander", "band": "T2", "basis": "PAGE"},
                         {"source_id": "S2", "url": "https://aj.example/x", "title": "Saudi coalition intercepts missiles over Riyadh", "band": "T2", "basis": "PAGE"}]}
    checks.cap_item(entry)
    assert entry["sources"][0]["url"] == "https://aj.example/x" and entry["sources"][0]["source_id"] == "S1"


def test_a_targeted_city_with_ste_people_is_direct_and_high() -> None:
    from grit.adverse.people import targeted_sites

    people = {"countries": {"Saudi Arabia": {"local_staff": 27, "entities": ["URS"], "travellers": 19,
                                             "sites": {"Riyadh": ["ST Engineering Saudi Limited, registered address"]}}}}
    sites = targeted_sites("Houthi missiles target Riyadh", ["Saudi Arabia"], people)
    assert sites == ["Riyadh (ST Engineering Saudi Limited, registered address)"]
    assert targeted_sites("Missiles hit Jeddah", ["Saudi Arabia"], people) == [], "another city is not a targeted STE site"
    draft = {"relationship": "Other relevant stakeholder", "exposure": checks.EXPOSURE_UNKNOWN, "severity": "Medium", "action": checks.MONITOR, "impacts": []}
    decided, _notes = checks.decide_a(draft, {"classes": [], "people": ["Saudi Arabia: 27 local staff."], "people_event": True, "people_cities": sites,
                                              "publication_stated": True})
    assert (decided["exposure"], decided["severity"], decided["targeted_sites"]) == (checks.DIRECT_EXPOSURE, "High", sites)
    assert "ST Engineering" in decided["escalation_triggers"] and decided["escalate"]


def test_an_own_product_exploit_is_at_least_medium_and_conflicts_are_flagged() -> None:
    page = ("ST Engineering iDirectiQ-Series terminals3315-Series terminals9-Series terminalslocal privilege escalationRedis IPC bus "
            "Local privilege escalation in iDirect iQ-Series terminals via unauthenticated Redis IPC bus.")
    conflicts = checks.description_conflicts([("S1", page), ("S2", "A remote attacker can run code; CVSS 9.8."), ("S3", "Rated CVSS 7.8.")])
    assert any(line.startswith("Attack route: S1 describe(s) a local flaw; S2") for line in conflicts)
    assert any("unauthenticated interface" in line for line in conflicts) and any(line.startswith("CVSS scores differ") for line in conflicts)
    assert any("description names only iQ-Series" in line for line in conflicts)
    draft = {"relationship": "Other relevant stakeholder", "exposure": checks.DIRECT_EXPOSURE, "severity": "Low", "action": checks.FOLLOW_BA, "impacts": []}
    decided, _ = checks.decide_a(draft, {"classes": ["STE"], "ste_named": True, "own_exploit": True, "conflicts": conflicts, "business_areas": ["Satcom"],
                                         "publication_stated": True})
    assert decided["severity"] == "Medium" and decided["conflicts"] == conflicts and decided["owner"] == "Satcom"


def test_a_pinned_mention_that_is_not_adverse_is_counted_not_reported() -> None:
    mention = {"severity": "Low", "action": checks.MONITOR, "tests": {"adverse_consequence": "No adverse development: revenue grew 9%."}}
    assert checks.mention_only(mention)
    assert not checks.mention_only({**mention, "tests": {"adverse_consequence": "A vulnerability exposes terminals."}})
    item = {"pinned": ["ST Engineering"], "severity": "Low", "action": checks.MONITOR}
    assert checks.apply_critic(item, {"verdict": "WITHDRAW", "rule": "NOT_ADVERSE", "reason": "results"})[0] is None, "not adverse leaves even a pinned item"
    report = {"run_date": "2026-11-16", "window": {"start": "2026-11-10", "end": "2026-11-16", "previous": None}, "items": {"A": [], "B": [], "C": []},
              "counts": {"pages": 0, "verified_pages": 0}, "attention": {"immediate": [], "monitoring": [], "lessons": []},
              "overall": checks.overall({"A": [], "B": [], "C": []}), "validation": ["ok"], "repeats": [],
              "own_reviewed": [{"headline": h, "url": "u", "names": ["ST Engineering"], "reason": "Not adverse"} for h in ("a", "b", "c", "d")]}
    assert "ST Engineering mentions reviewed (4): none adverse." in render.compose(report)
