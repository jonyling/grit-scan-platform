"""CredibilityLookup(): domain match against the governed source register."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def credibility_lookup(url: str, register: dict[str, Any] | Path | str) -> dict[str, Any]:
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
