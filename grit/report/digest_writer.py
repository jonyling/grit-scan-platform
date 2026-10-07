"""Build the model input, validate the model's draft, and render the document.

The model proposes an overview, one theme per LOB, and one summary per signal. It does not choose the
signals, the order, the links, the dates, or the reason a signal is linked. A draft that cites an unknown
signal, skips one, adds a number the source text lacks, includes a URL, or rates severity or likelihood is rejected.
"""

from __future__ import annotations

import json
import re
from typing import Any

from grit.core.verify_text import has_number, numbers_in

PROMPT_VERSION = "p6-report-3"
MAX_SNIPPET_CHARS = 2000
MAX_SUMMARY_WORDS = 60
ENTITY_LINKED = "Linked by name"
THEME_LEVEL = "Theme-level"
UNLINKED = "No STE link established"
WITHHELD_SUMMARY = "Summary withheld: the draft did not pass validation. Read the source."
_BANNED = re.compile(r"\b(severity|likelihood|probabilit(?:y|ies)|risk score|risk rating|high negative impact)\b", re.I)
_SIGNAL_ID = re.compile(r"\bS\d{2,}\b")  # a cited signal id is not a figure

SYSTEM_PROMPT = (
    "You draft a weekly signal digest for the management of ST Engineering Urban Solutions (URS). "
    "You are given a JSON object of signals. Each signal has a signal_id, a title, a publisher, a date, source snippets (text), "
    "and either a link_basis (it is linked to the STE register) or nearest_topics (no STE link is established). "
    "Write only from the supplied fields. Rules: "
    "1. Every statement about an event must come from that signal's title or text. Do not add a number, date, name, place, cause, or consequence that is not in it. When the text does not say, write 'not stated'. "
    "2. Do not rate severity, likelihood, probability, or risk, and do not call anything high or low risk. "
    "3. Do not describe why a signal is linked. That line is added separately. Never claim that URS is, or will be, affected. "
    "4. For a signal without a link, never say it affects URS. "
    "5. Do not merge different events. Do not include URLs. "
    "6. Each summary is one or two sentences, at most 40 words. "
    "Return one JSON object: {\"overview\": string (at most 80 words, topics only, no numbers), "
    "\"lob_themes\": [{\"lob\": string, \"theme\": string (at most 40 words), \"signal_ids\": [string]}], "
    "\"summaries\": [{\"signal_id\": string, \"summary\": string}]}. "
    "Include exactly one summary per signal. A lob_themes entry uses a lob label that appears in the signals and cites only signals with that label. "
    "You may write one theme for the label 'No STE link established'. "
    "If the input has a 'fix' list, your previous draft was rejected for those reasons. Return a corrected, complete JSON object."
)


class ReportError(RuntimeError):
    """The model call failed or returned something that is not JSON. The message never includes a credential."""


def build_payload(signals: dict[str, Any], skip_urls: frozenset[str] | set[str] = frozenset()) -> dict[str, Any]:
    """The only thing the model sees: the kept signals, in ranked order, with stable ids. A signal with a finding card is skipped."""
    ordered = [("bound", item) for item in signals["bound"] if item["url"] not in skip_urls] + [("unbound", item) for item in signals["unbound"]]
    width = max(2, len(str(len(ordered))))
    items = []
    for number, (group, signal) in enumerate(ordered, start=1):
        outlets = list(dict.fromkeys([signal["publisher"], *(other["publisher"] for other in signal["also_reported_by"])]))
        item: dict[str, Any] = {
            "signal_id": f"S{number:0{width}d}",
            "group": group,
            "lob": (signal["canonical_lob"] or (THEME_LEVEL if signal["link_level"] == "THEME" else ENTITY_LINKED)) if group == "bound" else UNLINKED,
            "title": signal["title"],
            "url": signal["url"],
            "publisher": signal["publisher"],
            "published_at": signal["published_at"],
            "text": (signal["text"] or "")[:MAX_SNIPPET_CHARS],
            "outlets": [outlet for outlet in outlets if outlet],
        }
        if group == "bound":
            item["link_basis"] = {
                "level": signal["link_level"],
                "lob": signal["canonical_lob"],
                "register_names": signal["entity_names"],
                "match_types": signal["match_types"],
                "evidence": signal["evidence_spans"],
            }
        else:
            item["nearest_topics"] = [_topic(anchor) for anchor in signal["best_anchors"][:2]]
        items.append(item)
    return {
        "run_date": signals["runDate"],
        "counts": {"candidates": signals["summary"]["candidates"], "linked": len(signals["bound"]), "not_linked": len(signals["unbound"]), "in_cards": len(skip_urls)},
        "signals": items,
    }


def _topic(anchor: dict[str, Any]) -> str:
    """The short label of an anchor. A management focus anchor carries an explanation after its title."""
    text = anchor["anchor"]
    if anchor.get("kind") == "MANAGEMENT_FOCUS":
        return text.split(". ")[0].rstrip(".")
    return text.split(": ")[0] if anchor.get("kind") == "LOB_SCOPE" else text


