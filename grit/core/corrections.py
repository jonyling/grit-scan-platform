"""The corrections ledger (URS methodology, section 22), as reports and agents use it.

R&A records a material correction once, with `grit corrections add`. Every later report of the pack carries it in its
quality check until R&A closes it. Until then, every agent also receives it with the admitted Memory corrections.

A correction can retire an exact phrase, such as "S$600m". Reporting must not write a retired phrase. The scorecard flags
a report that does.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def ledger_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """A correction as the brief shows it."""
    return {"id": row["correction_id"], "earlier": row["earlier"], "why": row["why"], "corrected": row["corrected"], "source": row["source"],
            "update_old": row.get("update_old"), "phrase": row.get("phrase"), "recorded_by": row["recorded_by"],
            "recorded_on": str(row["recorded_at"])[:10]}


def as_admitted(row: Mapping[str, Any]) -> dict[str, Any]:
    """A correction in the form agents receive admitted Memory corrections."""
    retired = f' Do not write "{row["phrase"]}".' if row.get("phrase") else ""
    return {"kind": "CORRECTION", "ledger_id": row["correction_id"], "source_ids": [], "profile_id": None,
            "text": f"Earlier statement: {row['earlier']}. Why it was wrong or incomplete: {row['why']}. "
                    f"Corrected fact: {row['corrected']} (source: {row['source']}).{retired}"}


def _folded(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def retired_in(text: Any, corrections: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The corrections whose retired phrase appears in text, ignoring case and spacing."""
    folded = _folded(text)
    return [row for row in corrections if row.get("phrase") and _folded(row["phrase"]) in folded]
