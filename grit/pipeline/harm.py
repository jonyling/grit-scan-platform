"""Run the harm scorer, assess_impact(), on a pool signal.

The adapter supplies only what acquisition knows. A search result was not fetched, so its URL, dates and figures are not
cross-checked. No structured observation (fatalities, debarment, suspension) and no value-at-risk exists yet. Severity
stays NOT_ASSESSED, and the financial dimension stays INSUFFICIENT_DATA. The scorer, not this adapter, decides every result.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from functools import lru_cache
from typing import Any, Mapping, Sequence

from grit.core.impact_scoring import (
    RiskObservations,
    Signal,
    SourceEvidence,
    SourceTier,
    ValueAtRiskBasis,
    assess_impact,
)
from grit.core.severity_definition import parse_severity_definition
from grit.settings import CONFIG_DIR, SPECS_DIR

# 50% of the preceding year's EBIT is S$68.1M in spec 15, so the EBIT the definition was written against is S$136.2M.
PRECEDING_YEAR_EBIT_SGD_MILLIONS = 136.2
_TIER = {"HIGH": SourceTier.TIER_1, "MEDIUM": SourceTier.TIER_2, "LOW": SourceTier.TIER_3}


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return {field: _plain(getattr(value, field)) for field in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {(key.value if isinstance(key, Enum) else str(key)): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@lru_cache(maxsize=8)
def _registry_and_definition(pack: str | None = None) -> tuple[Any, Any, str | None]:
    """The transmission registry needs the pack's confidential workbook. Without it, harm stays unscored and says why.

    The reviewed aliases in registry_aliases.json name URS register ids, so they apply only to the URS workbook. A pack
    with no workbook of its own (an entity pack) uses its parent's."""
    from grit.ontology.pack import manifests
    from grit.ontology.registry_loader import load_transmission_registry
    from grit.ontology.workbook import workbook_path

    definition = parse_severity_definition(SPECS_DIR / "15_Severity_Definition.md", PRECEDING_YEAR_EBIT_SGD_MILLIONS)
    known = manifests()
    manifest = known.get(pack or "") or {}
    if not manifest.get("build") and manifest.get("parent"):
        manifest = known.get(manifest["parent"]) or {}
    build = manifest.get("build") or {}
    prefix = str(build.get("prefix") or "URS")
    try:
        workbook = workbook_path(build.get("defaultWorkbook"), prefix=prefix)
        aliases = CONFIG_DIR / "registry_aliases.json" if prefix.upper() == "URS" else None
        registry = load_transmission_registry(workbook, aliases)
    except (FileNotFoundError, ValueError) as exc:
        return None, definition, str(exc)
    return registry, definition, None


def score_harm(record: Mapping[str, Any], *, run_date: date, credibility: Sequence[Mapping[str, Any]], lob: str | None, pack: str | None = None) -> dict[str, Any]:
    """credibility holds one credibility_lookup() result per outlet that carries the event, the signal's own page first."""
    registry, definition, missing = _registry_and_definition(pack)
    if registry is None:
        return {"status": "NOT_RUN", "reason": f"The transmission registry is unavailable: {missing}"}
    published = None
    try:
        published = datetime.strptime(str(record.get("published_at") or ""), "%Y-%m-%dT%H:%M:%SZ").date()
    except ValueError:
        pass
    sources = tuple(
        SourceEvidence(
            url=str(item.get("url") or ""),
            source_tier=_TIER.get(str(item.get("credibilityRating")), SourceTier.UNRATED),
            independent_outlet=str(item.get("outlet") or item.get("url") or ""),
            url_check_passed=False,
            dates_cross_checked=False,
            figures_cross_checked=False,
        )
        for item in credibility
    )
    signal = Signal(
        signal_id=str(record["signal_id"]),
        scan_date=run_date,
        published_date=published,
        event_date=None,
        expected_impact_date=None,
        title=str(record.get("title") or ""),
        body=str(record.get("text") or ""),
        country="",
        entity=None,
        lob=lob,
        sources=sources,
        observations=RiskObservations(None, None, None, None, None, None),
        value_at_risk=None,
        value_at_risk_basis=ValueAtRiskBasis.INSUFFICIENT_DATA,
    )
    result = _plain(assess_impact(signal, registry, definition))
    return {"status": "SCORED", **json.loads(json.dumps(result, default=str))}


def harm_verdict(harm: Mapping[str, Any]) -> str | None:
    return (harm.get("verdict") or {}).get("verdict") if harm.get("status") == "SCORED" else None


def opportunity_status(harm: Mapping[str, Any]) -> str | None:
    return (harm.get("opportunity_cost") or {}).get("status") if harm.get("status") == "SCORED" else None
