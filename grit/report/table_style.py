"""Shared Word table header chrome for weekly, daily-review, WSH and adverse reports.

Style first: one navy header cell look (the weekly brief's Table Grid). Column wording stays per-report unless
Wilson / Jony sign off a rename. Writers call header_row() instead of inventing their own shading.
"""

from __future__ import annotations

from typing import Any, Sequence

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

NAVY_HEX = "1F3864"
NAVY = RGBColor(0x1F, 0x38, 0x64)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TABLE_STYLE = "Table Grid"

# Named header sets. Intentional column differences across reports are documented here; do not silently rename.
WEEKLY_KEY_RISK = (
    "News/Developments",
    "News/Information Source",
    "Date and Time",
    "Relevance/Potential Impact",
    "Priority/Trend",
)
DAILY_REVIEW = ("#", "Signal", "Source", "Tier", "RED")
WSH_INCIDENT_LOG = ("Title", "Date", "Place", "Energy", "Source")
ADVERSE_SECTION = (
    "Event / Sources, date and time",
    "Why adverse & impact",
    "Severity & action",
    "Lessons learnt",
)


def shade_cell(cell: Any, fill: str = NAVY_HEX) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shading)


def header_row(document: Any, headers: Sequence[str], widths: Sequence[float] | None = None) -> Any:
    """A Table Grid with navy header cells and white bold 9pt labels. widths are centimetres when given."""
    table = document.add_table(rows=1, cols=len(headers))
    table.style = TABLE_STYLE
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for index, (cell, text) in enumerate(zip(table.rows[0].cells, headers)):
        if widths is not None and index < len(widths):
            cell.width = Cm(widths[index])
        shade_cell(cell, NAVY_HEX)
        # Clear any default paragraph text the style left behind.
        cell.text = ""
        run = cell.paragraphs[0].add_run(text)
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = WHITE
    return table
