"""Attach an action that already exists to the signal, scenario, or prediction it belongs to.

The function does not write an action. A signal with no reviewed option and no triggered scenario response gets none.
"""

from __future__ import annotations

from typing import Any, Mapping

from grit.report.brief import text_only
from grit.report.notices import suggestion_line


def suggestions_for_signal(brief: Mapping[str, Any], signal_id: str) -> list[str]:
    """Options and triggered scenario responses that cite this signal, in reading order."""
    lines: list[str] = []

    def add(action: str) -> None:
        line = suggestion_line(action)
        if line and line not in lines:
            lines.append(line)

    for row in brief.get("conditions") or []:
        for option in row.get("options") or []:
            if signal_id in (option.get("source_ids") or []):
                add(text_only(option["text"]["runs"]))
    for shift in (brief.get("scenarios") or {}).get("shifts") or []:
        for case in shift.get("cases") or []:
            triggered = {response.get("trigger"): response for response in case.get("responses_triggered") or []}
            for item in case.get("signposts_seen") or []:
                response = triggered.get(item.get("number"))
                if response and signal_id in (item.get("signal_ids") or []):
                    add(f"{response.get('action')} Owner: {response.get('owner')}.")
    return lines
