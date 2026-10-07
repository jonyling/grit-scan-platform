"""Small dependency-free reader for values in `.xlsx` worksheets.

It supports the plain/shared-string cells used by the governed URS workbook and
keeps the impact-scoring package deployable without a third-party Excel library.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    if not letters:
        raise ValueError(f"Invalid Excel cell reference: {reference}")
    result = 0
    for character in letters.group(0):
        result = result * 26 + (ord(character) - ord("A") + 1)
    return result - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(name))
    return ["".join(node.itertext()) for node in root.findall(f"{{{_MAIN}}}si")]


def workbook_sheet_names(path: str | Path) -> tuple[str, ...]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
        return tuple(sheet.attrib["name"] for sheet in root.findall(f".//{{{_MAIN}}}sheet"))


def sheet_states(path: str | Path) -> dict[str, str]:
    """Map each sheet name to its state: visible, hidden, or veryHidden."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
        return {sheet.attrib["name"]: sheet.attrib.get("state", "visible") for sheet in root.findall(f".//{{{_MAIN}}}sheet")}


def read_worksheet(path: str | Path, sheet_name: str, *, keep_blank_rows: bool = False) -> list[list[object | None]]:
    """Read a worksheet's values, preserving empty cells needed for headers.

    Rows that hold no cells are absent from the file and are skipped by default. With
    keep_blank_rows, each row lands at its own number minus one, so blank rows survive.
    """
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {relationship.attrib["Id"]: relationship.attrib["Target"] for relationship in relationships.findall(f"{{{_PKG_REL}}}Relationship")}
        selected = next((sheet for sheet in workbook.findall(f".//{{{_MAIN}}}sheet") if sheet.attrib["name"] == sheet_name), None)
        if selected is None:
            raise ValueError(f"Workbook sheet is missing: {sheet_name}")
        relationship_id = selected.attrib[f"{{{_REL}}}id"]
        target = targets[relationship_id]
        worksheet_path = posixpath.normpath(posixpath.join("xl", target))
        root = ET.fromstring(archive.read(worksheet_path))
        shared = _shared_strings(archive)
        rows: list[list[object | None]] = []
        for row in root.findall(f".//{{{_MAIN}}}sheetData/{{{_MAIN}}}row"):
            if keep_blank_rows:
                while len(rows) < int(row.attrib.get("r", len(rows) + 1)) - 1:
                    rows.append([])
            output: list[object | None] = []
            for cell in row.findall(f"{{{_MAIN}}}c"):
                index = _column_index(cell.attrib["r"])
                while len(output) <= index:
                    output.append(None)
                cell_type = cell.attrib.get("t")
                raw = cell.findtext(f"{{{_MAIN}}}v")
                inline = cell.find(f"{{{_MAIN}}}is")
                if inline is not None:
                    value: object | None = "".join(inline.itertext())
                elif raw is None:
                    value = None
                elif cell_type == "s":
                    value = shared[int(raw)]
                elif cell_type == "b":
                    value = raw == "1"
                else:
                    value = raw
                output[index] = value
            rows.append(output)
        return rows
