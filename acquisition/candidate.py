"""The only object an acquisition adapter may emit."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Literal

SourceLane = Literal["APPROVED", "WILD", "SPHERA", "AGILE", "ISOS"]


@dataclass(frozen=True)
class Candidate:
    source_lane: SourceLane
    title: str
    text: str
    url: str | None
    publisher: str | None
    published_at: str | None
    fetched_at: str
    content_hash: str
    seen_at: str | None = None

    def as_record(self) -> dict[str, str | None]:
        return asdict(self)


def content_hash(*parts: str | None) -> str:
    payload = "\n".join(part or "" for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
