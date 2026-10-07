"""Streamlit review screen.

Run from the repository root:

    streamlit run grit/ui/app.py

The screen calls ReviewGate through grit.ui.actions. It does not write the ledger.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grit.ui.actions import submit_from_screen
from grit.ui.seed import ACCEPTED_AT, CORRELATION_ID, seed_pending_package
from grit.ui.view_model import acquired_detail, package_view, signal_detail, signal_list


def main() -> None:
    from grit.settings import load_local_env

    load_local_env()
    import streamlit as st

    st.set_page_config(page_title="GRIT-Scan review", layout="wide")
    if "supervisor" not in st.session_state:
        supervisor, package_id, assessments = seed_pending_package()
        st.session_state.supervisor = supervisor
        st.session_state.package_id = package_id
        st.session_state.assessments = assessments
        st.session_state.weekly_delta = "Draft for senior-management review. No loss is asserted."

    supervisor = st.session_state.supervisor
    package_id = st.session_state.package_id
    st.title("GRIT-Scan review")
    st.caption("A missing fact reads as not established. This screen does not write state.")
    _show_acquired(st)

    st.divider()
    st.subheader("Sample review package")
    st.caption("SIG-1 is a sample. It is not one of the Currents rows written to Dataverse.")
    signals = signal_list(supervisor)
    selected = st.sidebar.radio("Sample signals", [item["signal_id"] for item in signals])
    chosen = next(item for item in signals if item["signal_id"] == selected)
    record = dict(st.session_state.assessments.get(selected, {}))
    record["state"] = chosen["state"]
    record["signal_id"] = selected

    detail, package = st.columns(2)
    with detail:
        st.subheader("Signal")
        for label, value in signal_detail(record):
            st.write(f"**{label}:** {value}")
    with package:
        st.subheader("Weekly package")
        for label, value in package_view(supervisor, package_id, st.session_state.weekly_delta):
            st.write(f"**{label}:** {value}")

    reviewer_id = st.text_input("Reviewer", value="reviewer-01")
    note = st.text_area("Rework note", value="")
    columns = st.columns(4)
    intents = (
        ("Approve", "APPROVE", []),
        ("Return for rework", "REQUEST_REWORK", [_finding(note)]),
        ("Reject", "REJECT", []),
        ("Hold", "HOLD", []),
    )
    for column, (label, intent, findings) in zip(columns, intents):
        if column.button(label) and reviewer_id.strip():
            result = submit_from_screen(
                supervisor,
                package_id=package_id,
                reviewer_id=reviewer_id.strip(),
                reviewer_intent=intent,
                findings=findings,
                correlation_id=CORRELATION_ID,
                accepted_at=ACCEPTED_AT,
            )
            decision = result["decision"]
            st.session_state.last_decision = decision["review_outcome"]
            if result["approval"] is not None:
                st.session_state.last_approval = (
                    f"{result['approval']['package_id']} version {result['approval']['package_version']} "
                    f"hash {result['approval']['package_hash']}"
                )
    if st.session_state.get("last_decision"):
        st.success(f"ReviewGate: {st.session_state.last_decision}")
    if st.session_state.get("last_approval"):
        st.write(st.session_state.last_approval)


def _show_acquired(st) -> None:
    rows, note = _acquired_rows()
    st.subheader(f"Acquired signals ({len(rows)})")
    st.caption(note)
    if not rows:
        st.write("No acquired signal is on this screen yet.")
        return
    for index, row in enumerate(rows, start=1):
        title = row.get("title") or row.get("url") or "untitled"
        with st.expander(f"{index}. {title}", expanded=index == 1):
            for label, value in acquired_detail(row):
                st.write(f"**{label}:** {value}")


def _acquired_rows() -> tuple[list[dict], str]:
    saved = _snapshot_rows()
    live = _dataverse_rows()
    rows = _merge_rows(saved, live or [])
    if live is not None and saved:
        return rows, f"Dataverse returned {len(live)}. The last write saved {len(saved)}. No score is asserted."
    if live is not None:
        return rows, "Read from Dataverse. No score is asserted."
    if saved:
        return rows, "Local snapshot from the last Currents write. No score is asserted."
    return [], "No Dataverse credentials in this process, and no local snapshot yet."


def _snapshot_rows() -> list[dict]:
    import json

    snapshot = _ROOT / "output" / "currents-latest.json"
    if not snapshot.is_file():
        return []
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    return list(payload.get("signals") or [])


def _merge_rows(saved: list[dict], live: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for row in [*saved, *live]:
        key = str(row.get("url") or row.get("title") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def _dataverse_rows() -> list[dict] | None:
    import os

    if not os.environ.get("DATAVERSE_ORG_URL"):
        return None
    from grit.dataverse.client import DataverseClient
    from grit.dataverse.choices import EVIDENCE_LOW

    client = DataverseClient.from_env()
    payload = client.get(
        "grit_signals?$select=grit_signalname,grit_headline,grit_sourcename,grit_sourceurl,grit_evidenceconfidence,grit_correlationid,createdon"
        "&$orderby=createdon desc&$top=20"
    )
    rows = []
    for row in payload.get("value") or []:
        if not str(row.get("grit_correlationid") or "").startswith("CURRENTS-"):
            continue
        evidence = row.get("grit_evidenceconfidence")
        rows.append(
            {
                "title": row.get("grit_headline") or row.get("grit_signalname"),
                "publisher": row.get("grit_sourcename"),
                "url": row.get("grit_sourceurl"),
                "evidence": "LOW" if evidence == EVIDENCE_LOW else evidence,
            }
        )
    return rows


def _finding(note: str) -> dict[str, object]:
    return {
        "finding_id": "F-UI-1",
        "finding_type": "MISSING_EVIDENCE",
        "severity": "MATERIAL",
        "description": note.strip() or "Returned from the review screen",
        "resolution_required": True,
    }


if __name__ == "__main__":
    main()
