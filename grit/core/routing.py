"""ApertureRouting() and NewsPriority (specs 24 and 29). Pure: no I/O, no model.

Priority is reader-scoped. It is assigned here and never written to a shared signal.

  P1  an ACTIVE management request in the reader's scope matches the signal.
  P2  a governed exposure path reaches the reader's business: an STE name, a registered supplier, a perimeter link, or a
      strong mention of a named project, supplier or site in the reader pack. A passing mention sets no priority.
  P3  a competitor, sector, or theme-level match with no direct path.
  P4  a registered customer with no direct path.
The lowest number wins, and every condition that matched is recorded.

A WIDE_APERTURE reader keeps every kept signal. A MATERIAL_APERTURE reader needs P1, P2, or P4, or a standing-condition
match in the reader pack. A suppressed signal is not deleted: the reason is returned.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

FUNCTION_VERSION = "aperture-routing-1"
MIN_KEYWORD = 6
LEAD_CHARS = 600


@dataclass(frozen=True)
class Route:
    include: bool
    priority: str | None
    matched: tuple[str, ...]
    request_id: str | None
    suppression_reason: str | None
    aperture: str

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


def _usable(keyword: str) -> bool:
    return len(keyword) >= MIN_KEYWORD or " " in keyword.strip()


def _hit(keyword: str, text: str) -> bool:
    body = r"\s+".join(re.escape(part) for part in keyword.split())
    return re.search(rf"(?<![\w]){body}(?![\w])", text, re.IGNORECASE) is not None


def match_request(record: Mapping[str, Any], requests: Iterable[Mapping[str, Any]], packs: Sequence[str]) -> tuple[str, str] | None:
    """The first active request whose keyword appears in the title or lead. Returns (request_id, keyword)."""
    title = str(record.get("title") or "")
    text = f"{title}\n{str(record.get('text') or '')[:LEAD_CHARS]}"
    for request in requests:
        if request.get("status") != "ACTIVE":
            continue
        scope = request.get("pack")
        if scope and scope not in packs:
            continue
        for keyword in request.get("keywords") or []:
            keyword = str(keyword).strip()
            if keyword and _usable(keyword) and _hit(keyword, text):
                return str(request["request_id"]), keyword
    return None


def aperture_routing(
    record: Mapping[str, Any],
    *,
    aperture: str,
    packs: Sequence[str],
    requests: Iterable[Mapping[str, Any]] = (),
) -> Route:
    if record.get("pack") and record["pack"] not in packs:
        return Route(False, None, (), None, "PACK_OUT_OF_SCOPE", aperture)
    matched: list[str] = []
    priorities: list[str] = []
    request = match_request(record, requests, packs)
    if request:
        matched.append(f"P1: active management request {request[0]} matches '{request[1]}'.")
        priorities.append("P1")
    link = record.get("link") or {}
    rung = record.get("rung")
    strong = [item for item in link.get("involves") or [] if item.get("strong")]
    exposure = list(record.get("exposure") or [])
    if rung in {"DIRECT", "PERIMETER"}:
        matched.append(f"P2: {rung.lower()} link to the STE register.")
        priorities.append("P2")
    if rung == "COUNTERPARTY" and any(item.get("cls") == "Supplier" for item in strong):
        matched.append("P2: names a registered supplier.")
        priorities.append("P2")
    for hit in exposure:
        if hit.get("strong") and hit.get("item_type") in {"PROJECT", "SUPPLIER", "SITE"}:
            matched.append(f"P2: names reader-pack {hit['item_type'].lower()} {hit.get('item_name')}.")
            priorities.append("P2")
    if rung in {"COMPETITOR", "SECTOR", "THEME"}:
        matched.append(f"P3: {rung.lower()}-level match without a direct path.")
        priorities.append("P3")
    customer = (rung == "COUNTERPARTY" and any(item.get("cls") == "Customer" for item in strong)) or any(hit.get("strong") and hit.get("item_type") == "CUSTOMER" for hit in exposure)
    if customer:
        matched.append("P4: names a registered customer.")
        priorities.append("P4")
    priority = min(priorities) if priorities else None
    conditions = list(record.get("condition_ids") or [])
    if aperture == "WIDE_APERTURE":
        return Route(True, priority, tuple(matched), request[0] if request else None, None, aperture)
    if priority in {"P1", "P2", "P4"} or conditions:
        if conditions and priority is None:
            matched.append(f"Material: matches standing condition {', '.join(conditions)}.")
        return Route(True, priority, tuple(matched), request[0] if request else None, None, aperture)
    return Route(False, priority, tuple(matched), None, "NO_EXPOSURE_PATH", aperture)
