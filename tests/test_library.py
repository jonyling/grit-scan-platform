"""Spec 38: the report library and the State of the Situation documents. No network, no model."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import pytest

from grit.agents.situation import IN_BRIEF, NARRATIVE, SECTIONS, SYSTEM, UNCERTAINTIES, INDICATORS, check_section
from grit.library import catalogue, sots
from grit.library.catalogue import Report, Topic
from grit.library.store import chunk, clean, ingest, load, retrieve
from grit.pipeline.reader_scan import stub_client


class _Words:
    """A fake embedder: words hashed into 256 dimensions. Texts that share words point the same way."""

    model, dimensions, batch_size = "fake-words", 256, 50

    def embed(self, texts: list[str], *, task: str) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for word in re.findall(r"[a-z]{4,}", text.casefold()):
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


def test_clean_joins_hyphenated_words_and_chunk_keeps_whole_sentences() -> None:
    assert clean("The econ-\nomy grew.\n\nIt   slowed.") == "The economy grew. It slowed."
    text = " ".join(f"Sentence number {number} says something about Gulf spending." for number in range(80))
    pieces = chunk(text)
    assert len(pieces) > 1 and all(len(piece) <= 1600 for piece in pieces)
    assert all(piece.endswith(".") for piece in pieces), "a passage ends at a sentence boundary"
    assert " ".join(pieces) == text


def _report_docx(path: Path, paragraphs: list[str]) -> None:
    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))


GULF = [
    "Gulf economies have depended on oil revenue since the 1970s. Saudi Arabia launched Vision 2030 in 2016 to diversify its economy. " * 6,
    "Fiscal break-even oil prices shape government spending capacity. The IMF projects growth of 3.5 percent in 2026 for the region. " * 6,
    "Defence localisation targets require foreign suppliers to build local industry. Saudi Arabia targets 50 percent local defence spending. " * 6,
]
QUANTUM = ["Quantum computers could break public-key cryptography. Experts surveyed see a cryptographically relevant machine as possible within ten years. " * 6]


def _library(tmp_path: Path) -> tuple[list[Report], Path, Path]:
    reports_dir, library_dir = tmp_path / "reports", tmp_path / "library"
    reports_dir.mkdir()
    _report_docx(reports_dir / "gulf.docx", GULF)
    _report_docx(reports_dir / "quantum.docx", QUANTUM)
    reports = [
        Report("R-GULF", "gulf.docx", "Gulf Outlook: Charting a path", "International Monetary Fund", "May 2025", "PUBLIC", None, ("IMF",)),
        Report("R-QUANTUM", "quantum.docx", "Quantum Timeline", "Global Risk Institute", "December 2024", "PUBLIC", None, ("GRI",)),
        Report("R-MISSING", "missing.pdf", "Not on file", "Nobody", "2026", "PUBLIC"),
    ]
    return reports, reports_dir, library_dir


def test_ingest_reads_each_report_once_and_lists_a_missing_one(tmp_path: Path) -> None:
    reports, reports_dir, library_dir = _library(tmp_path)
    first = ingest(reports, reports_dir=reports_dir, library_dir=library_dir, embedder=_Words(), log=lambda _m: None)
    assert first["read"] == ["R-GULF", "R-QUANTUM"] and first["missing"] == ["R-MISSING"] and first["embedded"] == first["passages"]
    again = ingest(reports, reports_dir=reports_dir, library_dir=library_dir, embedder=_Words(), log=lambda _m: None)
    assert again["unchanged"] == ["R-GULF", "R-QUANTUM"] and again["read"] == [] and again["embedded"] == 0, "an unchanged file is not read or embedded again"
    passages = load(["R-GULF"], library_dir=library_dir)
    assert passages and all(item.page_label == "part" and item.passage_id.startswith("R-GULF#") for item in passages)


def test_retrieve_reads_only_the_named_reports_and_gives_each_its_floor(tmp_path: Path) -> None:
    reports, reports_dir, library_dir = _library(tmp_path)
    ingest(reports, reports_dir=reports_dir, library_dir=library_dir, embedder=_Words(), log=lambda _m: None)
    found = retrieve("Saudi defence localisation spending", ["R-GULF"], k=4, library_dir=library_dir, embedder=_Words())
    assert found and {item.report_id for item, _score in found} == {"R-GULF"}
    mixed = retrieve("Saudi defence localisation spending", ["R-GULF", "R-QUANTUM"], k=3, per_report_floor=1, library_dir=library_dir, embedder=_Words())
    assert {item.report_id for item, _score in mixed} == {"R-GULF", "R-QUANTUM"}, "every named report gets its floor, even an off-topic one"
    assert retrieve("anything", ["R-MISSING"], library_dir=library_dir, embedder=_Words()) == []


PASSAGES = {
    "P1": {"text": "Saudi Arabia launched Vision 2030 in 2016 to diversify its economy away from oil.", "aliases": ["IMF", "International Monetary Fund"]},
    "P2": {"text": "The IMF projects growth of 3.5 percent in 2026 for the region.", "aliases": ["IMF", "International Monetary Fund"]},
}
GOOD_PARAGRAPH = (
    "Saudi Arabia launched Vision 2030 in 2016 to move its economy away from oil [P1]. "
    "The IMF projects regional growth of 3.5 percent in 2026 [P2]. " + "The plan reshaped public spending priorities across the kingdom [P1]. " * 16
)


def _narrative(**changes) -> dict:
    return {"paragraphs": [GOOD_PARAGRAPH], "quotes": [{"passage_id": "P1", "quote": "launched Vision 2030 in 2016"}], "not_covered": None, **changes}


def test_a_narrative_section_passes_only_when_every_sentence_is_grounded() -> None:
    assert check_section(_narrative(), NARRATIVE, PASSAGES) == []

    def problems(paragraph: str, **changes) -> str:
        return " | ".join(check_section(_narrative(paragraphs=[paragraph + " " + GOOD_PARAGRAPH], **changes), NARRATIVE, PASSAGES))

    assert "UNCITED" in problems("Gulf states are wealthy.")
    assert "UNKNOWN_PASSAGE" in problems("Gulf states are wealthy [P9].")
    assert "UNGROUNDED_NUMBER" in problems("Vision 2030 has 96 programmes [P1].")
    assert "UNGROUNDED_NUMBER" in problems("Growth reached 3.5 percent [P1].", ), "a number counts only in the passage that sentence cites"
    assert "UNATTRIBUTED_FORECAST" in problems("Regional growth will reach 3.5 percent in 2026 [P2].")
    assert "UNATTRIBUTED_FORECAST" not in problems("According to the outlook, growth is expected to reach 3.5 percent in 2026 [P2].")
    assert "BANNED_TERM" in problems("The likelihood of reform rose [P1].")
    assert "QUOTE_NOT_VERBATIM" in " ".join(check_section(_narrative(quotes=[{"passage_id": "P1", "quote": "launched an ambitious plan in 2016"}]), NARRATIVE, PASSAGES))
    assert "NO_QUOTE" in " ".join(check_section(_narrative(quotes=[]), NARRATIVE, PASSAGES))
    thin = _narrative(paragraphs=["Saudi Arabia launched Vision 2030 in 2016 [P1]."])
    assert "TOO_THIN" in " ".join(check_section(thin, NARRATIVE, PASSAGES))
    assert check_section({**thin, "not_covered": "The library does not describe the plan's results."}, NARRATIVE, PASSAGES) == [], "a short section is fine when it says what is missing"
    assert "EMPTY" in " ".join(check_section(_narrative(paragraphs=[]), NARRATIVE, PASSAGES))


def test_uncertainties_and_indicators_have_their_own_shapes() -> None:
    why = "Spending depends on oil revenue [P1]."
    items = [{"uncertainty": f"Question {letter}?", "why_it_matters": why} for letter in "ABC"]
    quotes = [{"passage_id": "P1", "quote": "launched Vision 2030 in 2016"}]
    assert check_section({"items": items, "quotes": quotes}, UNCERTAINTIES, PASSAGES) == []
    assert "BAD_COUNT" in " ".join(check_section({"items": items[:2], "quotes": quotes}, UNCERTAINTIES, PASSAGES))
    assert "MARK_IN_TEXT" in " ".join(check_section({"items": [{**items[0], "uncertainty": "Will it last? [P1]"}, *items[1:]], "quotes": quotes}, UNCERTAINTIES, PASSAGES))
    indicators = [{"indicator": f"Measure {letter}", "reading": None} for letter in "ABC"] + [{"indicator": "Regional growth", "reading": "The IMF projects 3.5 percent for 2026 [P2]."}]
    assert check_section({"items": indicators, "quotes": [{"passage_id": "P2", "quote": "projects growth of 3.5 percent"}]}, INDICATORS, PASSAGES) == []
    assert "BAD_COUNT" in " ".join(check_section({"items": indicators[:3], "quotes": quotes}, INDICATORS, PASSAGES))
    assert "UNGROUNDED_NUMBER" in " ".join(check_section({"items": [{**items[0], "uncertainty": "Will oil stay above 80 dollars?"}, *items[1:]], "quotes": quotes}, UNCERTAINTIES, PASSAGES))
    grounded = [{**items[0], "uncertainty": "Will growth stay near 3.5 percent?", "why_it_matters": "The IMF projects 3.5 percent [P2]."}, *items[1:]]
    assert check_section({"items": grounded, "quotes": quotes + [{"passage_id": "P2", "quote": "projects growth of 3.5 percent"}]}, UNCERTAINTIES, PASSAGES) == []


def _analyst(system: str, payload: dict) -> dict:
    assert system == SYSTEM
    ids = list(payload["passages"])
    first = ids[0]
    text = payload["passages"][first]["text"]
    quote = text[:40]
    sentence = f"{text.split('. ')[0].rstrip('.')} [{first}]."
    kind = payload["section"]["kind"]
    if kind in (NARRATIVE, IN_BRIEF):
        return {"paragraphs": [" ".join([sentence] * (20 if kind == NARRATIVE else 8))], "quotes": [{"passage_id": first, "quote": quote}], "not_covered": None}
    if kind == UNCERTAINTIES:
        return {"items": [{"uncertainty": f"Open question {letter}?", "why_it_matters": sentence} for letter in "ABC"], "quotes": [{"passage_id": first, "quote": quote}], "not_covered": None}
    return {"items": [{"indicator": f"Indicator {letter}", "reading": sentence if letter == "A" else None} for letter in "ABCD"], "quotes": [{"passage_id": first, "quote": quote}], "not_covered": None}


def test_a_topic_document_is_built_cited_and_written(tmp_path: Path, monkeypatch) -> None:
    reports, reports_dir, library_dir = _library(tmp_path)
    monkeypatch.setattr(catalogue, "REPORTS", reports)
    ingest(reports, reports_dir=reports_dir, library_dir=library_dir, embedder=_Words(), log=lambda _m: None)
    item = Topic("SOTS-TEST", "Gulf spending", "How Gulf fiscal positions shape spending.", ("STE-SHIFT-04", "STE-GC-01"), ("R-GULF",),
                 ("Saudi Arabia",), ("geopolitical",))
    audit: list[dict] = []
    document = sots.build_topic(item, client=stub_client(_analyst), model="stub", embedder=_Words(), library_dir=library_dir, log=lambda _m: None, record=audit.append)
    assert [row["status"] for row in document["sections"]] == ["DRAFTED"] * len(SECTIONS) and document["inBrief"]["status"] == "DRAFTED"
    assert len(audit) == len(SECTIONS) + 1 and all(entry["actor"] == "SituationAnalyst" for entry in audit)
    assert [row["name"] for row in document["covers"]] == ["Middle East defence and infrastructure spending", "Saudi, UAE and Qatar investment signals"]
    numbering = {row["passage_id"]: key for key, row in document["passages"].items()}
    assert len(numbering) == len(document["passages"]), "one passage keeps one id across the document"

    markdown = sots.to_markdown(document)
    assert "[P" not in markdown and "(IMF, May 2025, part 1)" in markdown
    assert "## STE exposure" in markdown and "## Sources" in markdown and "Signed off by" in markdown
    assert "International Monetary Fund, *Gulf Outlook: Charting a path*, May 2025" in markdown
    files = sots.write(document, tmp_path / "out", audit)
    assert Path(files["docx"]).is_file() and json.loads(Path(files["json"]).read_text())["topicId"] == "SOTS-TEST"
    assert len((tmp_path / "out" / "audit.jsonl").read_text().splitlines()) == len(audit)


def test_a_topic_with_no_report_says_so_and_calls_no_model(tmp_path: Path) -> None:
    item = catalogue.topic("SOTS-COMPETITORS")
    assert item.reports == () and item.coverage == "GAP"

    def never(system: str, payload: dict) -> dict:
        raise AssertionError("no model call for a topic with no report")

    document = sots.build_topic(item, client=stub_client(never), model="stub", embedder=_Words(), library_dir=tmp_path, log=lambda _m: None)
    assert {row["status"] for row in document["sections"]} == {"NOT_COVERED"} and "library holds no report" in sots.to_markdown(document)


def test_the_catalogue_is_consistent() -> None:
    from grit.ontology.group_seed import CONDITIONS, SHIFTS

    ids = [item.report_id for item in catalogue.REPORTS]
    assert len(ids) == len(set(ids))
    assert all(report_id in ids for item in catalogue.TOPICS for report_id in item.reports)
    covered = {theme for item in catalogue.TOPICS for theme in item.themes}
    assert {row["id"] for row in CONDITIONS} <= covered, "every dashboard theme has a State of the Situation"
    assert {f"STE-SHIFT-{number:02d}" for number in range(1, len(SHIFTS) + 1)} <= covered, "and every strategic shift"
    assert len(catalogue.TOPICS) == 11 and set(catalogue.PILOT) <= {item.topic_id for item in catalogue.TOPICS}
    assert all(item.gap_sources for item in catalogue.TOPICS if item.coverage == "GAP")


def test_a_citation_groups_pages_by_report_and_uses_short_names() -> None:
    table = {
        "P1": {"report_id": "PWC-SEMI-2026", "page_label": "p.", "page": 64},
        "P2": {"report_id": "PWC-SEMI-2026", "page_label": "p.", "page": 18},
        "P3": {"report_id": "DSC-RISK-REFRESH-2026", "page_label": "slide", "page": 13},
        "P4": {"report_id": "PWC-SEMI-2026", "page_label": "p.", "page": 64},
    }
    assert sots.render_text("Memory is tight [P1, P2, P3, P4].", table) == "Memory is tight (PwC Semiconductor, Nov 2025, pp. 18, 64; DS&C Risk Refresh, 5 Aug 2026, slide 13)."
    assert sots.render_text("Unknown [P9].", table) == "Unknown."


def test_exposure_names_suppliers_and_customers_from_the_registers(tmp_path: Path) -> None:
    item = Topic("SOTS-X", "Chips", "How chips flow.", (), (), ("Qatar",), ("supply chain",), counterparties=("NVIDIA",))
    (tmp_path / "ba").mkdir()
    registers = {
        "pack.json": {"pack": "ba", "level": "BA", "label": "Test BA", "registers": {key: f"{key}.json" for key in ("source_register", "exposure", "lob_profiles", "themes", "baseline")}},
        "exposure.json": {"entities": [{"entityName": "STE Qatar LLC", "countryOfIncorporation": "Qatar"}, {"entityName": "STE Inc", "countryOfIncorporation": "USA"}],
                          "suppliers": [{"supplierName": "NVIDIA Corporation"}, {"supplierName": "NVIDIAN Foods"}], "customers": [{"customerName": "Qatar DPE Customer"}]},
        "themes.json": {"themes": [{"themeId": "BA-KEY-01", "themeName": "Major supply chain disruption", "tier": "KEY"}]},
        "source_register.json": {"sources": []}, "lob_profiles.json": {"lobs": []}, "baseline.json": {"conditions": []},
    }
    for name, body in registers.items():
        (tmp_path / "ba" / name).write_text(json.dumps(body), encoding="utf-8")
    found = sots.exposure(item, tmp_path)["business_areas"][0]
    assert [row["name"] for row in found["entities"]] == ["STE Qatar LLC"]
    assert found["suppliers"] == ["NVIDIA Corporation"], "a fragment matches a whole word, not part of one"
    assert found["customers"] == ["Qatar DPE Customer"] and [row["id"] for row in found["themes"]] == ["BA-KEY-01"]


def test_a_redraft_replaces_only_the_named_section_and_keeps_passage_ids(tmp_path: Path, monkeypatch) -> None:
    reports, reports_dir, library_dir = _library(tmp_path)
    monkeypatch.setattr(catalogue, "REPORTS", reports)
    ingest(reports, reports_dir=reports_dir, library_dir=library_dir, embedder=_Words(), log=lambda _m: None)
    item = Topic("SOTS-TEST", "Gulf spending", "How Gulf fiscal positions shape spending.", (), ("R-GULF",))
    document = sots.build_topic(item, client=stub_client(_analyst), model="stub", embedder=_Words(), library_dir=library_dir, log=lambda _m: None)
    withheld = {**document, "sections": [{**row, "status": "WITHHELD", "draft": None} if row["key"] == "ACTORS" else row for row in document["sections"]]}
    calls: list[str] = []

    def analyst(system: str, payload: dict) -> dict:
        calls.append(payload["section"]["title"])
        return _analyst(system, payload)

    again = sots.redraft_sections(withheld, item, ["ACTORS"], client=stub_client(analyst), model="stub", embedder=_Words(), library_dir=library_dir, log=lambda _m: None)
    assert calls == ["Key actors and their positions"]
    assert {row["key"]: row["status"] for row in again["sections"]}["ACTORS"] == "DRAFTED"
    assert [row for row in again["sections"] if row["key"] != "ACTORS"] == [row for row in document["sections"] if row["key"] != "ACTORS"]
    assert {key: row["passage_id"] for key, row in document["passages"].items()}.items() <= {key: row["passage_id"] for key, row in again["passages"].items()}.items()


def test_a_broken_profile_file_is_named_in_the_error(tmp_path: Path) -> None:
    from grit.profiles import load_profiles

    (tmp_path / "wilson.json").write_text('{"profileId": "wilson" "name": "Wilson"}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"wilson\.json is not valid JSON: Expecting ',' delimiter at line 1"):
        load_profiles(tmp_path)


def test_review_fixes_sentence_splits_reading_shape_and_keyword_boundaries() -> None:
    from grit.agents.situation import cited_problems, sentences

    assert sentences("Growth slowed [P1]. 2031 output fell [P1]. $5 billion was raised [P1].") == ["Growth slowed [P1].", "2031 output fell [P1].", "$5 billion was raised [P1]."]
    assert sentences("The U.S. Department of Commerce tightened rules [P1]. E.g. chips were listed [P1].") == ["The U.S. Department of Commerce tightened rules [P1].", "E.g. chips were listed [P1]."]
    passages = {"P1": {"text": "Saudi growth slowed to 1.3 percent in 2024, and 2031 output fell 77 percent.", "aliases": []}}
    assert "UNCITED" in " ".join(cited_problems("Saudi growth slowed to 1.3 percent in 2024 [P1]. 2031 output fell 77 percent.", passages, "p")), "a sentence that starts with a year needs its own citation"
    items = [{"indicator": f"Measure {letter}", "reading": None} for letter in "ABC"] + [{"indicator": "Oil price", "reading": 81}]
    assert "BAD_SHAPE" in " ".join(check_section({"items": items, "quotes": []}, INDICATORS, PASSAGES))
    assert sots._keyword("AI").search("Adverse outcomes from AI use") and not sots._keyword("AI").search("Major supply chain disruption")
    assert not sots._keyword("AI").search("aircraft maintenance") and sots._keyword("cryptograph").search("Post-quantum cryptography migration")
