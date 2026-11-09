"""Workplace Safety & Health functional pack (spec 47): scope, digest sections, and pack-local alerts."""

from __future__ import annotations

import json
from pathlib import Path

from grit.daily.feeds import FEEDS
from grit.daily.lane import daily_packs
from grit.ontology.pack import load_ontology_pack, manifests
from grit.pipeline.reader_scan import graph_pack
from grit.pool import Pool
from grit.profiles import load_profiles
from grit.schedule import SCHEDULE, due_packs
from grit.settings import CONFIG_DIR
from grit.wsh.alerts import draft_wsh_alerts
from grit.wsh.digest import build_digest, collect_items, render_markdown, write_digest_pack
from grit.wsh.reason import check_selection
from grit.wsh.scope import candidate_for_reasoning, classify, in_scope, match_alert, section_for_signal

FIXTURE = Path(__file__).parent / "fixtures" / "wsh" / "fixture_month.json"


def test_wsh_pack_loads_as_function_with_digest_kind() -> None:
    assert "wsh" in manifests()
    pack = load_ontology_pack(CONFIG_DIR, "wsh")
    assert pack.level == "FUNCTION"
    assert pack.report_kind == "wsh-digest"
    assert pack.scope and pack.alert_patterns
    assert len(pack.themes["themes"]) == 6
    assert {row["themeId"] for row in pack.themes["themes"]} >= {"WSH-BULLETIN-01"}
    assert graph_pack(pack) is pack
    assert "wsh" not in SCHEDULE
    from datetime import date

    assert "wsh" not in due_packs(date(2026, 11, 2))


def test_james_profile_reads_only_wsh() -> None:
    profile = load_profiles()["james"]
    assert profile.packs == ("wsh",)
    assert profile.review_role == "REVIEWER"
    brief = profile.brief_for("wsh")
    assert brief["title"] == "GRIT-Scan: Workplace Safety & Health"
    assert brief["sections"] == ["SERIOUS", "LEADING", "SIF", "HFACS", "EMERGING"]
    assert "docx" in brief["formats"]


def test_wsh_feeds_are_pack_local_and_daily_packs_include_wsh() -> None:
    wsh_feeds = [feed for feed in FEEDS if "wsh" in feed.packs]
    assert {feed.feed_id for feed in wsh_feeds} >= {"mom-wsh", "wshc"}
    assert all(feed.packs == ("wsh",) for feed in wsh_feeds)
    assert "wsh" in daily_packs()
    assert daily_packs(["wsh"]) == ["wsh"]


def test_fixture_month_scope_and_alerts() -> None:
    pack = load_ontology_pack(CONFIG_DIR, "wsh")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for row in fixture["signals"]:
        classification = classify(row["title"], row["text"], scope=pack.scope)
        section = section_for_signal(classification, [row["theme_id"]], pack.scope, title=row["title"], body=row["text"])
        keep, reason = in_scope(classification, section_id=section)
        expect = row["expect"]
        assert section == expect["section"], row["id"]
        assert keep is expect["in_scope"], (row["id"], reason)
        if not expect["in_scope"]:
            assert reason == expect["drop_reason"]
        hit = match_alert(row["title"], row["text"], classification, pack.alert_patterns)
        if expect["alert_kind"] is None:
            assert hit is None, row["id"]
        else:
            assert hit is not None and hit["kind"] == expect["alert_kind"], row["id"]


def _seed_fixture(pool: Pool, pack_name: str = "wsh") -> str:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    run_id = f"{fixture['run_date']}-{pack_name}"
    pool.start_run(run_id, run_date=fixture["run_date"], pack=pack_name, kind="READER_SCAN")
    for row in fixture["signals"]:
        signal_id = pool.add_signal(
            url=row["url"], title=row["title"], text=row["text"], publisher=row["publisher"],
            published_at=row["published_at"], lane="FIXTURE", content_hash=row["id"],
        )
        pool.add_sighting(signal_id, run_id, query_id=row["theme_id"], query=row["title"], lane="THEME", target_id=row["theme_id"], rank=1)
        keep = row["expect"]["in_scope"]
        pool.save_assessment({
            "signal_id": signal_id, "run_id": run_id, "pack": pack_name,
            "decision": "KEEP" if keep else "DROP",
            "drop_reason": None if keep else row["expect"].get("drop_reason"),
            "themes_json": [{"theme_id": row["theme_id"], "tier": "KEY", "matched": [], "where": "TITLE", "strong": True}],
            "link_json": {"sighting_targets": [row["theme_id"]]},
        })
    pool.finish_run(run_id)
    return run_id


