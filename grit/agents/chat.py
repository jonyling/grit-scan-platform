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


_TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+#-]*[A-Za-z0-9+#]|[A-Za-z0-9]")
_OPENERS = frozenset("bugs bug attacks attack issues issue concerns concern news update updates latest new major big huge possible potential "
                     "risks risk threat threats looks seems what why how when watch track".split())


def title_terms(title: str) -> list[str]:
    """The distinctive terms of a title, each one the keep rule can match: acronyms of three or more capitals (PHP), names
    with a digit, dot or inner capital (Node.js, GitLab, CVE-2026-109618), and capitalized words (a first word too, unless
    it is a common opener such as 'Bugs'). 'Bugs in PHP, Node.js and GitLab.' gives PHP, Node.js, GitLab."""
    from grit.core.routing import _usable

    found: list[str] = []
    for number, token in enumerate(_TERM.findall(title or "")):
        token = token.rstrip(".")
        if token.isdigit():
            continue
        capitalised = token[:1].isupper() and len(token) >= 4 and (number > 0 or token.casefold() not in _OPENERS)
        distinctive = token.isupper() or any(char.isdigit() for char in token) or "." in token or any(char.isupper() for char in token[1:]) or capitalised
        if distinctive and _usable(token) and token not in found:
            found.append(token)
    return found


def parse_command(text: str) -> dict[str, Any] | None:
    """'/concern Taiwan blockade: blockade, quarantine' -> a request draft with those keywords.

    Without a colon, or with nothing after it, the keywords are the title's distinctive terms, else the title itself.
    A page is kept only when its title or lead names a keyword exactly. The distinctive terms come first because a
    sentence fragment such as 'Bugs in PHP' would never match a headline."""
    match = COMMAND.match(text.strip())
    if not match:
        return None
    kind = match.group(1).upper()
    title, _colon, rest = match.group(2).strip().partition(":")
    title = title.strip().rstrip(".").strip()
    if not title:
        return None
    keywords = [item.strip().strip(".;") for item in rest.split(",") if item.strip().strip(".;")]
    return {"kind": kind, "title": title, "keywords": keywords or title_terms(title) or [title]}


def add_request(pool: Pool, draft: Mapping[str, Any], *, profile_id: str, requested_by: str) -> str:
    request_id = f"MR-{utc_now()[:10]}-{len(pool.management_requests()) + 1:03d}"
    pool.insert("management_requests", {
        "request_id": request_id, "title": draft["title"], "description": None, "requested_by": requested_by, "request_date": utc_now()[:10], "kind": draft["kind"],
        "keywords_json": list(draft["keywords"]), "status": "PROPOSED", "origin": "CHAT", "profile_id": profile_id,
    })
    return request_id
