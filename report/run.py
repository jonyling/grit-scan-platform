"""Draft the weekly report from a filtered run. The draft is for review and is not approved."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from grit_scan.canonical import canonical_hash
from report.findings import method_lines, render_findings
from report.writer import PROMPT_VERSION, ReportError, build_payload, model_view, render, request_draft, sanitize, validate

DEFAULT_MODEL = "gemini-2.5-flash"


def _method(signals: dict[str, Any]) -> list[str]:
    summary, config = signals["summary"], signals["config"]
    dropped = ", ".join(f"{reason} {count}" for reason, count in summary["dropped"].items()) or "none"
    return [
        f"- Search results: {summary['candidates']}. Kept: {summary['keep_bound']} linked and {summary['keep_unbound']} not linked.",
        f"- Dropped, by reason: {dropped}.",
        f"- Keep rule: pages older than {config['window_days']} days are stale. An unlinked page must rank in the top {config['per_query_top']} of a query that found it. "
        f"Pages at cosine {config['near_duplicate']} or above are one story. Market-research listings and ranked lists are dropped as not an event. "
        f"A page that could be big or direct also absorbs pages from other outlets at cosine {config['priority']['same_event']}, so one event is one finding. "
        f"The unlinked section holds at most {config['unbound_cap']} pages, one per event.",
        "- The link to the STE register is deterministic. The model wrote the overview, the LOB themes, and the summaries. It did not choose the signals, the links, or the order.",
        "- No severity, likelihood, or credibility rating is stated. Every source is unrated.",
    ]


def main(argv: list[str] | None = None, *, client: Any | None = None) -> int:
    from grit_scan.local_env import load_local_env

    load_local_env()
    parser = argparse.ArgumentParser(description="Draft the weekly report from a run folder that already holds signals.json.")
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--model", default=None, help="Defaults to GEMINI_MODEL, then " + DEFAULT_MODEL + ".")
    parser.add_argument("--retries", type=int, default=1, help="How many times to send a rejected draft back with its violations.")
    parser.add_argument("--dry-run", action="store_true", help="Build the model input and print its size. Call nothing.")
    parser.add_argument("--no-findings", action="store_true", help="Ignore findings.json. Summarise every kept signal in the first report format.")
    args = parser.parse_args(argv)
    try:
        signals = json.loads((args.run / "signals.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"No signals.json in {args.run}. Run retrieval.filter_run on this folder first.")
        return 1
    findings: dict[str, Any] | None = None
    findings_path = args.run / "findings.json"
    if findings_path.is_file() and not args.no_findings:
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        if findings.get("status") != "DRAFT_FOR_REVIEW":
            print(f"findings.json is {findings.get('status')}: the loop did not finish. Run loop.run again, or pass --no-findings.")
            return 1
        if findings.get("runDate") != signals["runDate"]:
            print(f"findings.json is for {findings.get('runDate')} and signals.json is for {signals['runDate']}. Run loop.run again.")
            return 1
    carded = {card["url"] for card in findings["findings"]} if findings else set()
    payload = build_payload(signals, carded)
    model = args.model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    sent = json.dumps(model_view(payload), ensure_ascii=False)
    print(
        f"Signals in the summary pass: {len(payload['signals'])} ({payload['counts']['linked'] - payload['counts']['in_cards']} linked without a card, {payload['counts']['not_linked']} not linked). "
        f"Model input: {len(sent)} characters. Model: {model}."
    )
    if args.dry_run:
        print("Dry run. Nothing was sent.")
        return 0
    if client is None:
        from agents.gemini import gemini_client

        client = gemini_client()
    history: list[list[str]] = []
    violations: list[str] = []
    draft: dict[str, Any] = {}
    for _attempt in range(1 + max(0, args.retries) if payload["signals"] else 0):
        try:
            draft = request_draft(client, payload, model=model, fix=violations or None)
        except ReportError as exc:
            print(exc)
            return 1
        violations = validate(draft, payload)
        history.append(violations)
        if not violations:
            break
    if not payload["signals"]:
        draft = {"overview": "", "lob_themes": [], "summaries": []}  # every kept signal has a card, so the model has nothing to summarise
    meta = {"model": model, "prompt_version": PROMPT_VERSION, "response_hash": canonical_hash(draft), "attempts": len(history)}
    withheld: list[str] = []
    if violations and not any(item.startswith("BAD_SHAPE") for item in violations):
        draft, withheld = sanitize(draft, payload)
        violations = []
    if violations:
        (args.run / "report.rejected.json").write_text(json.dumps({**meta, "violations": violations, "history": history, "draft": draft}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"The draft was rejected after {len(history)} attempt(s): {len(violations)} violation(s). First: {violations[0]}. See report.rejected.json.")
        return 1
    method = _method(signals) + (method_lines(findings, signals["config"]["priority"]) if findings else [])
    findings_lines = render_findings(findings) if findings else None
    (args.run / "report.md").write_text(render(draft, payload, meta={**meta, "method": method, "withheld": withheld}, findings_lines=findings_lines), encoding="utf-8")
    (args.run / "report.json").write_text(
        json.dumps({**meta, "status": "DRAFT_FOR_REVIEW", "validation": {"accepted": True, "withheld": withheld, "earlier_attempts": history[:-1] if not withheld else history}, "input_signal_ids": [item["signal_id"] for item in payload["signals"]], "draft": draft}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.run / 'report.md'} and report.json. Status: draft for review." + (f" Withheld by validation: {', '.join(withheld)}." if withheld else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