def model_view(payload: dict[str, Any]) -> dict[str, Any]:
    """What the model is sent. It never sees a URL, so it cannot copy or change one."""
    return {**payload, "signals": [{key: value for key, value in item.items() if key != "url"} for item in payload["signals"]]}


def request_draft(client: Any, payload: dict[str, Any], *, model: str, fix: list[str] | None = None) -> dict[str, Any]:
    try:
        response = client.models.generate_content(
            model=model,
            contents=json.dumps({**model_view(payload), **({"fix": fix} if fix else {})}, ensure_ascii=False),
            config={"system_instruction": SYSTEM_PROMPT, "response_mime_type": "application/json", "temperature": 0.1},
        )
        text = (response.text or "").strip()
    except Exception as exc:
        raise ReportError(f"Report request failed: {str(exc).split('{', 1)[0].strip()[:160] or type(exc).__name__}") from None
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        text = "\n".join(lines[:-1] if lines and lines[-1].strip() == "```" else lines).strip()
    try:
        draft = json.loads(text)
    except json.JSONDecodeError:
        raise ReportError("The model did not return JSON") from None
    if not isinstance(draft, dict):
        raise ReportError("The model did not return a JSON object")
    return draft


def _problems(text: str, haystack: str, where: str, allowed_counts: set[str] | None = None) -> list[str]:
    """Violations in one piece of model text. A figure must appear in the source text, or be a count given to the model."""
    found: list[str] = []
    banned = _BANNED.search(text)
    if banned:
        found.append(f"BANNED_TERM: {where}: {banned.group(0).lower()}")
    if "http" in text.lower():
        found.append(f"URL_IN_TEXT: {where}")
    for token in numbers_in(_SIGNAL_ID.sub("", text)):
        if not has_number(token, haystack) and token not in (allowed_counts or set()):
            found.append(f"UNGROUNDED_NUMBER: {where}: {token}")
    return found


def _sound_theme(theme: Any) -> bool:
    """A theme the renderer can read: a LOB label, a text, and a list of signal ids that are strings."""
    return (
        isinstance(theme, dict)
        and isinstance(theme.get("lob"), str)
        and isinstance(theme.get("theme"), str)
        and isinstance(theme.get("signal_ids"), list)
        and all(isinstance(sid, str) for sid in theme["signal_ids"])
    )


