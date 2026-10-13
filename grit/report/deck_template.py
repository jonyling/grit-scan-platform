"""The executive-summary deck template, trimmed from the reference deck.

The reference deck (docs/reference/GRIT-Scan_Executive_Summary.pptx) carries the ST Engineering master and 249 layouts. The
brief uses two: the cover layout and the content layout. build_template() keeps those two and drops every slide, so each
weekly deck stays small. Rebuild the template when the reference deck changes:

    .venv/bin/python -m grit.report.deck_template
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation

from grit.settings import ROOT

REFERENCE = ROOT / "docs" / "reference" / "GRIT-Scan_Executive_Summary.pptx"
TEMPLATE = Path(__file__).with_name("templates") / "executive_summary.pptx"
COVER_LAYOUT = "23_Title and Content"
CONTENT_LAYOUT = "86_Title and Content"


def build_template(reference: Path = REFERENCE, destination: Path = TEMPLATE) -> Path:
    deck = Presentation(str(reference))
    slides = deck.slides._sldIdLst
    for slide_id in list(slides):
        deck.part.drop_rel(slide_id.rId)
        slides.remove(slide_id)
    keep = {COVER_LAYOUT, CONTENT_LAYOUT}
    for master in deck.slide_masters:
        for layout in list(master.slide_layouts):
            if layout.name not in keep:
                master.slide_layouts.remove(layout)
    missing = keep - {layout.name for master in deck.slide_masters for layout in master.slide_layouts}
    if missing:
        raise ValueError(f"The reference deck has no layout named {', '.join(sorted(missing))}.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    deck.save(str(destination))
    return destination


if __name__ == "__main__":
    print(build_template())
