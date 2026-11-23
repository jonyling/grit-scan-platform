"""Band challenge flags: agents may disagree with core band placement but never move the item.

Lifecycle: OPEN → human UPHOLD | ACCEPT_RULE_GAP | DISMISS via ReviewGate.
ACCEPT_RULE_GAP does not change band for this package version; it records a minimal backlog miss
for R&A (P3 owns analytics / rule tuning).

Governance: agents propose challenges; grit/core owns band; a named human decides.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from grit.core.overrides import BAND_ESCALATING, BANDS, reader_band_of
from grit.pool import Pool, utc_now

FUNCTION_NAME = "BandChallengeGate"
FUNCTION_VERSION = "1.0.0"

STATUS_OPEN = "OPEN"
STATUS_UPHOLD = "UPHOLD"
STATUS_ACCEPT_RULE_GAP = "ACCEPT_RULE_GAP"
STATUS_DISMISS = "DISMISS"
RESOLUTIONS = frozenset({STATUS_UPHOLD, STATUS_ACCEPT_RULE_GAP, STATUS_DISMISS})
STATUSES = frozenset({STATUS_OPEN, *RESOLUTIONS})

# Miss kind written when a reviewer accepts a challenge as a rule gap (minimal P2 storage for P3).
MISS_KIND_BAND_RULE_GAP = "BAND_RULE_GAP"

# Default ReviewGate policy: Escalating-band challenges must be resolved before APPROVE.
DEFAULT_REQUIRE_ESCALATING_RESOLVED = True

CHALLENGE_REASON_CHARS = 400


class ChallengeError(ValueError):
    pass


def challenge_fields_of(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """challenged_band / challenge_reason / challenge_status from an assessment lead or editorial overlay."""
    if not row:
        return {}
    editorial = row.get("editorial") if isinstance(row.get("editorial"), Mapping) else {}
    source = editorial if editorial else row
    out: dict[str, Any] = {}
    for key in ("challenged_band", "challenge_reason", "challenge_status"):
        if source.get(key) is not None:
            out[key] = source[key]
    return out


def challenge_open(row: Mapping[str, Any] | None) -> bool:
    fields = challenge_fields_of(row)
    if not fields.get("challenged_band"):
        return False
    status = fields.get("challenge_status") or STATUS_OPEN
    return status == STATUS_OPEN


def validate_agent_challenge(row: dict[str, Any], core_band: str) -> None:
    """Validate optional agent challenge fields. Never moves band. Sets challenge_status=OPEN when proposing."""
    challenged = row.get("challenged_band")
    reason = row.get("challenge_reason")
    if challenged is None and reason is None:
        row.pop("challenge_status", None)
        return
    if challenged is None or reason is None:
        raise ChallengeError(
            f"{row.get('signal_id')}: a band challenge needs both challenged_band and challenge_reason."
        )
    if not isinstance(challenged, str) or challenged.strip() not in BANDS:
        raise ChallengeError(
            f"{row.get('signal_id')}: challenged_band must be Escalating, Elevated, or Watch."
        )
    challenged = challenged.strip()
    if challenged == core_band:
        raise ChallengeError(
            f"{row.get('signal_id')}: challenged_band must differ from the core band ({core_band})."
        )
    if not isinstance(reason, str) or not reason.strip():
        raise ChallengeError(f"{row.get('signal_id')}: challenge_reason must be a non-empty string.")
    cleaned = " ".join(reason.split())
    if len(cleaned) > CHALLENGE_REASON_CHARS:
        raise ChallengeError(
            f"{row.get('signal_id')}: challenge_reason must be at most {CHALLENGE_REASON_CHARS} characters."
        )
    # Agents never write status; core stamps OPEN. Band stays core-owned.
    row["challenged_band"] = challenged
    row["challenge_reason"] = cleaned
    row["challenge_status"] = STATUS_OPEN
    row.pop("band", None)  # belt-and-braces: never persist agent band


def challenge_id_for(run_id: str, signal_id: str) -> str:
    return f"CHAL-{run_id}-{signal_id}"


def open_from_editorial(
    pool: Pool,
    *,
    run_id: str,
    signal_id: str,
    band: str,
    challenged_band: str,
    challenge_reason: str,
    override_rule_id: str | None = None,
    package_id: str | None = None,
    package_version: int | None = None,
) -> dict[str, Any] | None:
    """Append an OPEN challenge when the latest status for this run/signal is not already OPEN.
    Returns None when the same proposal is already standing. Does not change assessment band."""
    if challenged_band not in BANDS or band not in BANDS or challenged_band == band:
        raise ChallengeError("OPEN challenge needs distinct core and challenged bands.")
    if not challenge_reason.strip():
        raise ChallengeError("OPEN challenge needs a reason.")
    existing = pool.band_challenge(run_id, signal_id)
    same_proposal = bool(
        existing
        and existing["band"] == band
        and existing["challenged_band"] == challenged_band
        and existing["challenge_reason"] == challenge_reason.strip()
    )
    if existing and same_proposal:
        if existing["status"] != STATUS_OPEN:
            return None
        # Refresh package linkage if the package was just submitted.
        if package_id and (
            existing.get("package_id") != package_id or existing.get("package_version") != package_version
        ):
            return pool.append_band_challenge(
                challenge_id=existing["challenge_id"],
                run_id=run_id,
                signal_id=signal_id,
                band=band,
                challenged_band=challenged_band,
                challenge_reason=challenge_reason.strip(),
                status=STATUS_OPEN,
                override_rule_id=override_rule_id,
                package_id=package_id,
                package_version=package_version,
                detail={"relinked_package": True},
            )
        return None
    return pool.append_band_challenge(
        challenge_id=challenge_id_for(run_id, signal_id),
        run_id=run_id,
        signal_id=signal_id,
        band=band,
        challenged_band=challenged_band,
        challenge_reason=challenge_reason.strip(),
        status=STATUS_OPEN,
        override_rule_id=override_rule_id,
        package_id=package_id,
        package_version=package_version,
    )


def sync_package_challenges(pool: Pool, *, package_id: str, version: int, run_id: str,
                            member_ids: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Open durable challenge rows from latest editorial proposals for package members.
    Core band is always taken from assessments; editorial never moves it."""
    opened: list[dict[str, Any]] = []
    try:
        rows = pool.weekly_assessments(run_id, include_excluded=True)
    except ValueError:
        rows = pool.assessments(run_id)
    wanted = set(member_ids) if member_ids is not None else None
    for row in rows:
        signal_id = row["signal_id"]
        if wanted is not None and signal_id not in wanted:
            continue
        editorial = row.get("editorial") if isinstance(row.get("editorial"), Mapping) else {}
        challenged = editorial.get("challenged_band") if isinstance(editorial, Mapping) else None
        reason = editorial.get("challenge_reason") if isinstance(editorial, Mapping) else None
        if not challenged or not reason:
            continue
        band = reader_band_of(row)
        priority = row.get("priority") if isinstance(row.get("priority"), Mapping) else {}
        try:
            record = open_from_editorial(
                pool,
                run_id=run_id,
                signal_id=signal_id,
                band=band,
                challenged_band=str(challenged),
                challenge_reason=str(reason),
                override_rule_id=priority.get("override_rule_id"),
                package_id=package_id,
                package_version=version,
            )
        except ChallengeError:
            continue
        if record:
            opened.append(record)
    return opened


