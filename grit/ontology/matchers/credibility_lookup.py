"""CredibilityLookup(): domain match against the governed source register, then the shared source ratings.

A pack's own register rating wins. When the register has no rating for the domain (UNRATED, or no row), the shared list in
config/source-ratings.json gives one. That list is owned by R&A, applies to every pack, and is not touched by grit pack-build,
so a rating survives a workbook rebuild. Its status (PROPOSED or CONFIRMED) is reported with each match.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


RATINGS = ("HIGH", "MEDIUM", "LOW")


def credibility_lookup(url: str, register: dict[str, Any] | Path | str, shared: dict[str, Any] | None = None) -> dict[str, Any]:
    """shared: the shared ratings document. None reads config/source-ratings.json."""
    found = _register_lookup(url, register)
    if found["credibilityRating"] in RATINGS or found["domain"] is None:
        return found
    entry = _shared_match(found["domain"], shared_ratings() if shared is None else shared)
    if entry is None:
        return found
    return {
        **found, "matched": True, "sourceName": found["sourceName"] or entry.get("sourceName"), "credibilityRating": entry["rating"],
        "active": True if not found["matched"] else found["active"], "matchMethod": "SHARED_RATING", "ratingStatus": entry.get("status") or "PROPOSED",
    }


def _shared_match(domain: str, shared: dict[str, Any]) -> dict[str, Any] | None:
    for entry in shared.get("ratings") or []:
        rated = str(entry.get("domain") or "").lower()
        if rated and entry.get("rating") in RATINGS and (domain == rated or domain.endswith("." + rated)):
            return entry
    return None


_SHARED: dict[str, Any] = {}


def shared_ratings(path: Path | None = None) -> dict[str, Any]:
    """The shared ratings, read once per process. A missing file means no shared ratings."""
    from grit.settings import CONFIG_DIR

    file = Path(path) if path else CONFIG_DIR / "source-ratings.json"
    key = str(file)
    if key not in _SHARED:
        _SHARED[key] = json.loads(file.read_text(encoding="utf-8-sig")) if file.is_file() else {"ratings": []}
    return _SHARED[key]


def _register_lookup(url: str, register: dict[str, Any] | Path | str) -> dict[str, Any]:
    sources = list(_load(register).get("sources") or [])
    domain = _domain(url)
    if domain is None:
        return _result(url, None, False, None, None, "UNRATED", False, "INVALID_URL")
    match = next(
        (
            source
            for source in sources
            if domain == str(source.get("sourceDomain") or "").lower()
            or domain.endswith("." + str(source.get("sourceDomain") or "").lower())
        ),
        None,
    )
    if match is None:
        return _result(url, domain, False, None, None, "UNRATED", False, "NO_REGISTER_MATCH")
    return _result(
        url,
        domain,
        True,
        match.get("sourceName"),
        match.get("sourceClass"),
        match.get("credibilityRating"),
        bool(match.get("active")),
        "DOMAIN_MATCH",
    )


def _result(url, domain, matched, name, source_class, rating, active, method) -> dict[str, Any]:
    return {
        "url": url,
        "domain": domain,
        "matched": matched,
        "sourceName": name,
        "sourceClass": source_class,
        "credibilityRating": rating,
        "active": active,
        "matchMethod": method,
    }


def _domain(url: str) -> str | None:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _load(register: dict[str, Any] | Path | str) -> dict[str, Any]:
    if isinstance(register, dict):
        return register
    return json.loads(Path(register).read_text(encoding="utf-8-sig"))
