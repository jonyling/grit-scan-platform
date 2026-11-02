"""Push kept pool signals to Dataverse grit_signals. Dataverse stays the system of record.

The body uses only columns and choice codes that already exist (grit.dataverse.choices). A signal whose content hash is
already in Dataverse is patched with the refreshed labels; approval fields are never touched. Vectors stay in the pool:
grit_signals has no vector column, and none is invented here.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from grit.dataverse.choices import (
    EVIDENCE_LOW,
    RISK_LEVEL_HIGH,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MEDIUM,
    TREND_EASING,
    TREND_ESCALATING,
    TREND_NO_MATERIAL_CHANGE,
)
from grit.dataverse.signals import execution_body
from grit.pool import Pool, utc_now

_EVIDENCE = {"HIGH": 100000500, "MEDIUM": 100000501, "LOW": EVIDENCE_LOW}
_TREND = {"ESCALATING": TREND_ESCALATING, "EASING": TREND_EASING, "NO_MATERIAL_CHANGE": TREND_NO_MATERIAL_CHANGE}
_RISK = {"HIGH": RISK_LEVEL_HIGH, "MEDIUM": RISK_LEVEL_MEDIUM, "LOW": RISK_LEVEL_LOW}
_REFRESH = ("grit_headline", "grit_trend", "grit_risklevel", "grit_businessrelevancebasis", "grit_evidenceconfidence")


def signal_body(row: Mapping[str, Any], *, condition_names: Mapping[str, str], correlation_id: str) -> dict[str, Any]:
    conditions = [condition_names.get(item, item) for item in row.get("condition_ids") or []]
    themes = [hit["name"] for hit in row.get("risk_cascade") or [] if hit.get("level") == "ENTITY"]
    headline = conditions[0] if conditions else themes[0] if themes else "Unclassified signal (no standing condition or entity theme matched)"
    return {
        "grit_signalname": str(row["title"])[:100],
        "grit_headline": headline[:400],
        "grit_sourcename": row.get("publisher"),
        "grit_sourceurl": row["url"],
        "grit_fingerprint": hashlib.sha256(str(row["title"]).encode("utf-8")).hexdigest(),
        "grit_contenthash": hashlib.sha256(f"{row['url']}|{row['signal_id']}".encode("utf-8")).hexdigest(),
        "grit_signalversion": 1,
        "grit_evidenceconfidence": _EVIDENCE.get(str(row.get("evidence_confidence")), EVIDENCE_LOW),
        "grit_trend": _TREND.get(str(row.get("trend")), TREND_NO_MATERIAL_CHANGE),
        "grit_risklevel": _RISK.get(str(row.get("risk_level")), RISK_LEVEL_LOW),
        "grit_businessrelevancebasis": str(row.get("relevance_basis") or "")[:4000],
        "grit_correlationid": correlation_id[:100],
    }


def plan_sync(pool: Pool, run_id: str, condition_names: Mapping[str, str]) -> list[tuple[str, dict[str, Any]]]:
    """(signal_id, body) for every kept signal in the run."""
    return [(row["signal_id"], signal_body(row, condition_names=condition_names, correlation_id=run_id)) for row in pool.weekly_assessments(run_id)]


def push(pool: Pool, run_id: str, bodies: Sequence[tuple[str, Mapping[str, Any]]], *, client: Any) -> dict[str, int]:
    existing = client.content_hashes()
    execution = client.create("grit_executions", execution_body(name=f"GRIT-Scan {run_id}", correlation_id=run_id, started_at=utc_now(), configuration_version="grit-pool-1"))
    created = patched = 0
    for signal_id, body in bodies:
        record_id = existing.get(body["grit_contenthash"])
        if record_id:
            client.patch("grit_signals", record_id, {key: body[key] for key in _REFRESH})
            patched += 1
        else:
            record_id = client.create("grit_signals", {**body, "grit_ExecutionId@odata.bind": f"/grit_executions({execution})"})
            created += 1
        pool.insert("sync_state", {"signal_id": signal_id, "content_hash": body["grit_contenthash"], "dataverse_id": record_id, "synced_at": utc_now()}, replace=True)
    return {"created": created, "patched": patched}
