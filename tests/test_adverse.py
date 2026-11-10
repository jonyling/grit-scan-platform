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
