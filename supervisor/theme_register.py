"""In-memory theme and event register. Only the Supervisor holds the write token."""

from __future__ import annotations

from typing import Any

from grit_scan.canonical import canonical_hash
from grit_scan.theme_layer import register_write_gate


class ThemeRegister:
    def __init__(self, token: object) -> None:
        self._token = token
        self._records: list[dict[str, Any]] = []
        self.gate_log: list[dict[str, Any]] = []

    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._records)

    def snapshot(self) -> dict[str, Any]:
        themes = [record for record in self._records if record["record_kind"] == "THEME"]
        events = [record for record in self._records if record["record_kind"] == "EVENT"]
        latest: dict[str, dict[str, Any]] = {}
        for record in themes:
            identity = record["identity"]
            if identity not in latest or record["version"] > latest[identity]["version"]:
                latest[identity] = record
        return {
            "theme_ids": set(latest),
            "event_ids": {record["identity"] for record in events},
            "theme_status": {identity: record["payload"].get("theme_status") for identity, record in latest.items()},
            "theme_type": {identity: record["payload"].get("theme_type") for identity, record in latest.items()},
            "relationships": [
                record["payload"]
                for record in self._records
                if record["record_kind"] == "THEME_EVENT"
            ],
            "mutations": {record["idempotency_key"]: record["content_hash"] for record in self._records},
        }

    def apply(self, mutation: dict[str, Any], token: object) -> dict[str, Any]:
        if token is not self._token:
            raise PermissionError("Only the Supervisor may append the theme register")
        decision = register_write_gate(mutation, self.snapshot())
        self.gate_log.append(decision)
        if decision["decision"] != "ALLOW":
            return decision | {"record": None}
        payload = dict(mutation["proposed_record"])
        kind = _kind(mutation["mutation_type"])
        identity = _identity(mutation, payload)
        if mutation["mutation_type"] in {"SET_LIFECYCLE", "CLOSE"}:
            prior = next((record for record in reversed(self._records) if record["identity"] == identity and record["record_kind"] == "THEME"), None)
            if prior is not None:
                payload.setdefault("theme_type", prior["payload"].get("theme_type"))
                payload.setdefault("theme_name", prior["payload"].get("theme_name"))
        version = 1 + sum(1 for record in self._records if record["identity"] == identity and record["record_kind"] == kind)
        record = {
            "record_id": f"{kind}-{canonical_hash({'key': mutation['idempotency_key'], 'hash': mutation['content_hash']})[:16]}",
            "record_kind": kind,
            "identity": identity,
            "version": version,
            "content_hash": mutation["content_hash"],
            "idempotency_key": mutation["idempotency_key"],
            "payload": payload,
        }
        self._records.append(record)
        return decision | {"record": record}


def _kind(mutation_type: str) -> str:
    if mutation_type == "ACCEPT_EVENT":
        return "EVENT"
    if mutation_type == "ATTACH":
        return "THEME_EVENT"
    return "THEME"


def _identity(mutation: dict[str, Any], payload: dict[str, Any]) -> str:
    if mutation["mutation_type"] == "ACCEPT_EVENT":
        return str(payload.get("event_id") or canonical_hash(payload)[:12])
    if mutation["mutation_type"] == "ATTACH":
        return f"{payload.get('theme_id')}:{payload.get('event_id')}"
    if payload.get("theme_id"):
        return str(payload["theme_id"])
    return "THM-" + canonical_hash(
        {
            "name": payload.get("theme_name"),
            "register_version": payload.get("register_version"),
            "configuration_version": payload.get("configuration_version"),
        }
    )[:12]
