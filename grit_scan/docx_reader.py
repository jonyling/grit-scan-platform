"""Dependency-free .docx text/hyperlink extraction.

A .docx is a zip container of XML parts, same shape as .xlsx -- this mirrors
xlsx_reader.py's approach (standard-library only, no python-docx) since
python-docx is not installed in this environment.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _relationships(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        raw = archive.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}
    root = ET.fromstring(raw)
    return {rel.get("Id"): rel.get("Target") for rel in root}


def read_paragraphs(path: str | Path) -> tuple[str, ...]:
    """Return one string per non-empty paragraph, in document order."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_W_NS}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_W_NS}t"))
        if text.strip():
            paragraphs.append(text)
    return tuple(paragraphs)


def read_hyperlinks(path: str | Path) -> tuple[tuple[str, str], ...]:
    """Return (link_text, target_url) pairs, in document order."""
    with zipfile.ZipFile(path) as archive:
        relationships = _relationships(archive)
        root = ET.fromstring(archive.read("word/document.xml"))
    links: list[tuple[str, str]] = []
    for link in root.iter(f"{_W_NS}hyperlink"):
        rel_id = link.get(f"{_R_NS}id")
        if not rel_id or rel_id not in relationships:
            continue
        text = "".join(node.text or "" for node in link.iter(f"{_W_NS}t"))
        links.append((text, relationships[rel_id]))
    return tuple(links)


def read_tables(path: str | Path) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """Return one tuple-of-rows per table, each row a tuple of cell text."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    tables: list[tuple[tuple[str, ...], ...]] = []
    for table in root.iter(f"{_W_NS}tbl"):
        rows: list[tuple[str, ...]] = []
        for row in table.iter(f"{_W_NS}tr"):
            cells = []
            for cell in row.iter(f"{_W_NS}tc"):
                text = "".join(node.text or "" for node in cell.iter(f"{_W_NS}t"))
                cells.append(text)
            rows.append(tuple(cells))
        tables.append(tuple(rows))
    return tuple(tables)
