"""Streamlit review screen.

Run from the repository root:

    streamlit run ui/app.py

The screen calls ReviewGate through ui.actions. It does not write the ledger.
"""

from __future__ import annotations

from ui.actions import submit_from_screen
from ui.seed import ACCEPTED_AT, CORRELATION_ID, seed_pending_package
from ui.view_model import package_view, signal_detail, signal_list


def main() -> None:
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

    signals = signal_list(supervisor)
    selected = st.sidebar.radio("Signals", [item["signal_id"] for item in signals])
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