def test_fixture_month_digest_and_alerts(tmp_path) -> None:
    pack = load_ontology_pack(CONFIG_DIR, "wsh")
    profile = load_profiles()["james"]
    pool = Pool(tmp_path / "wsh.sqlite")
    run_id = _seed_fixture(pool)
    collected = collect_items(pool, run_id, pack)
    by_id = {section["id"]: section for section in collected["sections"]}
    assert len(by_id["SERIOUS"]["discussions"]) == 1
    note = by_id["SERIOUS"]["discussions"][0]
    assert "fall from height" in note["title"].casefold()
    assert note["lesson_or_practice"] == "not established"
    assert "Energy Wheel" in note["not_established"] or "not established" in note["not_established"].casefold()
    assert len(by_id["EMERGING"]["discussions"]) == 1
    assert "heat stress" in by_id["EMERGING"]["discussions"][0]["title"].casefold()
    assert len(by_id["SIF"]["discussions"]) == 1
    assert "Energy Wheel" in by_id["SIF"]["discussions"][0]["title"]
    # Out-of-scope Texas fatality is not discussed and does not alert.
    assert all("Texas" not in (item.get("title") or "") for item in by_id["SERIOUS"]["discussions"])
    assert any(row.get("reason") == "INCIDENT_OUTSIDE_PLACE" for row in collected["dropped"])

    digest = build_digest(pool, run_id, profile)
    assert digest["meta"]["report_kind"] == "wsh-digest"
    assert "v2" not in digest
    markdown = render_markdown(digest)
    assert "Serious incidents" in markdown
    assert "Standing frame:" in markdown
    assert "Texas" not in markdown

    written = write_digest_pack(
        pool, run_id, profile, folder=tmp_path / "out", submit=False,
        use_relevance=False, use_reasoned=False,
    )
    assert Path(written["files"]["md"]).is_file()
    assert Path(written["files"]["docx"]).is_file()
    assert written["report_kind"] == "wsh-digest"

    drafts = draft_wsh_alerts(pool, run_id, pack, [profile], tmp_path / "alerts")
    kinds = {draft["kind"] for draft in drafts}
    assert kinds == {"SERIOUS_INCIDENT", "REGULATORY"}
    assert all("Texas" not in (draft.get("title") or "") for draft in drafts)
    assert all(Path(draft["path"]).is_file() for draft in drafts)


def test_method_paper_never_alerts() -> None:
    pack = load_ontology_pack(CONFIG_DIR, "wsh")
    title = "Construction Safety Research Alliance updates Energy Wheel for hazard recognition"
    text = "Dr Matthew Hallowell and CSRA describe the Energy Wheel. A worker was killed is not this paper's subject in Singapore."
    classification = classify(title, text, scope=pack.scope)
    assert classification["is_method"]
    assert match_alert(title, text, classification, pack.alert_patterns) is None


