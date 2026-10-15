"""Read the report library into passages, embed them, and retrieve the passages that answer a question (spec 38).

Each passage keeps its report and its page (a PDF page, a slide, or a part of a Word document), so every claim drawn from it
can be cited to the page. The passages and their vectors stay under secrets/library/, because they are derived from reports
that may be internal. Nothing here calls a language model; embedding is a deterministic lookup through the vector cache.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from grit.library.catalogue import REPORTS, Report
from grit.settings import ROOT

REPORTS_DIR = ROOT / "secrets" / "reports"
LIBRARY_DIR = ROOT / "secrets" / "library"
MIN_PAGE_CHARS = 200  # a cover, a divider, or a contents page carries no passage
TARGET_CHARS = 1200
MAX_CHARS = 1600
DOCX_PART_CHARS = 2500


@dataclass(frozen=True)
class Passage:
    passage_id: str
    report_id: str
    page: int
    page_label: str  # "p.", "slide", or "part"
    text: str


def clean(text: str) -> str:
    """Join words split across a line break, then collapse whitespace."""
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text.replace("\u00ad", ""))
    return re.sub(r"\s+", " ", text).strip()


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])", text) if part.strip()]


def chunk(text: str) -> list[str]:
    """Pack whole sentences into passages of about TARGET_CHARS. A sentence longer than MAX_CHARS is cut at a word."""
    passages: list[str] = []
    current = ""
    for sentence in _sentences(text):
        while len(sentence) > MAX_CHARS:
            cut = sentence[:MAX_CHARS].rsplit(" ", 1)[0]
            if current:
                passages.append(current)
                current = ""
            passages.append(cut)
            sentence = sentence[len(cut):].strip()
        if current and len(current) + len(sentence) + 1 > TARGET_CHARS:
            passages.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        passages.append(current)
    return passages


def pages(path: Path) -> list[tuple[int, str, str]]:
    """(number, label, text) for each page of a PDF, slide of a deck, or part of a Word document."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import logging

        from pypdf import PdfReader

        logging.getLogger("pypdf").setLevel(logging.ERROR)  # malformed cross-references are common and harmless
        return [(number, "p.", clean(page.extract_text() or "")) for number, page in enumerate(PdfReader(str(path)).pages, start=1)]
    if suffix == ".pptx":
        from pptx import Presentation

        found = []
        for number, slide in enumerate(Presentation(str(path)).slides, start=1):
            parts = [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame]
            parts += [cell.text for shape in slide.shapes if shape.has_table for row in shape.table.rows for cell in row.cells]
            found.append((number, "slide", clean("\n".join(parts))))
        return found
    if suffix == ".docx":
        from docx import Document

        document = Document(str(path))
        texts = [paragraph.text for paragraph in document.paragraphs] + [cell.text for table in document.tables for row in table.rows for cell in row.cells]
        found, current = [], ""
        for text in texts:
            current = f"{current}\n{text}"
            if len(current) >= DOCX_PART_CHARS:
                found.append((len(found) + 1, "part", clean(current)))
                current = ""
        if current.strip():
            found.append((len(found) + 1, "part", clean(current)))
        return found
    raise ValueError(f"The library reads PDF, PowerPoint, and Word files, not {path.name}.")


