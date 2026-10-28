"""The Co-Pilot agent (spec 40, section 4): a bounded loop of tool rounds, then a checked answer.

1. The model sees the question, the reader's scope and profile, and the results so far. It asks for tools (at most four
   per round, four rounds) or returns its answer.
2. The checks in grit.copilot.checks decide. A failing answer is redrafted once with the violations, then withheld.
3. Rendering is deterministic: source ids become outlet, date and link badges from the pool, and access notes are added for
   internal reports and State of the Situation drafts.

The model's tool requests are JSON, not native function calls, so a stub model can drive the loop in tests and every
request is logged as data. The model never writes: commands (/concern, /note) are parsed by code.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from grit.agents.loop.llm import LoopError, call_json
from grit.copilot.checks import MARK, PARTS, answer_dates, check_answer, split_lines
from grit.copilot.tools import Toolbox
from grit.core.canonical import canonical_hash
from grit.pool import Pool, utc_now

VERSION = "copilot-v1"
MAX_ROUNDS = 4
MAX_CALLS = 4
MAX_HISTORY = 3
RETRIES = 2  # the model now and then returns broken JSON or an empty function call; a retry clears it
NOTE = re.compile(r"^/note\s+(.+)$", re.IGNORECASE | re.DOTALL)
RECENT = re.compile(r"\b(today|yesterday|this week|latest|newest|now|currently|still|since|any update|so far|recent(?:ly)?|postponed|happening)\b", re.I)
STALE_DAYS = 2

SYSTEM = """You are the GRIT-Scan Co-Pilot for ST Engineering's Risk & Assurance team. You answer one reader's question from
GRIT-Scan's records: the briefs' assessed state, the signal pool, the report library, and the business-area registers.

Work in rounds. Reply only with plain JSON text, never a function call. Each round, return one JSON object, either
  {"plan": "one line", "tool_calls": [{"tool": "get_state", "args": {"pack": "ste"}}, ...]}   (at most 4 lookups), or
  {"final": ANSWER}.

Lookups you can request, with their args:
- "get_state", args "pack" and optional "run_id": a pack's latest brief: bottom line, standing conditions or risk themes
  with trend, summary, implication and response options, and open watchpoints. Start here for "what is escalating", "why",
  "what should we do".
- "condition_history", args "item_id" and optional "since" (YYYY-MM-DD): one standing item's trend and summary at each
  run, with turning points.
- "search_signals", args "query" and optional "include_dropped": news events in the reader's packs, one per story, with
  outlets and dates. Use a short keyword query (names, acronyms, places), not the whole question.
- "search_library", args "query" and optional "topic": thought-leadership report passages and State of the Situation
  sections: history, context, structural drivers and published forecasts. Topic ids: SOTS-MIDEAST, SOTS-UKRAINE, SOTS-SEMIS, SOTS-QUANTUM,
  SOTS-AI-AUTONOMY, SOTS-SOVEREIGN-AI, SOTS-TECH-BLOCS, SOTS-HMT, SOTS-CHINA-TAIWAN, SOTS-DEFENCE-BUDGETS,
  SOTS-COMPETITORS.
- "whats_new", optional args "since" (YYYY-MM-DD; default the last 7 days) and "pack": the daily digests: new tier 1 and
  2 events, RED patterns, moved standing items and watchpoint updates, per pack and day. Start here for "what's new",
  "since Monday" or "this week".
- "get_scenarios", optional arg "shift" (a shift name or id, such as "semiconductor" or "STE-SHIFT-03"): the reviewed best,
  medium and worst case for STE under each strategic shift, where the latest signposts lean, the signposts seen with their
  signals, and the planned responses they trigger. Use it for "where is this heading", "what if", or "what should we prepare".
- "lookup_register", args "kind" (entities, customers, suppliers or competitors) and optional "name", "country", "pack".
- "web_search", arg "query": live web news, only when "web_searches_left" in the scope is above 0. When the request has
  "web_search_suggested", run web_search in your first round as well as reading the records. Use it when the records
  are silent, when the question is about something after the newest signal, or when it names an organisation the records
  do not hold. At most 2 per question. Results are unvetted: GRIT-Scan's rules have not assessed them.