def test_ste_context_drops_residential_combat_and_unlinked_noise() -> None:
    pack = load_ontology_pack(CONFIG_DIR, "wsh")
    cases = [
        (
            "61-year-old woman dies after fall from height at Jurong East, no foul play suspected",
            "A woman died after falling from height at an HDB block in Jurong East, Singapore. Police said no foul play is suspected.",
            "SERIOUS",
            "EXCLUDED",
        ),
        (
            "Container ship sinks in Strait of Hormuz after being hit by projectile, Singapore says",
            "A Singapore-flagged container ship was hit by a projectile in the Strait of Hormuz. Singapore maritime authorities said the crew was evacuated.",
            "SERIOUS",
            "EXCLUDED",
        ),
        (
            "The importance of checking in - Safety+Health Magazine",
            "At a safety congress, speakers discussed serious injuries. No named Campbell, ICMM, CSRA or Hallowell source is present.",
            "SIF",
            "NO_FRAMEWORK",
        ),
        (
            "Hougang Heat Stress 2026 | School CCA and Outdoor Safety",
            "School CCA heat stress advice for parents in Singapore. WBGT tips for students.",
            "EMERGING",
            "EXCLUDED",
        ),
        (
            "Worker killed after fall from height at Singapore construction worksite",
            "The Ministry of Manpower said a construction worker died after a fall from height at a worksite in Singapore.",
            "SERIOUS",
            None,
        ),
    ]
    for title, text, section, drop_prefix in cases:
        classification = classify(title, text, scope=pack.scope)
        keep, reason = in_scope(classification, section_id=section, title=title, body=text)
        if drop_prefix is None:
            assert keep, (title, reason)
        else:
            assert not keep, (title, reason)
            assert reason.startswith(drop_prefix) or drop_prefix in reason, (title, reason)


def test_wide_prefilter_admits_more_than_hard_gate() -> None:
    pack = load_ontology_pack(CONFIG_DIR, "wsh")
    title = "Singapore construction scaffolding collapse injures workers"
    text = "A scaffolding collapse at a construction site in Singapore injured workers. MOM is investigating."
    classification = classify(title, text, scope=pack.scope)
    soft, soft_reason = candidate_for_reasoning(classification, section_id="SERIOUS")
    hard, _ = in_scope(classification, section_id="SERIOUS", title=title, body=text)
    assert soft and soft_reason.startswith("CANDIDATE_"), soft_reason
    assert hard
    # Method paper with no Singapore place: soft admits for reasoning; hard gate also keeps methods.
    method_title = "CSRA Construction Safety Research Alliance Energy Wheel field study"
    method_text = "Matthew Hallowell and the Construction Safety Research Alliance published Energy Wheel results."
    method = classify(method_title, method_text, scope=pack.scope)
    soft_m, reason_m = candidate_for_reasoning(method, section_id="SIF")
    assert soft_m and reason_m == "CANDIDATE_METHOD"
    # Foreign construction SEO is not a soft candidate (hard gate already drops it).
    foreign = classify(
        "Scaffolding collapse injures four in Beitou",
        "Four workers were injured after scaffolding collapsed at a construction site in Taipei.",
        scope=pack.scope,
    )
    soft_f, reason_f = candidate_for_reasoning(foreign, section_id="SERIOUS")
    assert not soft_f, reason_f
    # Surname-only Hallowell hit without safety context is not a method candidate.
    mayor = classify("McPherson elected Hallowell mayor", "Town election results from Hallowell.", scope=pack.scope)
    soft_mayor, _ = candidate_for_reasoning(mayor, section_id="SIF")
    assert not soft_mayor
    # Hard exclude still blocks the soft gate.
    residential = classify(
        "Woman dies after fall from height at Jurong East HDB",
        "A woman died after falling from an HDB block in Jurong East. Police said no foul play.",
        scope=pack.scope,
    )
    soft_x, reason_x = candidate_for_reasoning(residential, section_id="SERIOUS")
    assert not soft_x and reason_x.startswith("EXCLUDED")


def test_reason_selection_contract_requires_fact_chain() -> None:
    ok = check_selection(
        {
            "signal_id": "sig-1",
            "decision": "INCLUDE",
            "reason": "Singapore construction fatality with MOM investigation",
            "source_ids": ["sig-1"],
            "fact": "MOM reported a construction worker died after a fall from height in Singapore.",
            "what_happened": "A construction worker was installing edge protection, unhooked to move, and fell from height. MOM reported the death.",
            "failed_controls": "not established",
            "exposure": "construction workforce at STE and contractor sites in Singapore",
            "transmission": "same high-energy work and MOM enforcement reach STE construction contractors",
            "impact": "could inform fall-from-height controls and contractor oversight",
            "lesson_or_practice": "not established",
            "ste_industry": "construction",
            "section_id": "SERIOUS",
            "uncertainty": "investigation findings not yet published",
        },
        {"sig-1"},
    )
    assert ok["decision"] == "INCLUDE"
    assert ok["exposure"].startswith("construction")
    thin = check_selection(
        {
            "signal_id": "sig-1",
            "decision": "INCLUDE",
            "reason": "fatality reported",
            "fact": "A worker died.",
            "section_id": "SERIOUS",
            "what_happened": "not established",
        },
        {"sig-1"},
    )
    assert thin["decision"] == "WATCH"
    downgraded = check_selection(
        {"signal_id": "sig-1", "decision": "INCLUDE", "fact": "not established", "reason": "not established", "section_id": "SERIOUS"},
        {"sig-1"},
    )
    assert downgraded["decision"] == "EXCLUDE"