def passages_for(report: Report, path: Path) -> list[Passage]:
    found = []
    for number, label, text in pages(path):
        if len(text) < MIN_PAGE_CHARS:
            continue
        for index, piece in enumerate(chunk(text), start=1):
            found.append(Passage(f"{report.report_id}#{number}.{index}", report.report_id, number, label, piece))
    return found


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ingest(
    reports: Sequence[Report] = REPORTS, *, reports_dir: Path = REPORTS_DIR, library_dir: Path = LIBRARY_DIR, embedder: Any | None = None, log: Any = print
) -> dict[str, Any]:
    """Extract every report on file into passages, and embed them. A report whose file has not changed is not read again. A
    report with no file is listed as missing; nothing is invented for it."""
    from grit.retrieval.embedder import Embedder
    from grit.retrieval.store import VectorStore

    library_dir.mkdir(parents=True, exist_ok=True)
    index_path = library_dir / "index.json"
    index: dict[str, Any] = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"reports": {}}
    summary: dict[str, Any] = {"read": [], "unchanged": [], "missing": []}
    for item in reports:
        path = reports_dir / item.file
        if not path.is_file():
            summary["missing"].append(item.report_id)
            continue
        digest = _digest(path)
        known = index["reports"].get(item.report_id)
        if known and known.get("sha256") == digest and (library_dir / f"{item.report_id}.jsonl").is_file():
            summary["unchanged"].append(item.report_id)
            continue
        found = passages_for(item, path)
        with (library_dir / f"{item.report_id}.jsonl").open("w", encoding="utf-8") as handle:
            for passage in found:
                handle.write(json.dumps(asdict(passage), ensure_ascii=False) + "\n")
        index["reports"][item.report_id] = {"file": item.file, "sha256": digest, "passages": len(found), "pages": len({passage.page for passage in found})}
        summary["read"].append(item.report_id)
        log(f"  {item.report_id:24} {len(found):>4} passages from {index['reports'][item.report_id]['pages']} pages")
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    store = VectorStore(library_dir / "vectors.sqlite")
    try:
        texts = [passage.text for passage in load(index["reports"], library_dir=library_dir)]
        store.vectors(texts, task="RETRIEVAL_DOCUMENT", embedder=embedder or Embedder())
        summary["embedded"], summary["cached"] = store.embedded, store.hits
    finally:
        store.close()
    summary["passages"] = len(texts)
    return summary


def load(report_ids: Iterable[str], *, library_dir: Path = LIBRARY_DIR) -> list[Passage]:
    found = []
    for report_id in report_ids:
        path = library_dir / f"{report_id}.jsonl"
        if path.is_file():
            found += [Passage(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return found


def retrieve(
    question: str, report_ids: Sequence[str], *, k: int = 12, per_report_floor: int = 2, per_report_cap: int = 5,
    library_dir: Path = LIBRARY_DIR, embedder: Any | None = None,
) -> list[tuple[Passage, float]]:
    """The passages that best answer a question, from the named reports only. Each report on file gives its best
    per_report_floor passages first, so a short country brief is not crowded out by a long global report. The rest go by
    score, at most per_report_cap from one report. Ties keep the report and page order."""
    from grit.retrieval.embedder import Embedder
    from grit.retrieval.similarity import dot
    from grit.retrieval.store import VectorStore

    passages = load(report_ids, library_dir=library_dir)
    if not passages:
        return []
    embedder = embedder or Embedder()
    store = VectorStore(library_dir / "vectors.sqlite")
    try:
        vectors = store.vectors([passage.text for passage in passages], task="RETRIEVAL_DOCUMENT", embedder=embedder)
        query = store.vectors([question], task="RETRIEVAL_QUERY", embedder=embedder)[0]
    finally:
        store.close()
    scored = sorted(((passage, dot(query, vector)) for passage, vector in zip(passages, vectors)), key=lambda pair: -pair[1])
    chosen: list[tuple[Passage, float]] = []
    taken: dict[str, int] = {}
    for report_id in report_ids:
        for pair in [pair for pair in scored if pair[0].report_id == report_id][:per_report_floor]:
            chosen.append(pair)
            taken[report_id] = taken.get(report_id, 0) + 1
    for pair in scored:
        if len(chosen) >= k:
            break
        if pair in chosen or taken.get(pair[0].report_id, 0) >= per_report_cap:
            continue
        chosen.append(pair)
        taken[pair[0].report_id] = taken.get(pair[0].report_id, 0) + 1
    return sorted(chosen[:k], key=lambda pair: -pair[1])