def validate(draft: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """Return the violations. An empty list means the draft may be rendered."""
    violations: list[str] = []
    if not isinstance(draft.get("overview"), str) or not isinstance(draft.get("lob_themes"), list) or not isinstance(draft.get("summaries"), list):
        return ["BAD_SHAPE: overview, lob_themes, and summaries are required"]
    signals = {item["signal_id"]: item for item in payload["signals"]}
    label_of = {sid: item["lob"] for sid, item in signals.items()}
    every_text = " ".join(f"{item['title']} {item['text']}" for item in signals.values())
    allowed_counts = {str(value) for value in payload["counts"].values()}

    violations.extend(_problems(draft["overview"], every_text, "overview", allowed_counts))
    seen: set[str] = set()
    for entry in draft["summaries"]:
        sid, text = (entry.get("signal_id"), entry.get("summary")) if isinstance(entry, dict) else (None, None)
        if not isinstance(sid, str) or sid not in signals:
            violations.append(f"UNKNOWN_SIGNAL: {str(sid)[:30]}")
        elif sid in seen:
            violations.append(f"DUPLICATE_SUMMARY: {sid}")
        elif not isinstance(text, str) or not text.strip():
            violations.append(f"EMPTY_SUMMARY: {sid}")
        else:
            seen.add(sid)
            if len(text.split()) > MAX_SUMMARY_WORDS:
                violations.append(f"SUMMARY_TOO_LONG: {sid}")
            violations.extend(_problems(text, f"{signals[sid]['title']} {signals[sid]['text']}", sid))
    violations.extend(f"MISSING_SUMMARY: {sid}" for sid in signals if sid not in seen)
    for theme in draft["lob_themes"]:
        if not _sound_theme(theme):
            violations.append("BAD_THEME_SHAPE")
            continue
        violations.extend(_problems(theme["theme"], every_text, f"theme {theme.get('lob')}"))
        for sid in theme["signal_ids"]:
            if sid not in signals:
                violations.append(f"UNKNOWN_SIGNAL: {sid}")
            elif label_of[sid] != theme.get("lob"):
                violations.append(f"THEME_LOB_MISMATCH: {theme.get('lob')}: {sid}")
    return violations


def sanitize(draft: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Remove the parts of a draft that fail validation. Return the clean draft and what was withheld."""
    signals = {item["signal_id"]: item for item in payload["signals"]}
    every_text = " ".join(f"{item['title']} {item['text']}" for item in signals.values())
    allowed_counts = {str(value) for value in payload["counts"].values()}
    withheld: list[str] = []
    clean: dict[str, Any] = {"overview": draft["overview"], "lob_themes": [], "summaries": []}
    if _problems(draft["overview"], every_text, "overview", allowed_counts):
        clean["overview"] = ""
        withheld.append("overview")
    kept: set[str] = set()
    for entry in draft["summaries"]:
        sid, text = (entry.get("signal_id"), entry.get("summary")) if isinstance(entry, dict) else (None, None)
        if not isinstance(sid, str) or sid not in signals or sid in kept:
            continue
        item = signals[sid]
        if isinstance(text, str) and text.strip() and len(text.split()) <= MAX_SUMMARY_WORDS and not _problems(text, f"{item['title']} {item['text']}", sid):
            kept.add(sid)
            clean["summaries"].append(entry)
    withheld.extend(sid for sid in signals if sid not in kept)
    label_of = {sid: item["lob"] for sid, item in signals.items()}
    for theme in draft["lob_themes"]:
        sound = _sound_theme(theme)
        if sound and not _problems(theme["theme"], every_text, "theme") and all(sid in signals and label_of[sid] == theme.get("lob") for sid in theme["signal_ids"]):
            clean["lob_themes"].append(theme)
        else:
            withheld.append(f"theme: {theme.get('lob') if isinstance(theme, dict) else '?'}")
    return clean, withheld


def _link_line(item: dict[str, Any]) -> str:
    basis = item["link_basis"]
    names = ", ".join(name for name, kind in zip(basis["register_names"], basis["match_types"]) if kind.startswith("REGISTER_"))
    evidence = ", ".join(f'"{text}"' for text in basis["evidence"])
    if basis["level"] == "ENTITY":
        return f"Linked to the STE register by name: {names or evidence}."
    if basis["level"] == "THEME":
        return "Big event with no register link: it hits a KEY STE theme and meets the scale cues. No entity or line of business is named."
    return f"Linked to the {basis['lob']} line of business by keyword {evidence}."


def render(draft: dict[str, Any], payload: dict[str, Any], *, meta: dict[str, Any], findings_lines: list[str] | None = None) -> str:
    summaries = {entry["signal_id"]: entry["summary"].strip() for entry in draft["summaries"]}
    withheld = meta.get("withheld") or []
    themes = {theme["lob"]: theme["theme"].strip() for theme in draft["lob_themes"]}
    counts = payload["counts"]
    in_cards = counts.get("in_cards", 0)
    lines = [
        f"# Weekly signal digest: URS, run {payload['run_date']}",
        "",
        f"**Status:** Draft for review. Not approved. Drafted by {meta['model']} (prompt {meta['prompt_version']}).",
        "",
        f"This run kept {counts['linked']} signals linked to the STE register and {counts['not_linked']} with no link, from {counts['candidates']} search results."
        + (f" {in_cards} of the linked signals have a finding card." if in_cards else ""),
        "",
    ]
    if findings_lines:
        lines += [*findings_lines]
    lines += [
        "## Overview of the other signals" if findings_lines else "## Overview",
        "",
        draft["overview"].strip() or "No overview passed validation.",
        "",
        "## Other linked signals" if findings_lines else "## Signals linked to the STE register",
    ]
    for label in dict.fromkeys(item["lob"] for item in payload["signals"] if item["group"] == "bound"):
        lines += ["", f"### {label}", ""]
        if label in themes:
            lines += [themes[label], ""]
        lines += _items([item for item in payload["signals"] if item["group"] == "bound" and item["lob"] == label], summaries)
    lines += [
        "",
        f"## {UNLINKED}",
        "",
        "These pages match an ontology topic. No register entity, customer, supplier, or line of business is named in them, so they are not GRIT signals.",
        "",
    ]
    if UNLINKED in themes:
        lines += [themes[UNLINKED], ""]
    lines += _items([item for item in payload["signals"] if item["group"] == "unbound"], summaries)
    method = list(meta["method"])
    if withheld:
        method.append(f"- Withheld by validation: {', '.join(withheld)}. The draft was kept without these parts.")
    lines += ["", "## How this was produced", "", *method, ""]
    return "\n".join(lines)


def _items(items: list[dict[str, Any]], summaries: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for item in items:
        outlets = [outlet for outlet in item["outlets"] if outlet != item["publisher"]]
        lines += [
            f"- **{item['title']}** ({item['publisher']}, {(item['published_at'] or '')[:10] or 'date not stated'}) [{item['signal_id']}]",
            f"  {summaries.get(item['signal_id']) or WITHHELD_SUMMARY}",
            f"  {_link_line(item) if item['group'] == 'bound' else 'Nearest ontology topic: ' + '; '.join(item['nearest_topics']) + '.'}",
        ]
        if outlets:
            lines.append(f"  Also carried by: {', '.join(outlets)}.")
        lines.append(f"  Source: {item['url']}")
    return lines