def test_widened_scan_plan_covers_six_industries() -> None:
    pack = load_ontology_pack(CONFIG_DIR, "wsh")
    plan = pack.scan_plan or {}
    queries = [q for theme in plan.get("themes") or [] for q in (theme.get("queries") or [])]
    assert len(queries) >= 50
    blob = " ".join(queries).casefold()
    for industry in ("construction", "shipyard", "aerospace", "defence", "facilities", "security"):
        assert industry in blob, industry
    for cue in ("energy wheel", "hfacs", "heat stress", "mom", "wsh council"):
        assert cue in blob, cue
    bulletin = next(theme for theme in plan["themes"] if theme["themeId"] == "WSH-BULLETIN-01")
    assert any("WSH Alert" in query for query in bulletin["queries"])
    assert any("site:wshc.sg" in query for query in bulletin["queries"])


def test_council_line_names_empty_series() -> None:
    from grit.wsh.bulletins import council_line

    line = council_line([])
    assert "No WSH Alert in this window" in line
    assert "No WSH Advisory in this window" in line
    assert "No WSH Insights in this window" in line
    filled = council_line([
        {"title": "Recent Fatalities Underscore Safe Concrete Pumping", "series": "ADVISORY"},
        {"title": "Strengthening Safety in Small-Scale Construction Works", "series": "INSIGHTS"},
    ])
    assert "WSH Advisory:" in filled
    assert "No WSH Alert in this window" in filled
    assert "Insights" in filled


def test_bulletin_selection_uses_control_list_as_lesson() -> None:
    row = check_selection(
        {
            "signal_id": "sig-b",
            "decision": "INCLUDE",
            "reason": "WSH Advisory on concrete pumping fatalities",
            "fact": "Two fatal hose-whip accidents in September 2026",
            "exposure": "construction concrete casting crews in Singapore",
            "transmission": "same pumping operations on STE and contractor sites",
            "impact": "could inform pump setup and hose handling controls",
            "lesson_or_practice": "paraphrased lesson",
            "what_happened": "On 11 September 2026 a worker was struck by a whipping hose; on 28 September another died after a hose whip and fall from the 12th storey.",
            "earlier_cases": "Similar risks on 2 January 2025 and 2 December 2023.",
            "what_companies_should_do": "1. Plan the operation. 2. Establish safe work procedures. 3. Conduct pre-operational checks.",
            "instrument": "WSH (Construction) Regulations 2007",
            "investigation_status": "not established",
            "penalty": "corporate offenders may face a maximum fine of $500,000",
            "ste_industry": "construction",
            "section_id": "SERIOUS",
            "uncertainty": "not established",
        },
        {"sig-b"},
        bulletin=True,
    )
    assert row["decision"] == "INCLUDE"
    assert row["lesson_or_practice"].startswith("1. Plan the operation")
    assert "12th storey" in row["fact"]


def test_cluster_collapse_prefers_mom_over_vendor() -> None:
    from grit.wsh.bulletins import collapse_notes

    notes = [
        {
            "title": "Heat Stress Training: What Your Programme Must Cover (2026)",
            "decision": "WATCH",
            "cluster_key": "heat-stress",
            "significance": {"evidence": 2},
            "sources": [{"outlet": "wsh-peer.com", "url": "https://wsh-peer.com/heat", "signal_id": "a"}],
        },
        {
            "title": "Heat Stress Rules From 1 December 2026",
            "decision": "INCLUDE",
            "cluster_key": "heat-stress",
            "significance": {"evidence": 4},
            "sources": [{"outlet": "mom.gov.sg", "url": "https://www.mom.gov.sg/heat", "signal_id": "b"}],
        },
    ]
    collapsed = collapse_notes(notes)
    assert len(collapsed) == 1
    assert collapsed[0]["sources"][0]["signal_id"] == "b"
    assert {src["signal_id"] for src in collapsed[0]["sources"]} == {"a", "b"}


