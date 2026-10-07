"""The chat under the dashboard. It answers from pool records, and it takes management concerns, topics, trends and keywords.

Answers: the question is matched to the run's kept signals by embedding similarity when vectors exist, otherwise by word
overlap. The model sees at most twelve records by id (S1, S2, ...) with their deterministic labels, and never a URL. The
answer must cite only those ids, add no URL, and use no governed score word. A failing answer is withheld, not shown.

Commands: "/concern TITLE: keyword, keyword" (also /topic, /trend, /keyword) adds a PROPOSED management request. A reviewer
activates it on the dashboard. Only an ACTIVE request assigns P1 and adds queries to the next scan.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

from grit.agents.checks import BANNED, marked_ids
from grit.agents.loop.llm import LoopError, call_json
from grit.agents.reader_prompts import CHAT_SYSTEM, CHAT_VERSION
from grit.core.canonical import canonical_hash
from grit.pool import Pool, utc_now

MAX_RECORDS = 12
COMMAND = re.compile(r"^/(concern|topic|trend|keyword)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
_STOP = frozenset("the and for with what this that are was were has have any about which from into does how why who when week weekly there their they them our ste".split())


def parse_command(text: str) -> dict[str, Any] | None:
    """'/concern Taiwan blockade: blockade, quarantine' -> a request draft. Keywords default to the title."""
    match = COMMAND.match(text.strip())
    if not match:
        return None
    kind = match.group(1).upper()
    body = match.group(2).strip()
    title, _, rest = body.partition(":")
    keywords = [item.strip() for item in (rest or title).split(",") if item.strip()]
    return {"kind": kind, "title": title.strip(), "keywords": keywords}


def add_request(pool: Pool, draft: Mapping[str, Any], *, profile_id: str, requested_by: str) -> str:
    request_id = f"MR-{utc_now()[:10]}-{len(pool.management_requests()) + 1:03d}"
    pool.insert("management_requests", {
        "request_id": request_id, "title": draft["title"], "description": None, "requested_by": requested_by, "request_date": utc_now()[:10], "kind": draft["kind"],
        "keywords_json": list(draft["keywords"]), "status": "PROPOSED", "origin": "CHAT", "profile_id": profile_id,
    })
    return request_id


def _words(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(text or "") if word.casefold() not in _STOP}


def retrieve(question: str, rows: Sequence[Mapping[str, Any]], *, vectors: Mapping[str, Sequence[float]] | None = None, query_vector: Sequence[float] | None = None) -> list[Mapping[str, Any]]:
    """The most relevant shown rows. Cosine when vectors exist, else word overlap. Ties keep briefing order."""
    if query_vector is not None and vectors:
        scored = [(sum(a * b for a, b in zip(query_vector, vectors[row["signal_id"]])), number, row) for number, row in enumerate(rows) if row["signal_id"] in vectors]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [row for _score, _n, row in scored[:MAX_RECORDS]]
    asked = _words(question)
    scored = [(len(asked & _words(f"{row.get('title')} {row.get('text')}")), number, row) for number, row in enumerate(rows)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [row for score, _n, row in scored[:MAX_RECORDS] if score > 0] or list(rows[:MAX_RECORDS])


def _view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": row.get("title"), "outlet": row.get("publisher"), "published": (row.get("published_at") or "")[:10] or None, "text": str(row.get("text") or "")[:900],
        "link_rung": row.get("rung"), "tier": row.get("tier"), "news_priority": (row.get("route") or {}).get("priority"),
        "standing_conditions": row.get("condition_ids") or [], "named_items": [hit["item_name"] for hit in row.get("exposure") or [] if hit.get("strong")],
        "risk_register": [f"{hit['level']} {hit['name']}" for hit in (row.get("risk_cascade") or [])[:3]], "harm_verdict": row.get("harm_verdict") or "not established",
        "red": bool(row.get("red")), "this_reader_decided": (row.get("memory") or {}).get("reasons") or [],
    }


def check_answer(answer: Any, ids: set[str]) -> list[str]:
    if not isinstance(answer, Mapping) or not isinstance(answer.get("answer"), str) or not answer["answer"].strip():
        return ["BAD_SHAPE: no answer text"]
    text = answer["answer"]
    problems = [f"UNKNOWN_SOURCE: {item}" for item in marked_ids(text) if item not in ids]
    if BANNED.search(text):
        problems.append(f"BANNED_TERM: {BANNED.search(text).group(0)}")
    if "http" in text.lower() or "www." in text.lower():
        problems.append("URL_IN_TEXT")
    return problems


def answer_question(
    question: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    client: Any,
    model: str,
    profile_label: str,
    vectors: Mapping[str, Sequence[float]] | None = None,
    embed: Callable[[str], Sequence[float]] | None = None,
) -> dict[str, Any]:
    """Returns {status, answer, citations: [{signal_id, title, outlet, url}], log}."""
    query_vector = None
    if embed is not None and vectors:
        try:
            query_vector = embed(question)
        except Exception:
            query_vector = None
    chosen = retrieve(question, rows, vectors=vectors, query_vector=query_vector)
    table = {f"S{number}": _view(row) for number, row in enumerate(chosen, start=1)}
    back = {f"S{number}": row for number, row in enumerate(chosen, start=1)}
    payload = {"question": question, "reader": profile_label, "records": table}
    log = {"actor": "Chat", "version": CHAT_VERSION, "model": model, "input_hash": canonical_hash(payload)}
    if not chosen:
        return {"status": "NO_RECORDS", "answer": "The pool has no kept signal for this run yet.", "citations": [], "log": log | {"status": "NO_RECORDS"}}
    try:
        raw = call_json(client, model=model, system=CHAT_SYSTEM, payload=payload, retries=1)
    except LoopError as exc:
        return {"status": "LLM_ERROR", "answer": f"The model call failed: {exc}", "citations": [], "log": log | {"status": "LLM_ERROR"}}
    problems = check_answer(raw, set(table))
    log |= {"output_hash": canonical_hash(raw), "status": "VALIDATED" if not problems else "WITHHELD", "detail": {"violations": problems}}
    if problems:
        return {"status": "WITHHELD", "answer": "The answer failed its checks and is withheld: " + "; ".join(problems[:3]), "citations": [], "log": log}
    text = raw["answer"].strip()
    cited = [key for key in dict.fromkeys(marked_ids(text)) if key in back]
    citations = [{"ref": key, "signal_id": back[key]["signal_id"], "title": back[key].get("title"), "outlet": back[key].get("publisher"), "url": back[key].get("url")} for key in cited]
    return {"status": "VALIDATED", "answer": text, "citations": citations, "log": log}