Every lookup result gives each item an id: S (signal), B (brief state), R (report passage), D (State of the Situation
section), G (register row), W (web result, unvetted). You may cite only ids you have been given.

ANSWER is an object:
  "answer": one to three sentences that answer the question directly, each citing ids, e.g. "... [B2, S4]."
  "evidence": the facts behind the answer, one per line, each citing ids.
  "implications": what it means for ST Engineering, each citing ids. Use the brief's validated implication [B#] where one
     exists; any other inference is written as a possibility with could, may or might.
  "uncertain": what the evidence does not settle, and any counter-indicator. Say when the newest signal is older than the
     question needs.
  "next_step": optional: open a condition, read a State of the Situation document, or propose a management request with
     /concern TITLE: keyword, keyword.
  "nothing_found": true only when the records hold nothing on the question; then say so plainly in "answer".

Rules:
- Use only the records. Do not add facts from memory. Every number must appear in a source you cite.
- A sentence with will, forecast, projected, expected to or anticipated must begin "According to <the outlet, report or
  brief you cite>," for example "According to the World Bank, ... [R2]" or "According to the brief, ... [B3]". Otherwise
  state only what has happened.
- Never write a URL. Never use the words severity, likelihood, probability, risk score or risk rating.
- Prefer the brief's state over raw signals; use signals as the evidence behind it.
- scope.screen names the tab the reader has open and lists the records on that tab. When the question is about the screen, use that tab. Do not answer from the Dashboard when another tab is open.
- A scenario lean is a count of signposts seen. Never turn it into odds or a forecast; say which signposts were seen.
- Cite a web result [W#] only for what the records lack. A web result never overrides a brief's assessment; when they
  differ, say so under "uncertain".
- "history" holds this reader's earlier turns, for follow-up questions only; cite nothing from it.
- Plain, direct English for a senior executive. Follow the reader's profile on what they want and how they like it written.
- Internal reports may be quoted; the access note is added for you.
"""

REDRAFT = "Your answer failed its checks. Fix every violation listed and return {\"final\": ANSWER}. Do not call tools."
FORCE = "No tool rounds are left. Return {\"final\": ANSWER} from the results you have."


def _context(pool: Pool, scope: Any, web: Any | None = None) -> dict[str, Any]:
    packs = []
    for pack in scope.packs:
        run = pool.one("SELECT run_id, run_date FROM runs WHERE pack = ? AND status = 'COMPLETE' AND kind != 'DAILY' ORDER BY run_date DESC, started_at DESC LIMIT 1", (pack,))
        packs.append({"pack": pack, "label": scope.label(pack), "latest_run": run["run_id"] if run else None, "latest_run_date": run["run_date"] if run else None})
    context = {"packs": packs, "window": scope.window, "run_on_screen": scope.run_id, "focus": scope.focus, "today": scope.today.isoformat(),
               "web_searches_left": min(web.remaining(), 2) if web is not None else 0}
    if getattr(scope, "screen", None):
        context["screen"] = scope.screen
    return context


def web_trigger(question: str, newest: str | None, today: Any) -> str | None:
    """A deterministic reason to search the web: the question asks about now, and the newest signal is older than two days."""
    if not RECENT.search(question or ""):
        return None
    try:
        age = (today - datetime.fromisoformat(str(newest)[:10]).date()).days if newest else None
    except ValueError:
        age = None
    if age is None or age > STALE_DAYS:
        return f"The question asks about the present, and the newest signal in scope is from {newest or 'no date'}. Run web_search before answering."
    return None


def _profile_view(profile: Any) -> dict[str, Any]:
    reader = getattr(profile, "reader", None)
    view: dict[str, Any] = {"name": profile.name, "role": profile.role}
    if reader is not None:
        view["profile"] = reader.body[:3500]
    return view


def settings(profile: Any) -> dict[str, Any]:
    reader = getattr(profile, "reader", None)
    copilot = dict(reader.section("copilot")) if reader is not None else {}
    return {"answer_length": copilot.get("answer_length") or "standard", "show_review_status": copilot.get("show_review_status", True)}


def history(pool: Pool, profile_id: str, limit: int = MAX_HISTORY) -> list[dict[str, str]]:
    """This reader's own recent turns. Another reader's conversation is never read."""
    rows = pool.rows("SELECT role, content FROM chat_messages WHERE profile_id = ? ORDER BY message_id DESC LIMIT ?", (profile_id, limit * 2))
    return [{"role": row["role"], "content": plain(row["content"])[:700]} for row in rows[::-1]]


def plain(markdown: str) -> str:
    """An earlier answer without links, footer or emphasis, so the model reads it as context and not as a format to copy."""
    text = re.sub(r"<sub>.*?</sub>|\n---\n.*$", "", markdown or "", flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]+\)", r"\1", text)
    return re.sub(r"\s+", " ", text.replace("**", "").replace("*", "")).strip()


def _final(raw: Mapping[str, Any]) -> Any:
    return raw.get("final") if "final" in raw else (raw if "answer" in raw else None)


def ask(
    pool: Pool, scope: Any, question: str, *, client: Any, model: str, embed: Callable[[str], Sequence[float]] | None = None,
    embedder: Any | None = None, toolbox: Toolbox | None = None, past: Sequence[Mapping[str, str]] = (), max_rounds: int = MAX_ROUNDS, web: Any | None = None,
) -> dict[str, Any]:
    """Returns {status, markdown, draft, citations, steps, log}. status is VALIDATED, WITHHELD or LLM_ERROR. web is a
    grit.copilot.web.WebSearch for this reader, or None to keep the web off."""
    tools = toolbox or Toolbox(pool, scope, embed=embed, embedder=embedder, web=web)
    options = settings(scope.profile)
    base = {"question": question, "reader": _profile_view(scope.profile), "scope": _context(pool, scope, tools.web), "answer_length": options["answer_length"], "history": list(past)}
    if tools.web is not None and base["scope"]["web_searches_left"]:
        newest = tools.newest_signal()
        trigger = web_trigger(question, newest, scope.today)
        base["scope"]["newest_signal"] = newest
        if trigger:
            base["web_search_suggested"] = trigger
    results: list[dict[str, Any]] = []
    plans: list[str] = []
    draft: Any = None
    attempts: list[dict[str, Any]] = []
    calls = 0
    try:
        for round_number in range(max_rounds + 1):
            left = max_rounds - round_number
            payload = {**base, "rounds_left": left, "results": results} | ({"instruction": FORCE} if left == 0 else {})
            raw = call_json(client, model=model, system=SYSTEM, payload=payload, retries=RETRIES, pause=1.0)
            calls += 1
            draft = _final(raw)
            requested = raw.get("tool_calls") if isinstance(raw.get("tool_calls"), list) else []
            if draft is not None or not requested or left == 0:
                break
            plans.append(str(raw.get("plan") or ""))
            for request in requested[:MAX_CALLS]:
                if not isinstance(request, Mapping):
                    continue
                name, args = str(request.get("tool") or ""), request.get("args") if isinstance(request.get("args"), Mapping) else {}
                results.append({"tool": name, "args": dict(args), "result": tools.call(name, args)})
        dates = answer_dates(scope.today)
        problems = check_answer(draft, tools.sources.by_id, length=options["answer_length"], dates=dates)
        attempts.append({"violations": problems})
        if problems:
            raw = call_json(client, model=model, system=SYSTEM, payload={**base, "results": results, "draft": draft, "violations": problems, "instruction": REDRAFT}, retries=RETRIES, pause=1.0)
            calls += 1
            draft = _final(raw)
            problems = check_answer(draft, tools.sources.by_id, length=options["answer_length"], dates=dates)
            attempts.append({"violations": problems})
    except LoopError as exc:
        return _result("LLM_ERROR", f"The model call failed: {exc}", None, tools, plans, attempts, base, model, calls)
    if problems:
        text = "The answer failed its checks twice and is withheld. " + "; ".join(problems[:3])
        return _result("WITHHELD", text, draft, tools, plans, attempts, base, model, calls)
    return _result("VALIDATED", render(draft, tools.sources.by_id, show_review_status=options["show_review_status"], steps=steps(tools)), draft, tools, plans, attempts, base, model, calls)


def steps(tools: Toolbox) -> str:
    """What the Co-Pilot did, in one line, without the model's reasoning."""
    counts: dict[str, int] = {}
    for call in tools.calls:
        counts[call["tool"]] = counts.get(call["tool"], 0) + 1
    names = {"get_state": "read a brief", "condition_history": "read a condition's history", "search_signals": "searched the signals",
             "search_library": "searched the library", "lookup_register": "looked up a register", "web_search": "searched the web", "whats_new": "read the daily digests", "get_scenarios": "read the scenarios"}
    parts = [f"{names.get(tool, tool)}" + (f" ×{count}" if count > 1 else "") for tool, count in counts.items()]
    kinds: dict[str, int] = {}
    for source in tools.sources.by_id.values():
        kinds[source.kind] = kinds.get(source.kind, 0) + 1
    seen = ", ".join(f"{count} {label}" for kind, label in (("STATE", "brief items"), ("SIGNAL", "signals"), ("REPORT", "report passages"), ("SOTS", "SotS sections"), ("REGISTER", "register rows"), ("WEB", "web results")) if (count := kinds.get(kind)))
    return ("Steps: " + "; ".join(parts) + "." if parts else "Steps: no tool was used.") + (f" Read {seen}." if seen else "")


def _badge(source: Any) -> str:
    if source.kind in {"SIGNAL", "WEB"} and source.url:
        return f"[{source.badge}]({source.url})"
    return source.badge


def render(draft: Mapping[str, Any], sources: Mapping[str, Any], *, show_review_status: bool = True, steps: str = "") -> str:
    def swap(match: re.Match[str]) -> str:
        found = [sources[item.strip()] for item in match.group(1).split(",") if item.strip() in sources]
        return f"({'; '.join(dict.fromkeys(_badge(source) for source in found))})" if found else ""

    def text(value: str) -> str:
        return re.sub(r"\s+([.,;:])", r"\1", MARK.sub(swap, value)).strip()

    def lines(key: str) -> list[str]:
        return split_lines(draft.get(key)) or []

    out = [text(" ".join(lines("answer")))]
    for key, title in (("evidence", "Evidence"), ("implications", "What it means for STE"), ("uncertain", "What is uncertain")):
        if lines(key):
            out += ["", f"**{title}**", *(f"- {text(line)}" for line in lines(key))]
    if lines("next_step"):
        out += ["", f"**Next step.** {text(' '.join(lines('next_step')))}"]
    cited = [sources[item] for item in dict.fromkeys(item for key in PARTS for line in lines(key) for group in MARK.findall(line) for item in (part.strip() for part in group.split(","))) if item in sources]
    notes = list(dict.fromkeys(source.note for source in cited if source.note))
    if notes:
        out += ["", *(f"*{note}*" for note in notes)]
    footer = " ".join(filter(None, ["Co-Pilot answer from GRIT-Scan records. Not reviewed." if show_review_status else "", steps]))
    if footer:
        out += ["", "---", f"*{footer}*"]
    return "\n".join(out).strip()


def _result(status: str, markdown: str, draft: Any, tools: Toolbox, plans: list[str], attempts: list[dict[str, Any]], base: Mapping[str, Any], model: str, calls: int) -> dict[str, Any]:
    cited: list[str] = []
    if isinstance(draft, Mapping):
        for key in PARTS:
            for line in split_lines(draft.get(key)) or []:
                for group in MARK.findall(line):
                    cited += [part.strip() for part in group.split(",")]
    citations = []
    for source_id in dict.fromkeys(cited):
        source = tools.sources.get(source_id)
        if source:
            citations.append({"id": source_id, "kind": source.kind, "badge": source.badge, "url": source.url, **source.detail})
    log = {
        "actor": "CoPilot", "version": VERSION, "model": model, "status": status, "input_hash": canonical_hash(dict(base)), "output_hash": canonical_hash(draft) if draft is not None else None,
        "detail": {"scope": base["scope"], "calls": tools.calls, "plans": plans, "model_calls": calls, "attempts": attempts, "cited": [item["id"] for item in citations]},
    }
    return {"status": status, "markdown": markdown, "draft": draft, "citations": citations, "steps": steps(tools), "log": log}


def record(pool: Pool, profile: Any, question: str, reply: str, *, citations: Sequence[Mapping[str, Any]] = (), log: Mapping[str, Any] | None = None, run_id: str | None = None) -> None:
    """Save the turn under the reader's own id, and log it. Only this reader's screen reads chat_messages for this id."""
    now = utc_now()
    pool.insert("chat_messages", {"profile_id": profile.profile_id, "role": "user", "content": question, "created_at": now})
    pool.insert("chat_messages", {"profile_id": profile.profile_id, "role": "assistant", "content": reply, "citations_json": list(citations), "created_at": now})
    if log:
        pool.log(actor_type="AGENT", actor=log["actor"], version=log.get("version"), model=log.get("model"), run_id=run_id, subject=profile.profile_id,
                 input_hash=log.get("input_hash"), output_hash=log.get("output_hash"), status=log["status"], detail={**log.get("detail", {}), "question": question})


def save_note(profile: Any, text: str, *, root: Any = None, now: datetime | None = None) -> str:
    """/note TEXT: the reader's note goes to their inbox. Jony files it with grit notes add, so every note keeps one owner."""
    from pathlib import Path

    from grit.profiles.reader import readers_dir

    stamp = (now or datetime.now()).strftime("%Y-%m-%dT%H%M%S")
    folder = Path(root or readers_dir()) / profile.profile_id / "inbox"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}_copilot.md"
    path.write_text(f"# Note from {profile.label} via the Co-Pilot, {stamp[:10]}\n\n{text.strip()}\n", encoding="utf-8")
    return str(path)


