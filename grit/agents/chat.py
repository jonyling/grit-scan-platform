"""Chat commands, shared by the Co-Pilot and the command line.

"/concern TITLE: keyword, keyword" (also /topic, /trend, /keyword) adds a PROPOSED management request. A reviewer activates
it on the dashboard. Only an ACTIVE request assigns P1 and adds queries to the next scan. Questions go to the Co-Pilot
(grit.copilot, spec 40), which replaced the earlier single-run chat.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from grit.pool import Pool, utc_now

COMMAND = re.compile(r"^/(concern|topic|trend|keyword)\s+(.+)$", re.IGNORECASE | re.DOTALL)


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
