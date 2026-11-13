"""The daily two-stage review (spec 48). config/review/<pack>.json names the reviewers, the lists and the clock.

A batch is one pack's working day. It covers the pack's daily runs from the day after the previous working day up to that
day, so Monday's batch holds Saturday's and Sunday's signals, and the day after a holiday holds the holiday's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from grit import settings

BP, MANAGER = "BP", "MANAGER"
STAGES = (BP, MANAGER)


def review_dir() -> Path:
    return settings.CONFIG_DIR / "review"


def _clock(value: Any, where: str) -> time:
    try:
        hours, minutes = str(value).split(":")
        return time(int(hours), int(minutes))
    except (TypeError, ValueError):
        raise ValueError(f"{where}: a time such as 09:00") from None


@dataclass(frozen=True)
class Stage:
    name: str
    profile_id: str
    display_name: str
    role: str
    list_id: str | None
    mail_at: time
    close_at: time
    publish_at: time | None = None
    list_url: str | None = None


@dataclass(frozen=True)
class ReviewConfig:
    pack: str
    title: str
    business_area: str
    zone: ZoneInfo
    stages: Mapping[str, Stage]
    argue_at: time
    final_mail_at: time
    max_signals: int
    holidays: frozenset[date]

    @property
    def bp(self) -> Stage:
        return self.stages[BP]

    @property
    def manager(self) -> Stage:
        return self.stages[MANAGER]

    def working_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def previous_working_day(self, day: date) -> date:
        earlier = day - timedelta(days=1)
        while not self.working_day(earlier):
            earlier -= timedelta(days=1)
        return earlier

    def covered_days(self, day: date) -> list[date]:
        """The calendar days whose daily runs feed this working day's batch."""
        start = self.previous_working_day(day) + timedelta(days=1)
        return [start + timedelta(days=offset) for offset in range((day - start).days + 1)]

    def at(self, day: date, clock: time) -> datetime:
        return datetime.combine(day, clock, tzinfo=self.zone)


def batch_id(day: date, pack: str) -> str:
    return f"{day.isoformat()}-{pack}-review"


def load_review_config(pack: str, directory: Path | None = None) -> ReviewConfig:
    path = (directory or review_dir()) / f"{pack}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("pack") != pack:
        raise ValueError(f"{path.name}: pack must be {pack!r}")
    stages: dict[str, Stage] = {}
    for name in STAGES:
        item = (raw.get("stages") or {}).get(name)
        if not isinstance(item, Mapping) or not item.get("profileId"):
            raise ValueError(f"{path.name}: stages.{name} needs a profileId")
        where = f"{path.name}: stages.{name}"
        stages[name] = Stage(
            name=name,
            profile_id=str(item["profileId"]),
            display_name=str(item.get("displayName") or item["profileId"]),
            role=str(item.get("role") or ""),
            list_id=(str(item["listId"]).strip() or None) if item.get("listId") else None,
            mail_at=_clock(item.get("mailAt"), f"{where}.mailAt"),
            close_at=_clock(item.get("closeAt"), f"{where}.closeAt"),
            publish_at=_clock(item["publishAt"], f"{where}.publishAt") if item.get("publishAt") else None,
            list_url=str(item.get("listUrl") or "").strip() or None,
        )
    if stages[BP].publish_at is None:
        raise ValueError(f"{path.name}: stages.BP needs publishAt")
    config = ReviewConfig(
        pack=pack,
        title=str(raw.get("title") or f"GRIT-Scan daily signal review: {pack}"),
        business_area=str(raw.get("businessArea") or pack),
        zone=ZoneInfo(str(raw.get("timezone") or "Asia/Singapore")),
        stages=stages,
        argue_at=_clock(raw.get("argueAt"), f"{path.name}: argueAt"),
        final_mail_at=_clock(raw.get("finalMailAt"), f"{path.name}: finalMailAt"),
        max_signals=int(raw.get("maxSignals") or 60),
        holidays=frozenset(date.fromisoformat(item) for item in raw.get("holidays") or []),
    )
    order = [config.argue_at, config.bp.publish_at, config.bp.mail_at, config.bp.close_at, config.manager.mail_at,
             config.manager.close_at, config.final_mail_at]
    if order != sorted(order):
        raise ValueError(f"{path.name}: the times must run argue, publish, BP mail, BP close, Manager mail, Manager close, final mail")
    return config


def configured_packs(directory: Path | None = None) -> list[str]:
    folder = directory or review_dir()
    return sorted(path.stem for path in folder.glob("*.json")) if folder.is_dir() else []