def handle(pool: Pool, scope: Any, message: str, *, answer: Callable[[], dict[str, Any]], notes_root: Any = None) -> dict[str, Any]:
    """One chat message: a /concern, /topic, /trend or /keyword proposal, a /note, or a question for answer()."""
    from grit.agents.chat import add_request, parse_command

    profile = scope.profile
    command = parse_command(message)
    if command:
        request_id = add_request(pool, command, profile_id=profile.profile_id, requested_by=profile.label)
        long = [keyword for keyword in command["keywords"] if len(keyword.split()) > 3]
        warning = (f" Keywords of more than three words rarely appear word for word in a headline: {', '.join(long)}." if long else "")
        return {"status": "COMMAND", "markdown": f"Proposed {request_id}: {command['title']}. Keywords: {', '.join(command['keywords'])}. A page is kept for this request only when its "
                f"headline or lead names one of these exactly.{warning} To set the keywords yourself, write /concern TITLE: keyword, keyword. A reviewer activates it on the Management requests tab.",
                "citations": [], "log": {"actor": "ManagementRequest", "status": "PROPOSED", "detail": {"request_id": request_id}}}
    note = NOTE.match(message.strip())
    if note:
        save_note(profile, note.group(1), root=notes_root)
        return {"status": "COMMAND", "markdown": "Saved your note. Jony will file it and update your profile; the profile then shapes your briefs and answers.",
                "citations": [], "log": {"actor": "ReaderNote", "status": "SAVED", "detail": {}}}
    return answer()


def to_json(result: Mapping[str, Any]) -> str:
    return json.dumps({key: result[key] for key in ("status", "markdown", "citations", "steps") if key in result}, indent=2, ensure_ascii=False)