def test_enforcement_campaign_routes_to_serious_with_energy() -> None:
    pack = load_ontology_pack(CONFIG_DIR, "wsh")
    title = "After 7 deaths, MOM urges safety time-out, tightens enforcement in construction, transport"
    text = "MOM urged a voluntary safety time-out after seven workplace fatalities and tightened stop-work orders for work at height failures."
    classification = classify(title, text, scope=pack.scope)
    section = section_for_signal(classification, ["WSH-EMERGING-01"], pack.scope, title=title, body=text)
    assert section == "SERIOUS"
    from grit.wsh.bulletins import fill_energy

    energy = fill_energy(classification)
    assert "height" in energy.casefold()


def test_precedent_stored_only_when_bulletin_names_a_date(tmp_path) -> None:
    from grit.wsh.precedents import store_from_selection, surface_for_note

    pool = Pool(tmp_path / "prec.sqlite")
    pool.start_run("2026-11-04-wsh", run_date="2026-11-04", pack="wsh", kind="READER_SCAN")
    signal_id = pool.add_signal(
        url="https://www.wshc.sg/alert/skylight",
        title="WSH Alert: Worker fell through skylight",
        text="Fragile surface fall.",
        publisher="wshc.sg",
        published_at="2024-12-13",
        lane="FIXTURE",
        content_hash="skylight-1",
    )
    pool.finish_run("2026-11-04-wsh")
    assert store_from_selection(
        pool, "2026-11-04-wsh",
        signal_id=signal_id,
        hazard="Fall from height",
        earlier_cases="Similar fatal accidents occurred in October 2023 and May 2022.",
        mechanism="fall through fragile skylight",
    )
    assert not store_from_selection(
        pool, "2026-11-04-wsh",
        signal_id=signal_id,
        hazard="Fall from height",
        earlier_cases="not established",
        mechanism="ignored",
    )
    lines = surface_for_note(pool, "Fall from height")
    assert lines
    assert "October" in lines[0] or "2023" in lines[0] or "2022" in lines[0]
    assert surface_for_note(pool, "Confined space") == []


def test_digest_markdown_includes_council_line(tmp_path) -> None:
    profile = load_profiles()["james"]
    pool = Pool(tmp_path / "wsh.sqlite")
    run_id = _seed_fixture(pool)
    digest = build_digest(pool, run_id, profile, use_reasoned=False)
    assert "Council bulletins" in (digest.get("council_line") or "")
    markdown = render_markdown(digest)
    assert "Council bulletins" in markdown


def test_next_digest_version_advances_past_disk_folders(tmp_path) -> None:
    from grit.wsh.digest import next_digest_version

    pool = Pool(tmp_path / "ver.sqlite")
    run_id = "2026-11-04-wsh"
    profile_id = "james"
    # Simulate --no-submit leaves: packages stay at v1 while folders already reach v2.
    pool.start_run(run_id, run_date="2026-11-04", pack="wsh", kind="READER_SCAN")
    pool.finish_run(run_id)
    pool.insert(
        "packages",
        {
            "package_id": f"PKG-{run_id}-{profile_id}",
            "version": 1,
            "run_id": run_id,
            "profile_id": profile_id,
            "status": "PENDING_REVIEW",
            "content_hash": "fixture",
            "membership_json": {},
            "files_json": {},
            "created_at": "2026-11-04T00:00:00Z",
        },
    )
    root = tmp_path / "runs" / run_id / profile_id
    (root / "v1").mkdir(parents=True)
    (root / "v2").mkdir(parents=True)
    assert next_digest_version(pool, run_id, profile_id, output_root=tmp_path) == 3
    assert next_digest_version(pool, run_id, profile_id, output_root=tmp_path / "empty") == 2
