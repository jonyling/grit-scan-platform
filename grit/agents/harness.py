"""DeepSeek Harness calls for a reasoned weekly run. The harness proposes. The weekly contract decides.

A live call runs in a temporary directory that holds only the task JSON. It does not use this checkout as its workspace.
The private reasoning stream is dropped before anything is stored. A stored decision stays under 32 KB.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.agents.weekly import HOP_CHARS, MAX_HOPS, REASON_CHARS, ContractError
from grit.settings import ROOT, load_local_env

TRACE_BYTES = 32 * 1024
PRIVATE = frozenset({"thinking", "reasoning", "reasoning_content", "chain_of_thought", "thought", "reasoning_details"})
MISS_KINDS = frozenset({"KEYWORD_DROP", "RANK_REJECT", "REPORT_RETURN"})
PROTECTED = (ROOT / "config", ROOT / "grit", ROOT / "specs")
DECISIONS = frozenset({"INCLUDE", "WATCH", "EXCLUDE", "DEFER"})
# Copies of the task. The stored answer keeps the decision, not the packet that was sent.
ECHO = frozenset({
    "task", "candidates", "current_selections", "register_names", "register_descriptions", "sources", "report",
    "open_issues", "reader_memory", "human_feedback", "budget", "authority", "accepted_misses", "editable_fields",
    "contract", "pack", "run",
})
ROW_FIELDS = frozenset({
    "signal_id", "decision", "reason", "source_ids", "significance", "impact_status", "exposure_status",
    "immediate_threat", "fact", "exposure", "transmission", "impact", "hops", "risk_ids", "uncertainty",
    "reason_cut", "reason_defaulted", "hop_rejected", "decision_downgraded", "source_ids_defaulted",
})
SIGNAL_MARK = re.compile(r"SIG-[0-9a-f]{16}")
INDEX_PATH = re.compile(r"\[(\d+)\]")


def as_miss(row: Mapping[str, Any]) -> dict[str, Any]:
    """A miss as the next run receives it. A rejection is not a new fact about the world."""
    signal = row.get("signal_id")
    return {"kind": "MISS", "miss_id": row["miss_id"], "miss_kind": row["kind"], "profile_id": None,
            "source_ids": [signal] if isinstance(signal, str) and signal else [],
            "text": f"Accepted miss ({row['kind']}), recorded by {row['recorded_by']}. {row['text']} "
                    "This is a review correction, not a new fact about the world."}


def propose_changes(misses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A review file of search words and notes taken from accepted misses. It does not change the repository."""
    keywords = [str(row["keyword"]).strip() for row in misses if str(row.get("keyword") or "").strip()]
    notes = [f"{row.get('kind')}: {str(row.get('text') or '')[:240]}" for row in misses]
    return {"proposed_keywords": list(dict.fromkeys(keywords)), "proposed_prompt_notes": notes, "apply": False,
            "note": "Review this file. Nothing in the repository was changed."}


def assert_proposal_path(path: Path) -> Path:
    """A proposal file stays outside the rules, the code, and the specs."""
    resolved = path.expanduser().resolve()
    for banned in PROTECTED:
        if resolved == banned or banned in resolved.parents:
            raise ValueError("A proposal cannot be written into config, grit, or specs.")
    return resolved


def strip_private(value: Any) -> Any:
    """Drop the model's private reasoning. The decision and the hops remain."""
    if isinstance(value, Mapping):
        return {str(key): strip_private(item) for key, item in value.items() if key not in PRIVATE}
    if isinstance(value, list):
        return [strip_private(item) for item in value]
    return value


