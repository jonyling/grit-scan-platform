"""Shape records for the review screen. Empty fields stay off the screen."""

from __future__ import annotations

from typing import Any

from grit.workflow.supervisor import Supervisor

NOT_ESTABLISHED = "not established"
_ABSENCE = {"NOT_ASSESSED", "INSUFFICIENT_DATA"}


def shown(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = ", ".join(str(item) for item in value)
    text = getattr(value, "value", None)
    if text is None:
        text = str(value)
    if text in _ABSENCE:
        return NOT_ESTABLISHED
    return text


def signal_list(supervisor: Supervisor) -> list[dict[str, str]]:
    latest: dict[str, Any] = {}
    for row in supervisor.ledger.rows():
        current = latest.get(row.signal_id)
        if current is None or row.sequence > current.sequence:
            latest[row.signal_id] = row
    return [
        {"signal_id": row.signal_id, "execution_id": row.execution_id, "state": row.state}
        for row in sorted(latest.values(), key=lambda item: item.signal_id)
    ]


def acquired_detail(record: dict[str, Any]) -> list[tuple[str, str]]:
    """A fetched headline is not an assessment. Unscored fields stay absent."""
    labels = (
        ("Headline", record.get("title") or record.get("headline")),
        ("Source", record.get("publisher") or record.get("source")),
        ("URL", record.get("url")),
        ("Published", record.get("published_at")),
        ("Evidence", record.get("evidence")),
        ("Severity", "NOT_ASSESSED"),
        ("Financial basis", "INSUFFICIENT_DATA"),
        ("Transmission", "NOT_ASSESSED"),
    )
    return [(label, text) for label, value in labels if (text := shown(value)) is not None]


def signal_detail(record: dict[str, Any]) -> list[tuple[str, str]]:
    labels = (
        ("Signal", record.get("signal_id")),
        ("State", record.get("state")),
        ("Entity", record.get("entity")),
        ("Line of business", record.get("lob")),
        ("Severity", record.get("severity")),
        ("Driving dimension", record.get("driving_dimension")),
        ("Financial basis", record.get("financial_basis")),
        ("Transmission", record.get("transmission")),
        ("Evidence", record.get("evidence")),
        ("Horizon", record.get("horizon")),
        ("Verdict", record.get("verdict")),
        ("Opportunity cost", record.get("opportunity_cost")),
        ("Coverage gaps", record.get("coverage_gaps")),
    )
    return [(label, text) for label, value in labels if (text := shown(value)) is not None]


def package_view(supervisor: Supervisor, package_id: str, weekly_delta: str | None = None) -> list[tuple[str, str]]:
    package = supervisor.package(package_id)
    fields: list[tuple[str, Any]] = [
        ("Package", package.package_id),
        ("Version", package.version),
        ("Status", package.status),
        ("Content hash", package.content_hash),
        ("Membership hash", package.membership_hash),
        ("Members", package.member_signal_ids),
        ("Weekly delta", weekly_delta),
    ]
    if package.release_blocked:
        fields.append(("Release", "blocked pending reconciliation"))
    return [(label, text) for label, value in fields if (text := shown(value)) is not None]