def package_challenges(pool: Pool, package_id: str, version: int, *, open_only: bool = False) -> list[dict[str, Any]]:
    """Latest challenge status per signal for a package version."""
    return pool.band_challenges_for_package(package_id, version, open_only=open_only)


def unresolved_escalating(challenges: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """OPEN challenges against items currently in Escalating (core band)."""
    return [
        dict(row) for row in challenges
        if row.get("status") == STATUS_OPEN and row.get("band") == BAND_ESCALATING
    ]


def resolve_challenge(
    pool: Pool,
    *,
    challenge_id: str,
    resolution: str,
    reviewer_id: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Named human resolves an OPEN challenge. Never moves the item's band for this package version.
    ACCEPT_RULE_GAP appends a misses backlog row (BAND_RULE_GAP) for later R&A rule work."""
    if resolution not in RESOLUTIONS:
        raise ChallengeError(f"Resolution must be one of {', '.join(sorted(RESOLUTIONS))}.")
    if not reviewer_id.strip():
        raise ChallengeError("A named reviewer must resolve the challenge.")
    current = pool.band_challenge_by_id(challenge_id)
    if current is None:
        raise ChallengeError(f"No challenge {challenge_id}.")
    if current["status"] != STATUS_OPEN:
        raise ChallengeError(f"{challenge_id} is already {current['status']}.")
    at = utc_now()
    row = pool.append_band_challenge(
        challenge_id=challenge_id,
        run_id=current["run_id"],
        signal_id=current["signal_id"],
        band=current["band"],
        challenged_band=current["challenged_band"],
        challenge_reason=current["challenge_reason"],
        status=resolution,
        override_rule_id=current.get("override_rule_id"),
        package_id=current.get("package_id"),
        package_version=current.get("package_version"),
        decided_by=reviewer_id.strip(),
        decided_at=at,
        decision_note=(note or "").strip() or None,
        detail={"prior_status": STATUS_OPEN},
    )
    if resolution == STATUS_ACCEPT_RULE_GAP:
        run = pool.run(current["run_id"]) or {}
        pack = str(run.get("pack") or "")
        if pack:
            pool.record_miss(
                pack=pack,
                kind=MISS_KIND_BAND_RULE_GAP,
                text=(
                    f"Rule gap from band challenge {challenge_id}: core band {current['band']}, "
                    f"model proposed {current['challenged_band']}. {current['challenge_reason']}"
                    + (f" Reviewer note: {note.strip()}" if note and note.strip() else "")
                ),
                recorded_by=reviewer_id.strip(),
                signal_id=current["signal_id"],
            )
    pool.log(
        actor_type="FUNCTION",
        actor=FUNCTION_NAME,
        status=resolution,
        subject=challenge_id,
        run_id=current["run_id"],
        detail={"reviewer": reviewer_id, "signal_id": current["signal_id"], "band": current["band"],
                "challenged_band": current["challenged_band"]},
    )
    return row


def review_gate_challenge_block(
    challenges: Sequence[Mapping[str, Any]],
    *,
    require_escalating_resolved: bool = DEFAULT_REQUIRE_ESCALATING_RESOLVED,
) -> list[str]:
    """Challenge IDs that should block APPROVE under policy. Empty when APPROVE may proceed."""
    if not require_escalating_resolved:
        return []
    return [str(row["challenge_id"]) for row in unresolved_escalating(challenges)]