def _tool_trace(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    calls = raw.get("tool_calls") or raw.get("tool_trace") or []
    if not isinstance(calls, list):
        return []
    kept = []
    for call in calls[:20]:
        if not isinstance(call, Mapping):
            continue
        ids = call.get("signal_ids") or []
        kept.append({"name": str(call.get("name") or "")[:80],
                     "signal_ids": [item for item in ids if isinstance(item, str)][:20]})
    return kept


def _decision(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    label = value.strip().upper()
    return label if label in DECISIONS else None


def _selection_list(rows: Any) -> list[Any]:
    """A map of signal id to a decision becomes the selection list the check requires."""
    if isinstance(rows, list):
        return rows
    if not isinstance(rows, Mapping):
        return []
    converted = []
    for signal_id, item in rows.items():
        if not isinstance(signal_id, str) or not signal_id.strip():
            continue
        if isinstance(item, str):
            decision = _decision(item) or item.strip().upper()
            converted.append({"signal_id": signal_id, "decision": decision, "reason": f"Meaning review: {decision}.",
                              "source_ids": [signal_id], "reason_defaulted": True})
            continue
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        row.setdefault("signal_id", signal_id)
        decision = _decision(row.get("decision"))
        if decision:
            row["decision"] = decision
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            row["reason"] = f"Meaning review: {row.get('decision') or 'EXCLUDE'}."
            row["reason_defaulted"] = True
        if not row.get("source_ids"):
            ids = row.get("signal_ids")
            row["source_ids"] = [item for item in ids if isinstance(item, str)] if isinstance(ids, list) and ids else [signal_id]
        converted.append(row)
    return converted


def _trim_rows(rows: list[Any], *, reason_limit: int, prose_limit: int | None, hop_limit: int) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        decision = _decision(row.get("decision"))
        if decision:
            row["decision"] = decision
        if not row.get("source_ids") and isinstance(row.get("signal_ids"), list):
            row["source_ids"] = [item for item in row["signal_ids"] if isinstance(item, str)]
        reason = row.get("reason")
        if isinstance(reason, str) and len(reason) > reason_limit:
            row["reason"], row["reason_cut"] = reason[:reason_limit], True
        if prose_limit is not None:
            for key in list(row):
                if key not in ROW_FIELDS:
                    row.pop(key, None)
            for field in ("fact", "exposure", "transmission", "impact", "uncertainty"):
                text = row.get(field)
                if isinstance(text, str) and len(text) > prose_limit:
                    row[field] = text[:prose_limit]
        hops = row.get("hops")
        if isinstance(hops, list):
            row["hops"] = hops[:MAX_HOPS]
            for hop in row["hops"]:
                if not isinstance(hop, dict):
                    continue
                for key in ("from", "to"):
                    text = hop.get(key)
                    if isinstance(text, str) and len(text) > hop_limit:
                        hop[key] = text[:hop_limit]


def _stored_size(value: Mapping[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _fit_trace(value: dict[str, Any]) -> None:
    """Keep the decisions when the answer is long. Private reasoning is already gone. A ranking of the whole week still has to fit."""
    if _stored_size(value) <= TRACE_BYTES:
        return
    for key in list(value):
        if key in ECHO:
            value.pop(key, None)
    value.pop("tool_trace", None)
    value["trace_cut"] = True
    rows = value.get("selections") if isinstance(value.get("selections"), list) else []
    _trim_rows(rows, reason_limit=200, prose_limit=None, hop_limit=HOP_CHARS)
    if _stored_size(value) <= TRACE_BYTES:
        return
    for limit in (160, 80, 40):
        _trim_rows(rows, reason_limit=limit, prose_limit=limit, hop_limit=min(80, limit))
        if _stored_size(value) <= TRACE_BYTES:
            return
    for row in rows:
        if isinstance(row, dict):
            row.pop("hops", None)
            row.pop("uncertainty", None)
    if _stored_size(value) <= TRACE_BYTES:
        return
    def _score(row: Mapping[str, Any]) -> dict[str, int]:
        raw = row.get("significance") if isinstance(row.get("significance"), Mapping) else {}
        out = {}
        for key, high in (("exposure", 3), ("consequence", 3), ("immediacy", 3), ("evidence", 3), ("change", 2)):
            try:
                out[key] = max(0, min(high, int(raw.get(key, 0))))
            except (TypeError, ValueError):
                out[key] = 0
        return out

    def _compact_row(row: Mapping[str, Any]) -> dict[str, Any]:
        signal = row.get("signal_id") if isinstance(row.get("signal_id"), str) else None
        refs = row.get("source_ids") if isinstance(row.get("source_ids"), list) else ([signal] if signal else [])
        # Status flags are omitted so eighty ranked rows still fit; check_selections fills defaults.
        return {
            "signal_id": signal, "decision": row.get("decision"),
            "significance": _score(row), "reason": (str(row.get("reason") or "")[:40] or "Ranked."),
            "source_ids": [item for item in refs if isinstance(item, str)][:1] or ([signal] if signal else []),
            "fact": "not established", "exposure": "not established", "transmission": "not established",
            "impact": "not established", "rank_trimmed": True,
        }

    # A full-week ranking can still overrun. Keep the decision fields, and replace chain prose with placeholders that
    # the selection contract accepts. INCLUDE and WATCH always need fact, exposure, transmission, and impact.
    value["selections"] = [_compact_row(row) for row in rows if isinstance(row, dict)]
    value["rank_compact"] = True
    if _stored_size(value) > TRACE_BYTES:
        raise ContractError("The harness output is larger than 32 KB after the trace was cut.")


def prepare_output(raw: Any) -> dict[str, Any]:
    """The JSON that may be stored: no private stream, selections as a list, and at most 32 KB."""
    if isinstance(raw, str):
        raw = _parse_object(raw)
    if not isinstance(raw, Mapping):
        raise ContractError("The harness must return a JSON object.")
    value = strip_private(dict(raw))
    trace = _tool_trace(value)
    value.pop("tool_calls", None)
    if trace:
        value["tool_trace"] = trace
    if "selections" in value and not isinstance(value.get("selections"), list):
        value["selections"] = _selection_list(value.get("selections"))
        value["selections_normalized"] = True
    rows = value.get("selections") if isinstance(value.get("selections"), list) else []
    _trim_rows(rows, reason_limit=REASON_CHARS, prose_limit=None, hop_limit=HOP_CHARS)
    _fit_trace(value)
    return value


def ground_selection_ids(raw: Mapping[str, Any], sources: Mapping[str, Any],
                         payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Replace URL citations with the matching signal IDs from this task's candidates or sources."""
    value = dict(raw)
    rows = value.get("selections")
    if not isinstance(rows, list):
        return value
    by_url: dict[str, str] = {}
    for bucket in (sources, (payload or {}).get("candidates") or {}, (payload or {}).get("sources") or {}):
        if not isinstance(bucket, Mapping):
            continue
        for signal_id, row in bucket.items():
            if not isinstance(signal_id, str) or not isinstance(row, Mapping):
                continue
            url = str(row.get("url") or "").strip().rstrip("/")
            if url:
                by_url[url.casefold()] = signal_id
                by_url[url.casefold().removeprefix("https://").removeprefix("http://")] = signal_id
    known = set(sources) | set((payload or {}).get("candidates") or {}) | set((payload or {}).get("sources") or {})
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        ids = row.get("source_ids")
        if not isinstance(ids, list):
            continue
        cleaned: list[str] = []
        for item in ids:
            if not isinstance(item, str) or not item.strip():
                changed = True
                continue
            token = item.strip()
            if token in known:
                if token not in cleaned:
                    cleaned.append(token)
                continue
            mapped = by_url.get(token.casefold().rstrip("/")) or by_url.get(
                token.casefold().rstrip("/").removeprefix("https://").removeprefix("http://"))
            if mapped and mapped not in cleaned:
                cleaned.append(mapped)
                changed = True
                continue
            if SIGNAL_MARK.fullmatch(token):
                if token not in cleaned:
                    cleaned.append(token)
                continue
            changed = True
        if not cleaned and isinstance(row.get("signal_id"), str):
            cleaned = [row["signal_id"]]
            changed = True
        row["source_ids"] = cleaned
    if changed:
        value["source_ids_grounded"] = True
    return value


def _edit_text(edit: Mapping[str, Any]) -> str:
    """The prose may arrive as text, as after, or as one sentence per cited signal."""
    sentences = edit.get("sentences")
    if isinstance(sentences, list) and sentences:
        parts = []
        for sentence in sentences:
            if isinstance(sentence, str) and sentence.strip():
                parts.append(sentence.strip())
                continue
            if not isinstance(sentence, Mapping) or not isinstance(sentence.get("text"), str) or not sentence["text"].strip():
                continue
            body = sentence["text"].strip()
            ids = sentence.get("signal_ids") if isinstance(sentence.get("signal_ids"), list) else sentence.get("source_ids")
            ids = [item for item in ids or [] if isinstance(item, str)]
            if ids and "SIG-" not in body:
                body = body.rstrip(".!?") + f" [{', '.join(ids)}]."
            elif body[-1:] not in ".!?":
                body += "."
            parts.append(body)
        if parts:
            return " ".join(parts)
    for key in ("text", "after"):
        text = edit.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _cited_ids(text: str) -> list[str]:
    found: list[str] = []
    for mark in SIGNAL_MARK.findall(text):
        if mark not in found:
            found.append(mark)
    return found


def normalize_report_edits(raw: Mapping[str, Any], editable: Mapping[str, Any] | None) -> dict[str, Any]:
    """Turn a report draft into one edit per allowed path. Repeated bottom-line prose is joined. Other paths are left out."""
    value = dict(raw)
    edits = value.get("edits")
    if not isinstance(edits, list):
        return value
    allowed = set(editable) if isinstance(editable, Mapping) else set()
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    changed = False
    for edit in edits:
        if not isinstance(edit, Mapping):
            changed = True
            continue
        path = edit.get("path")
        if isinstance(path, str):
            path = INDEX_PATH.sub(r".\1", path)
        text = _edit_text(edit)
        if not isinstance(path, str) or not text or (allowed and path not in allowed):
            changed = True
            continue
        if path in merged:
            merged[path]["text"] = merged[path]["text"].rstrip() + " " + text
            merged[path]["source_ids"] = _cited_ids(merged[path]["text"])
            changed = True
            continue
        order.append(path)
        merged[path] = {"path": path, "text": text, "source_ids": _cited_ids(text)}
        if edit.get("text") != text or edit.get("source_ids") != merged[path]["source_ids"]:
            changed = True
    value["edits"] = [merged[path] for path in order]
    if changed:
        value["edits_normalized"] = True
    return value


def ground_report_edits(raw: Mapping[str, Any], sources: Mapping[str, Any]) -> dict[str, Any]:
    """A sentence whose number is not in its cited pages is left out. The other sentences stay."""
    from grit.agents.checks import text_problems
    from grit.report.brief import CITE

    value = dict(raw)
    edits = value.get("edits")
    if not isinstance(edits, list) or not isinstance(sources, Mapping):
        return value
    kept: list[dict[str, Any]] = []
    trimmed: list[dict[str, Any]] = []
    for edit in edits:
        if not isinstance(edit, Mapping) or not isinstance(edit.get("text"), str):
            continue
        pieces: list[str] = []
        last = 0
        for mark in CITE.finditer(edit["text"]):
            piece = edit["text"][last:mark.end()].strip()
            last = mark.end()
            if piece:
                pieces.append(piece)
        tail = edit["text"][last:].strip()
        if tail:
            pieces.append(tail)
        accepted: list[str] = []
        for piece in pieces:
            refs = _cited_ids(piece)
            haystack = " ".join(f"{(sources.get(ref) or {}).get('title') or ''} {(sources.get(ref) or {}).get('text') or ''}" for ref in refs)
            problems = text_problems(CITE.sub("", piece), haystack, "report edit")
            if problems:
                trimmed.append({"path": edit.get("path"), "text": piece[:180], "problems": problems[:3]})
                continue
            accepted.append(piece)
        if not accepted:
            continue
        text = " ".join(accepted)
        kept.append({"path": edit["path"], "text": text, "source_ids": _cited_ids(text)})
    value["edits"] = kept
    if trimmed:
        value["edits_trimmed"] = trimmed[:12]
    return value


def _parse_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ContractError("The harness did not return a JSON object.") from exc
    if not isinstance(parsed, dict):
        raise ContractError("The harness did not return a JSON object.")
    return parsed


_CHAIN = ('"fact":"...","exposure":"...","transmission":"...","impact":"..."')
_SCORE = ('"significance":{"exposure":0-3,"consequence":0-3,"immediacy":0-3,"evidence":0-3,"change":0-2}')


def _report_rules(payload: Mapping[str, Any]) -> str:
    """The checks a report draft must pass, stated with this task's own IDs and roles."""
    rules = ["Every date, number, and distinctive name in a sentence must appear in the title or text of the page that "
             "sentence cites (sources[ID]). Write a date as the page writes it, or leave it out."]
    front = [str(row.get("signal_id")) for row in payload.get("front_page") or [] if isinstance(row, Mapping) and row.get("signal_id")]
    if front:
        rules.append(f"bottom_line.text cites only these front-page findings: {', '.join(front)}.")
    authority = payload.get("authority") if isinstance(payload.get("authority"), Mapping) else {}
    posture = authority.get("action_posture") if isinstance(authority.get("action_posture"), Mapping) else {}
    forum, forum_date = str(posture.get("next_forum") or "").strip(), str(posture.get("next_forum_date") or "").strip()
    if forum and forum_date:
        triggers = [str(item) for item in payload.get("red_triggers") or [] if isinstance(item, str) and item.strip()]
        rules.append(f"bottom_line.escalation makes one ask that names the meeting {forum} on {forum_date}"
                     + (f" and one RED trigger from: {'; '.join(triggers)}." if triggers else "."))
    else:
        roles = [str(item) for item in payload.get("owner_roles") or [] if isinstance(item, str) and item.strip()]
        rules.append("bottom_line.escalation makes one ask"
                     + (f" that names one of these roles exactly as written: {'; '.join(roles)}," if roles else ",")
                     + " uses the word confirm, reconfirm, or check, and gives a time with before, by, or the next cycle.")
    if payload.get("settled_conflicts"):
        rules.append("Quote only the chosen figure of each settled_conflicts row.")
    return " ".join(rules)


def _instruction(task: str, payload: Mapping[str, Any] | None = None) -> str:
    payload = payload or {}
    if task.startswith("meaning-"):
        ask = ("Decide INCLUDE, WATCH, or EXCLUDE for every candidate by its meaning for the register descriptions. "
               "A keyword miss can still be relevant. "
               'Return {"selections":[{"signal_id":"...","decision":"INCLUDE|WATCH|EXCLUDE","reason":"one sentence","source_ids":["..."]}]}. '
               "selections is a list with one object per candidate.")
    elif task.startswith("cascade-"):
        ask = ("Start from current_selections, which holds the reviewed selection for each candidate. "
               "candidates[].decision is the keyword filter's verdict, not an editorial decision. "
               'Return {"selections":[{"signal_id":"...","decision":"INCLUDE|WATCH|EXCLUDE|DEFER","reason":"...",'
               f'"source_ids":["..."],{_CHAIN},"hops":[{{"from":"...","to":"...","support":"cited|not established","source_ids":["..."]}}]}}]}}. '
               "One object per candidate. decision is never DROP or KEEP. "
               "Add hops, at most four. A cited hop needs source_ids. The last hop must name a register item. "
               "An uncited hop is not an immediate threat and is not direct exposure. "
               f"If you change an EXCLUDE to INCLUDE or WATCH, add {_SCORE}.")
    elif task.startswith("rank-"):
        ask = ("Revise these selections together for this business's week. "
               "A sector story with no path to a named entity, project, customer, or supplier is not INCLUDE. "
               'Return {"selections":[{"signal_id":"...","decision":"INCLUDE|WATCH|EXCLUDE","reason":"under 200 characters",'
               f'"source_ids":["..."],{_CHAIN}}}]}}, in order of importance, most important first. '
               "One object per candidate. The significance scores in current_selections are kept, so do not rescore. "
               "Leave out the task packet and your private reasoning.")
    else:
        ask = ('Return {"edits":[{"path":"bottom_line.text","text":"One sentence [SIG-0123456789abcdef].","source_ids":["SIG-0123456789abcdef"]},'
               '{"path":"bottom_line.escalation","text":"One sentence [SIG-0123456789abcdef].","source_ids":["SIG-0123456789abcdef"]}]}. '
               "Write each of those two paths once. Put the prose in text. Cite each sentence before its final punctuation. "
               + _report_rules(payload))
    return (f"Read task.json in this directory. Write proposal.json as one JSON object for task {task}. "
            f"{ask} Do not add score, priority, severity, RED, or approval fields. Do not write your private reasoning.")


# Fields a DeepSeek selection may leave out; the reviewed selection supplies them.
CARRIED = ("fact", "exposure", "transmission", "impact", "exposure_status", "impact_status", "risk_ids", "uncertainty",
           "immediate_threat")


def carry_over_selections(raw: Mapping[str, Any], current: Mapping[str, Any] | None, *, keep_scores: bool) -> dict[str, Any]:
    """Fill fields DeepSeek left out from the reviewed selection. keep_scores keeps the reviewed significance as well."""
    value = dict(raw)
    rows = value.get("selections")
    if not isinstance(rows, list) or not isinstance(current, Mapping):
        return value
    for row in rows:
        if not isinstance(row, dict):
            continue
        before = current.get(row.get("signal_id"))
        if not isinstance(before, Mapping):
            continue
        for key in CARRIED:
            present = row.get(key)
            if (present is None or (isinstance(present, str) and not present.strip())) and before.get(key) is not None:
                row[key] = before[key]
        score = before.get("significance")
        if isinstance(score, Mapping) and (keep_scores or not isinstance(row.get("significance"), Mapping)):
            row["significance"] = dict(score)
    return value


def deepseek_model() -> str:
    load_local_env()
    return os.environ.get("DEEPSEEK_MODEL", "").strip() or "deepseek-v4-flash"


def deepseek_harness(task: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run one DeepSeek Harness task in a temporary directory. The API key stays in the process environment."""
    load_local_env()
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise RuntimeError("DEEPSEEK_API_KEY is missing from .env.")
    try:
        from deepseek_harness import DeepSeekHarness
    except ImportError as exc:
        raise RuntimeError("Install deepseek-harness-sdk to use --reasoner deepseek.") from exc
    model = deepseek_model()
    with tempfile.TemporaryDirectory(prefix="grit-dsh-") as root:
        workspace, home = Path(root) / "workspace", Path(root) / "dsh-home"
        workspace.mkdir()
        home.mkdir()
        (workspace / "task.json").write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        with DeepSeekHarness(provider="deepseek-official", model=model, cwd=str(workspace), dsh_home=str(home),
                             reasoning_effort="high") as harness:
            result = harness.run(_instruction(task, payload), session_id="grit-" + task[:40].replace("/", "-"))
        path = workspace / "proposal.json"
        text = path.read_text(encoding="utf-8") if path.is_file() else str(getattr(result, "final_response", "") or "")
    if not text.strip():
        raise RuntimeError("DeepSeek Harness did not write proposal.json.")
    return _parse_object(text)
