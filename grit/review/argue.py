"""Gemini argues each signal before a person reviews it (spec 48). Agents propose. Rules check. A named human decides.

A Proposer writes the case that the signal matters to the business area. A Critic writes the case against and gives a view.
Every quote must appear in the page, and every number in the prose must be in the page. A draft that fails twice is
withheld, and the reviewer sees that the argument was withheld. Neither agent sets a tier, a score, RED or a decision.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Mapping

from grit.agents.checks import MIN_QUOTE, normalise, text_problems
from grit.agents.loop.llm import LoopError, call_json
from grit.pool import Pool, utc_now
from grit.review.batch import candidates
from grit.review.config import ReviewConfig, batch_id

TEXT_LIMIT = 6000
VIEWS = ("HOLDS", "WEAK", "FAILS")
PROPOSER = (
    "You prepare one news signal for a risk reviewer at ST Engineering. Write the case that this signal matters to the named "
    "business area: what happened, who it touches, and why the business area should care, in at most 90 words. Use only the "
    "page text and the assessment you are given. Do not write a URL, a score, a severity or a likelihood. "
    "Return {\"case_for\": text, \"quotes\": [two or fewer sentences copied exactly from the page that support it]}."
)
CRITIC = (
    "You are the independent Critic for one news signal that a colleague argued matters to an ST Engineering business area. "
    "Write the strongest case against including it, in at most 70 words: a weak link to the business, stale or repeated news, "
    "an unsupported claim, or an overstated effect. Then give your verdict on your colleague's case for, after your objections: "
    "HOLDS (the case for survives them), WEAK (it survives only in part) or FAILS (it does not survive). Use only the page text. "
    "Do not write a URL, a score, a severity or a likelihood. "
    "Return {\"case_against\": text, \"view\": HOLDS|WEAK|FAILS, \"quotes\": [two or fewer sentences copied exactly from the page]}."
)


def _quotes(value: Any, text: str, where: str) -> tuple[list[str], list[str]]:
    if value in (None, []):
        return [], []
    if not isinstance(value, list):
        return [], [f"{where}.quotes: a list of strings."]
    good, problems = [], []
    page = normalise(text)
    for quote in value[:2]:
        if isinstance(quote, str) and len(quote.strip()) >= MIN_QUOTE and normalise(quote) in page:
            good.append(quote.strip())
        else:
            problems.append(f"NOT_VERBATIM: {where}: a quote is not copied exactly from the page.")
    return good, problems


def check_case(raw: Mapping[str, Any], key: str, text: str, where: str) -> tuple[str, list[str], list[str]]:
    """(prose, quotes, problems). Prose needs to be non-empty and grounded; quotes must be verbatim."""
    prose = raw.get(key)
    if not isinstance(prose, str) or not prose.strip():
        return "", [], [f"MISSING: {where}.{key} is empty."]
    quotes, problems = _quotes(raw.get("quotes"), text, where)
    return prose.strip(), quotes, problems + text_problems(prose, text, where)


def condition_names(pack: str) -> dict[str, str]:
    try:
        from grit.ontology.pack import load_ontology_pack
        from grit.pipeline.reader_scan import standing_items
        from grit.settings import CONFIG_DIR

        return {item["id"]: item["name"] for item in standing_items(load_ontology_pack(CONFIG_DIR, pack))}
    except (OSError, ValueError, KeyError):
        return {}


def task(record: Mapping[str, Any], business_area: str, names: Mapping[str, str], today: date | None = None) -> dict[str, Any]:
    return {
        "today": (today or date.today()).isoformat(),
        "business_area": business_area,
        "signal": {
            "title": record.get("title"),
            "publisher": record.get("publisher"),
            "published_at": record.get("published_at"),
            "page_text": str(record.get("text") or "")[:TEXT_LIMIT],
        },
        "assessment": {
            "priority_tier": record.get("tier"),
            "red": bool(record.get("red")),
            "risk_level": record.get("risk_level"),
            "trend": record.get("trend"),
            "evidence_confidence": record.get("evidence_confidence"),
            "relevance_basis": record.get("relevance_basis"),
            "conditions": [names.get(item, item) for item in record.get("condition_ids") or []],
            "themes": [hit.get("name") for hit in record.get("risk_cascade") or [] if hit.get("name")],
        },
    }


def _ask(client: Any, model: str, system: str, payload: dict[str, Any], key: str, text: str, where: str
         ) -> tuple[dict[str, Any] | None, str, list[str], list[str]]:
    """One call, and one retry that names the problems. Returns (raw, prose, quotes, problems)."""
    problems: list[str] = []
    for _attempt in range(2):
        try:
            raw = call_json(client, model=model, system=system, payload=payload, retries=1, pause=2.0, thinking="LOW")
        except LoopError as exc:
            return None, "", [], [f"LLM_ERROR: {exc}"]
        prose, quotes, problems = check_case(raw, key, text, where)
        if where == "critic" and raw.get("view") not in VIEWS:
            problems.append("BAD_VIEW: critic.view must be HOLDS, WEAK or FAILS.")
        if not problems:
            return raw, prose, quotes, []
        payload = {**payload, "violations": problems}
    return None, "", [], problems


def argue(client: Any, model: str, record: Mapping[str, Any], business_area: str, names: Mapping[str, str], today: date | None = None) -> dict[str, Any]:
    text = str(record.get("title") or "") + "\n" + str(record.get("text") or "")[:TEXT_LIMIT]
    payload = task(record, business_area, names, today)
    _raw, case_for, quotes_for, problems = _ask(client, model, PROPOSER, payload, "case_for", text, "proposer")
    if problems:
        return {"status": "FAILED" if problems[0].startswith("LLM_ERROR") else "WITHHELD", "problems": problems}
    raw, case_against, quotes_against, problems = _ask(client, model, CRITIC, {**payload, "case_for": case_for}, "case_against", text, "critic")
    if problems or raw is None:
        return {"status": "FAILED" if problems and problems[0].startswith("LLM_ERROR") else "WITHHELD", "case_for": case_for,
                "quotes": quotes_for, "problems": problems}
    return {"status": "ARGUED", "case_for": case_for, "case_against": case_against, "critic_view": raw["view"],
            "quotes": list(dict.fromkeys(quotes_for + quotes_against)), "problems": []}


def argue_batch(pool: Pool, config: ReviewConfig, day: date, *, client: Any | None, model: str,
                client_error: str | None = None, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Argue every signal in the day's batch that is not yet argued. With no client, each signal is recorded as
    NOT_CONFIGURED and still goes to the reviewer."""
    batch = batch_id(day, config.pack)
    published, overflow = candidates(pool, config, day)
    done = {row["signal_id"] for row in pool.rows("SELECT signal_id FROM review_arguments WHERE batch_id = ? AND status = 'ARGUED'", (batch,))}
    names = condition_names(config.pack)
    counts: dict[str, int] = {}
    for record in published:
        if record["signal_id"] in done:
            counts["ALREADY_ARGUED"] = counts.get("ALREADY_ARGUED", 0) + 1
            continue
        if client is None:
            result: dict[str, Any] = {"status": "NOT_CONFIGURED", "problems": [client_error or "Gemini is not configured."]}
        else:
            result = argue(client, model, record, config.business_area, names, day)
        pool.upsert("review_arguments", {
            "batch_id": batch, "signal_id": record["signal_id"], "run_id": record["run_id"], "status": result["status"],
            "case_for": result.get("case_for"), "case_against": result.get("case_against"), "critic_view": result.get("critic_view"),
            "quotes_json": result.get("quotes") or [], "problems_json": result.get("problems") or [],
            "model": model if client is not None else None, "argued_at": utc_now(),
        }, ["batch_id", "signal_id"])
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        if progress:
            progress(f"{record['signal_id']}: {result['status']}")
    return {"batch": batch, "signals": len(published), "overflow": [row["signal_id"] for row in overflow], **counts}


def summary_line(outcome: Mapping[str, Any]) -> str:
    parts = [f"{key.lower().replace('_', ' ')} {value}" for key, value in outcome.items() if key not in {"batch", "signals", "overflow"} and value]
    extra = f"; {len(outcome.get('overflow') or [])} over the cap, in the workbook only" if outcome.get("overflow") else ""
    return f"{outcome['batch']}: {outcome['signals']} signal(s)" + (": " + ", ".join(parts) if parts else "") + extra + "."


def withheld_text(argument: Mapping[str, Any] | None) -> tuple[str, str, str]:
    """What a reviewer sees in place of a missing argument."""
    if not argument:
        return "Argument not available.", "", ""
    status = argument.get("status")
    if status == "ARGUED":
        return str(argument.get("case_for") or ""), str(argument.get("case_against") or ""), str(argument.get("critic_view") or "")
    if status == "NOT_CONFIGURED":
        return "Argument not available: Gemini is not configured.", "", ""
    if status == "WITHHELD":
        return (str(argument.get("case_for") or "") or "Argument withheld: the draft failed the source checks."), "Critic's case withheld: it failed the source checks.", ""
    return "Argument not available: the model call failed.", "", ""

