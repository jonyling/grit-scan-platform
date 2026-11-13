"""The daily review clock (spec 48). `grit daily-review tick` runs from cron every 15 minutes and does whatever is due.

Each step runs once per batch, in order, and only after the step before it. A step that fails is tried again at the next
tick. A step that cannot run because a setting is missing is SKIPPED and the cycle goes on, so the packs are still written.
If the server was down at a deadline, the stage closes at the next tick, and only decisions made by the deadline count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Callable, Mapping

from grit.pool import Pool
from grit.profiles import Profile
from grit.review import sharepoint
from grit.review.argue import argue_batch
from grit.review.batch import mark_step, step_done
from grit.review.config import BP, MANAGER, ReviewConfig, batch_id
from grit.review.mail import mail_pack
from grit.review.pack import FINAL

ARGUE, PUBLISH_BP, MAIL_BP, CLOSE_BP, PUBLISH_MANAGER, MAIL_MANAGER, CLOSE_MANAGER, MAIL_FINAL = (
    "ARGUE", "PUBLISH_BP", "MAIL_BP", "CLOSE_BP", "PUBLISH_MANAGER", "MAIL_MANAGER", "CLOSE_MANAGER", "MAIL_FINAL")


@dataclass
class Services:
    """What the steps need from outside the pool. Each one may be missing; the step then records why."""
    profiles: Mapping[str, Profile]
    graph: Any | None = None
    graph_error: str | None = None
    graph_failed: bool = False  # settings are present but the token call failed: retry, do not skip
    gemini: Any | None = None
    gemini_error: str | None = None
    model: str = "gemini-2.5-flash"


def schedule(config: ReviewConfig) -> list[tuple[str, time, str | None]]:
    """(step, due time, step it waits for)."""
    return [
        (ARGUE, config.argue_at, None),
        (PUBLISH_BP, config.bp.publish_at or config.argue_at, ARGUE),
        (MAIL_BP, config.bp.mail_at, PUBLISH_BP),
        (CLOSE_BP, config.bp.close_at, PUBLISH_BP),
        (PUBLISH_MANAGER, config.bp.close_at, CLOSE_BP),
        (MAIL_MANAGER, config.manager.mail_at, PUBLISH_MANAGER),
        (CLOSE_MANAGER, config.manager.close_at, PUBLISH_MANAGER),
        (MAIL_FINAL, config.final_mail_at, CLOSE_MANAGER),
    ]


def _status(outcome: Mapping[str, Any]) -> str:
    return {"DONE": "DONE", "NOT_CONFIGURED": "SKIPPED", "UNCERTAIN": "UNCERTAIN"}.get(str(outcome.get("status")), "FAILED")


def run_step(pool: Pool, config: ReviewConfig, day: date, step: str, services: Services, now: datetime) -> dict[str, Any]:
    profiles = services.profiles
    if step == ARGUE:
        outcome = argue_batch(pool, config, day, client=services.gemini, model=services.model, client_error=services.gemini_error)
        retry = outcome.get("FAILED") and now < config.at(day, config.bp.publish_at or config.argue_at)
        return {**outcome, "status": "FAILED" if retry else "DONE", **({"error": "model calls failed; retrying before publish"} if retry else {})}
    if services.graph_failed:
        return {"status": "FAILED", "error": services.graph_error}
    if step in {PUBLISH_BP, PUBLISH_MANAGER}:
        stage = BP if step == PUBLISH_BP else MANAGER
        outcome = sharepoint.publish(pool, config, day, stage, profiles[config.stages[stage].profile_id], client=services.graph)
        return {**outcome, "reason": outcome.get("reason") or services.graph_error} if outcome["status"] == "NOT_CONFIGURED" else outcome
    if step in {CLOSE_BP, CLOSE_MANAGER}:
        return sharepoint.close(pool, config, day, BP if step == CLOSE_BP else MANAGER, client=services.graph)
    stage = {MAIL_BP: BP, MAIL_MANAGER: MANAGER, MAIL_FINAL: FINAL}[step]
    if stage != FINAL and now >= config.at(day, config.stages[stage].close_at):
        return {"status": "NOT_CONFIGURED", "reason": "the stage closed before the pack could go"}
    list_urls = {name: config.stages[name].list_url for name in (BP, MANAGER)}
    return mail_pack(pool, config, day, stage, profiles, client=services.graph, list_urls=list_urls)


def tick(pool: Pool, config: ReviewConfig, day: date, now: datetime, services: Services, *, say: Callable[[str], None] = print) -> list[tuple[str, str]]:
    """Run every step that is due and not yet done, in order. Between publish and close, read the open stage's decisions."""
    batch = batch_id(day, config.pack)
    if not config.working_day(day):
        say(f"{batch}: {day:%A} is not a working day. Its signals join the next working day's batch.")
        return []
    ran: list[tuple[str, str]] = []
    for step, due, after in schedule(config):
        if step_done(pool, batch, step):
            continue
        if now < config.at(day, due) or (after and not step_done(pool, batch, after)):
            break
        if step == ARGUE and now < config.at(day, config.bp.publish_at or due):
            run = pool.run(f"{day.isoformat()}-{config.pack}-daily")
            if not run or run.get("status") != "COMPLETE":
                say(f"{batch}: waiting for today's daily run before arguing.")
                break
        try:
            outcome = run_step(pool, config, day, step, services, now)
        except Exception as exc:  # a failed step is retried at the next tick; the error is recorded without a credential
            outcome = {"status": "FAILED", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        status = _status(outcome)
        mark_step(pool, batch, step, status, outcome)
        pool.log(actor_type="FUNCTION", actor=f"DailyReview.{step}", status=status, run_id=batch, model=services.model if step == ARGUE else None,
                 output_hash=outcome.get("approved_hash"), detail={key: value for key, value in outcome.items() if key != "files"})
        ran.append((step, status))
        say(f"{batch}: {step} {status}" + (f" ({outcome.get('reason') or outcome.get('error')})" if status != "DONE" and (outcome.get("reason") or outcome.get("error")) else ""))
        if status == "FAILED":
            break
    open_stage = BP if step_done(pool, batch, PUBLISH_BP) and not step_done(pool, batch, CLOSE_BP) else \
        MANAGER if step_done(pool, batch, PUBLISH_MANAGER) and not step_done(pool, batch, CLOSE_MANAGER) else None
    if open_stage and services.graph is not None:
        outcome = sharepoint.collect(pool, config, day, open_stage, client=services.graph)
        if outcome.get("recorded"):
            say(f"{batch}: {outcome['recorded']} new {open_stage} decision(s).")
    return ran
