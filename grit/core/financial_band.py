"""Map a stated money amount to the pack's EBIT severity bands. No model is called here."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from grit.core.scale_cues import parse_figures


def band_for_amount(amount_millions: float, bands: Sequence[Mapping[str, Any]]) -> str | None:
    """Return the band name for an amount in millions of the pack currency, or None when bands are empty."""
    ordered = [dict(row) for row in bands if isinstance(row, Mapping) and row.get("name")]
    if not ordered:
        return None
    for row in ordered:
        lower = row.get("fromMillions")
        upper = row.get("underMillions")
        if lower is not None and amount_millions < float(lower):
            continue
        if upper is not None and amount_millions >= float(upper):
            continue
        return str(row["name"])
    return str(ordered[-1]["name"])


def financial_size(text: str, pack_bands: Mapping[str, Any] | None) -> dict[str, Any]:
    """Label a finding only when a cited page states an amount in the pack currency."""
    if not isinstance(pack_bands, Mapping) or not pack_bands.get("bands"):
        return {"label": "not established", "amount_millions": None, "currency": None, "reason": "No severity bands on the pack."}
    currency = str(pack_bands.get("currency") or "SGD").upper()
    figures = [item for item in parse_figures(text or "") if item.currency == currency and item.counted]
    if not figures:
        return {"label": "not established", "amount_millions": None, "currency": currency, "reason": "No amount in the pack currency was stated."}
    amount = max(item.amount for item in figures) / 1_000_000
    name = band_for_amount(amount, list(pack_bands.get("bands") or []))
    return {"label": name or "not established", "amount_millions": round(amount, 2), "currency": currency,
            "reason": f"{currency} {amount:g} million falls in {name}." if name else "No band matched the amount."}
