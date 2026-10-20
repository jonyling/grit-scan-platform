"""GRIT-Scan dashboard with the chat beneath it.

Run from the repository root:

    .venv/bin/python -m grit ui

The screen reads the signal pool. Its buttons call governed functions: the reader's profile.md shapes the order (spec 39), a package
decision goes through ReviewGate(), and a management request waits for a reviewer to activate it. The screen never writes
a workflow row itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


from grit.settings import load_local_env  # noqa: E402

BOX = {"WATCHPOINT": "#FFF2CC", "STABLE": "#E2EFDA", "RED": "#F8CBAD"}


def main() -> None:
    import pandas as pd
    import streamlit as st

    from grit.agents.chat import add_request
    from grit.pool import Pool
    from grit.profiles import load_profiles
    from grit.report.brief import build_brief, routed_signals
    from grit.report.markdown import render_markdown
    from grit.ui import actions
    from grit.ui.view_model import kpis, profile_in_force, signal_detail, signal_rows

    load_local_env()
    st.set_page_config(page_title="GRIT-Scan", layout="wide")

    @st.cache_resource
    def pool() -> Pool:
        return Pool()

    db = pool()
    profiles = load_profiles()
    readers = {profile.label: profile for profile in profiles.values() if profile.briefs}
    labels = list(readers)
    default = next((number for number, label in enumerate(labels) if readers[label].profile_id == "wilson"), 0)
    with st.sidebar:
        st.title("GRIT-Scan")
        profile = readers[st.selectbox("Reader", labels, index=default)]
        runs = [row for row in db.rows("SELECT run_id, pack FROM runs WHERE status = 'COMPLETE' ORDER BY run_date DESC, pack, started_at DESC") if profile.brief_for(row["pack"])]
        if not runs:
            st.info("No run yet for this reader's packs. Start one with: .venv/bin/python -m grit weekly --dry-run")
            return
        run_id = st.selectbox("Run", [row["run_id"] for row in runs])
        st.caption(f"Aperture: {profile.aperture.replace('_', ' ').lower()}. Packs: {', '.join(profile.packs)}. Review role: {profile.review_role.replace('_', ' ').lower()}.")
        st.subheader("Profile in force")
        lines = profile_in_force(profile)
        st.write(lines[0])
        for line in lines[1:]:
            st.caption(line)

    brief = build_brief(db, run_id, profile)
    routed = routed_signals(db, run_id, profile)
    packages = db.rows("SELECT * FROM packages WHERE run_id = ? AND profile_id = ? ORDER BY version DESC", (run_id, profile.profile_id))
    decisions = {signal_id: decision for (_profile, signal_id), decision in db.latest_decisions(profile.profile_id).items()} if profile.signal_buttons else None

    meta, bottom = brief["meta"], brief["bottom_line"]
    main_column, drawer = st.columns([0.64, 0.36], gap="medium")
    with main_column:
        st.header(meta["title"])
        st.caption(f"{meta['subtitle']} | Scan window {meta['window']} | Prepared for {meta['prepared_for']}" + (" | Draft for review, not approved" if meta.get("show_review_status", True) else ""))
        st.markdown(
            f"<div style='background:{BOX.get(bottom['label'], '#FFF2CC')};padding:14px 18px;border-radius:6px;border:1px solid #1F3864'>"
            f"<b style='color:#1F3864'>{bottom['label']} — Bottom line</b><br>{bottom['text']} {bottom.get('escalation') or ''}</div>",
            unsafe_allow_html=True,
        )
        for column, (label, value) in zip(st.columns(6), kpis(routed, brief, packages).items()):
            column.metric(label, value)

        dashboard, signals_tab, packs_tab, requests_tab, alerts_tab = st.tabs(["Dashboard", "Signals", "Packs and review", "Management requests", "RED and AIRIS"])

        with dashboard:
            st.subheader("Standing risk themes" if meta.get("standing_label") == "risk theme" else "Standing conditions")
            st.caption("Select a row to focus the Co-Pilot on it.")
            picked_item = st.dataframe(
                pd.DataFrame([{"#": row["number"], "Item": row["name"] + (f" ({row['group']})" if row.get("group") else ""), "Trend": row["trend"], "Confidence": row["confidence"], "Basis": row["basis"]["plain"]} for row in brief["conditions"]]),
                hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=f"conditions-{run_id}",
            )
            chosen_items = list(picked_item.selection.rows) if picked_item is not None and picked_item.selection else []
            st.session_state["copilot_focus"] = brief["conditions"][chosen_items[0]]["condition_id"] if chosen_items else None
            left, right = st.columns(2)
            with left:
                st.subheader("Named items")
                named = [item for group in brief["projects"] + brief["entities"] for item in group["items"]] + brief["suppliers"] + brief["customers"] + brief["hazards"] + brief["competitors"]
                st.dataframe(pd.DataFrame([{"Item": item["name"], "This week": item["text"]["plain"] if item["text"] else "Nothing found"} for item in named]), hide_index=True, width="stretch")
            with right:
                if brief["management_requests"]:
                    st.subheader("Management-requested topics")
                    for row in brief["management_requests"]:
                        if row["status"] == "ACTIVE":
                            st.markdown(f"**{row['title']}**: " + ("; ".join(f"[{item['title']}]({item['url']})" for item in row["signals"][:3]) or "no signal matched this week."))
                for row in brief["entity_packs"]:
                    st.subheader(row["label"])
                    st.write(row["bottom_line"] or "No validated bottom line on this date.")
                st.subheader("Watchpoints")
                for row in brief["watchpoints"]:
                    st.markdown(f"**{row['title']}** ({row['status'].lower()}, quiet for {row['cycles']} cycle(s))  \n{row['update']['plain'] if row['update'] else row['detail']}")
                if brief["qa"]:
                    st.subheader("QA and correction log")
                    for row in brief["qa"]:
                        st.markdown(f"- **{row['item']}**: {row['correction']['plain']}")

        with signals_tab:
            only_shown = st.toggle("Only what your profile shows", value=True)
            rows = [row for row in routed if row["shown"] or not only_shown]
            table = pd.DataFrame(signal_rows(rows, decisions))
            picked = st.dataframe(table.drop(columns=["signal_id"]) if not table.empty else table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row")
            chosen = list(picked.selection.rows) if picked is not None and picked.selection else []
            if chosen:
                row = rows[chosen[0]]
                st.markdown(f"### [{row['title']}]({row['url']})")
                st.caption(f"{row.get('publisher')} | {(row.get('published_at') or 'date not stated')[:10]} | {row['signal_id']}")
                st.write(row.get("text") or "")
                for label, value in signal_detail(row):
                    st.markdown(f"**{label}.** {value}")
                if profile.signal_buttons:
                    rationale = st.text_input("Why (optional)", key=f"why-{row['signal_id']}")
                    for column, decision in zip(st.columns(3), ("APPROVE", "REJECT", "WATCH")):
                        if column.button(decision.title(), key=f"{decision}-{row['signal_id']}"):
                            actions.record_feedback(db, profile, row["signal_id"], decision, rationale)
                            st.rerun()

        with packs_tab:
            if st.toggle("Preview this week's brief", value=False):
                st.markdown(render_markdown(brief))
            if st.button("Write this week's pack and submit it for review"):
                from grit.report.package import write_pack

                st.json(write_pack(db, run_id, profile))
            for row in packages:
                st.markdown(f"**{row['package_id']} v{row['version']}**: {row['status']}. Content hash `{row['content_hash'][:16]}`. Decided by {row['decided_by'] or 'nobody yet'}.")
                files = row["files"] or {}
                for column, kind in zip(st.columns(4), ("docx", "pptx", "xlsx", "md")):
                    path = files.get(kind)
                    if path and Path(path).is_file():
                        column.download_button(f"Download {kind}", Path(path).read_bytes(), file_name=Path(path).name, key=f"dl-{row['package_id']}-{row['version']}-{kind}")
                if row["status"] == "PENDING_REVIEW" and profile.review_role in {"REVIEWER", "BA_HEAD"}:
                    for column, intent in zip(st.columns(4), ("APPROVE", "REQUEST_REWORK", "HOLD", "REJECT")):
                        if column.button(intent.replace("_", " ").title(), key=f"{intent}-{row['package_id']}-{row['version']}"):
                            result = actions.review(db, profile, row["package_id"], row["version"], intent)
                            st.info(f"ReviewGate: {result['outcome']}. The package is now {result['status']}.")
            if not packages:
                st.caption("No pack is written for this reader and run yet.")

        with requests_tab:
            requests = db.management_requests()
            if requests:
                st.dataframe(pd.DataFrame([{key: row[key] for key in ("request_id", "status", "kind", "title", "keywords", "requested_by", "origin")} for row in requests]), hide_index=True, width="stretch")
            with st.form("request"):
                title = st.text_input("Concern, topic, trend or keyword")
                keywords = st.text_input("Search keywords, comma-separated (6 or more characters, or two words)")
                kind = st.selectbox("Kind", ["CONCERN", "TOPIC", "TREND", "KEYWORD"])
                if st.form_submit_button("Propose") and title:
                    draft = {"kind": kind, "title": title, "keywords": [item.strip() for item in (keywords or title).split(",") if item.strip()]}
                    request_id = add_request(db, draft, profile_id=profile.profile_id, requested_by=profile.label)
                    st.success(f"{request_id} proposed. When a reviewer activates it, it adds queries to the next scan and sets P1.")
            if profile.review_role in {"REVIEWER", "BA_HEAD"}:
                for row in [item for item in requests if item["status"] in {"PROPOSED", "ACTIVE"}]:
                    cols = st.columns([4, 1, 1])
                    cols[0].write(f"{row['request_id']} {row['title']} ({row['status']})")
                    if row["status"] == "PROPOSED" and cols[1].button("Activate", key=f"act-{row['request_id']}"):
                        actions.set_request_status(db, profile, row["request_id"], "ACTIVE")
                        st.rerun()
                    if cols[2].button("Close", key=f"close-{row['request_id']}"):
                        actions.set_request_status(db, profile, row["request_id"], "CLOSED")
                        st.rerun()

        with alerts_tab:
            alerts = db.rows("SELECT * FROM alerts WHERE run_id = ? ORDER BY created_at", (run_id,))
            st.subheader("RED alert drafts")
            if not alerts:
                st.caption("No RED pattern fired this week.")
            for row in alerts:
                st.markdown(f"**{row['alert_id']}**: {row['status']}. Rule {row['rule'].get('rule_id')}. Recipients: {', '.join(row['recipients'])}.")
                if row["draft_path"] and Path(row["draft_path"]).is_file():
                    st.download_button("Download the draft email", Path(row["draft_path"]).read_bytes(), file_name=Path(row["draft_path"]).name, key=f"eml-{row['alert_id']}")
                if row["status"] == "DRAFT":
                    sent, dismissed = st.columns(2)
                    if sent.button("I sent it", key=f"sent-{row['alert_id']}"):
                        actions.decide_alert(db, profile, row["alert_id"], "SENT_BY_HUMAN")
                        st.rerun()
                    if dismissed.button("Dismiss", key=f"dismiss-{row['alert_id']}"):
                        actions.decide_alert(db, profile, row["alert_id"], "DISMISSED")
                        st.rerun()
            st.subheader("AIRIS handoff")
            if profile.review_role == "BA_HEAD":
                from grit.report.airis import export_to_airis, preview

                ready = preview(db, profile)
                st.caption(f"{len(ready)} tier 1-2 signal(s) linked to a register sit in packages you approved and are not yet handed off.")
                if ready:
                    st.dataframe(pd.DataFrame(ready), hide_index=True, width="stretch")
                    if st.button("Export to AIRIS"):
                        st.json(export_to_airis(db, profile))
            else:
                st.caption("Packages the BA head approves send their register-linked tier 1-2 signals to AIRIS.")

    with drawer:
        copilot_drawer(db, profile, run_id, brief, focus=st.session_state.get("copilot_focus"))


def copilot_drawer(db, profile, run_id, brief, *, focus=None):  # noqa: ANN001, ANN201
    """The Co-Pilot: scope chips, the reader's own conversation, starter questions for a selected condition, and the input."""
    import streamlit as st

    from grit.copilot.agent import ask, handle, history, record
    from grit.copilot.scope import WINDOWS, pack_labels, reach, scope_for
    from grit.acquisition.brave_plan import canonical_url
    from grit.copilot.web import WebSearch, add_to_pool

    st.subheader("Co-Pilot")
    labels = pack_labels()
    packs = reach(profile, labels)
    window = st.segmented_control("Window", list(WINDOWS), format_func=WINDOWS.get, default="90d", key="copilot_window") or "90d"
    narrowed = st.selectbox("Packs", ["all", *packs], format_func=lambda pack: "Every pack you can search" if pack == "all" else labels.get(pack, {}).get("label", pack), key="copilot_pack")
    scope = scope_for(profile, window=window, run_id=run_id, pack=None if narrowed == "all" else narrowed, focus=focus, labels=labels)
    use_web = st.toggle("Search the web when the records are silent", value=True, key="copilot_web")
    web = WebSearch(db, profile, today=scope.today) if use_web else None
    st.caption(f"Searches {', '.join(scope.label(pack) for pack in scope.packs)}." + (f" Web searches left today: {web.remaining()}." if web else "")
               + " Your conversation is private to you. Type /note to leave a note for your profile, or /concern TITLE: keywords to propose a management request.")
    pending = None
    if focus:
        name = next((row["name"] for row in brief["conditions"] if row["condition_id"] == focus), focus)
        st.caption(f"Focus: {name}")
        for column, question in zip(st.columns(3), (f"Why did {name} move?", f"What changed on {name} since last month?", f"What should we consider on {name}?")):
            if column.button(question.split(" on ")[0].split(" did ")[0] if len(question) > 40 else question, key=f"starter-{question}", help=question):
                pending = question
    box = st.container(height=560)
    with box:
        for message in db.rows("SELECT message_id, role, content, citations_json FROM chat_messages WHERE profile_id = ? ORDER BY message_id DESC LIMIT 20", (profile.profile_id,))[::-1]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                for item in [row for row in message.get("citations") or [] if row.get("kind") == "WEB" and row.get("url")]:
                    wanted = [pack for pack in item.get("packs") or scope.packs if pack in packs]
                    queued_packs = {row["pack"] for row in db.rows("SELECT a.pack FROM pool_additions a JOIN signals s ON s.signal_id = a.signal_id WHERE s.url = ? AND a.status = 'PENDING'", (canonical_url(item["url"]),))}
                    remaining = [pack for pack in wanted if pack not in queued_packs]
                    name = f"{item.get('outlet')}, {(item.get('title') or '')[:50]}"
                    if not remaining:
                        st.caption(f"Queued for the next scan: {name}")
                    elif st.button(f"Add to pool: {name}", key=f"add-{message['message_id']}-{item['id']}", help="The next scan of each pack assesses it like any other page."):
                        add_to_pool(db, profile, item, remaining)
                        st.rerun()
    question = st.chat_input("Ask the Co-Pilot", key="copilot_input") or pending
    if not question:
        return
    with box:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"), st.status("Reading the briefs, signals and library…", expanded=False) as status:
            try:
                client, model, embedder = _model_parts()
            except RuntimeError as exc:
                st.error(f"The model is not configured: {exc}")
                return

            def answer():  # noqa: ANN202
                return ask(db, scope, question, client=client, model=model, embedder=embedder,
                           embed=lambda text: embedder.embed([text], task="RETRIEVAL_QUERY")[0], past=history(db, profile.profile_id), web=web)

            result = handle(db, scope, question, answer=answer)
            status.update(label=result.get("steps") or result["status"].title(), state="complete" if result["status"] in {"VALIDATED", "COMMAND"} else "error")
    log = result.get("log") or {}
    record(db, profile, question, result["markdown"], citations=result.get("citations") or [], log=log if log.get("actor") == "CoPilot" else None, run_id=run_id)
    st.rerun()


def _model_parts():  # noqa: ANN202
    import streamlit as st

    @st.cache_resource
    def parts():  # noqa: ANN202
        from grit.agents.gemini import gemini_client
        from grit.cli import _model
        from grit.retrieval.embedder import Embedder

        client = gemini_client()
        return client, _model(None), Embedder(client=client)

    return parts()


if __name__ == "__main__":
    main()
