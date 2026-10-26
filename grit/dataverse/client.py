"""App-only Dataverse gateway. Credentials stay in the environment."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import requests

ENV_VARS = (
    "DATAVERSE_TENANT_ID",
    "DATAVERSE_CLIENT_ID",
    "DATAVERSE_CLIENT_SECRET",
    "DATAVERSE_ORG_URL",
)


class DataverseError(RuntimeError):
    """A Dataverse call failed. The message does not include a credential."""


def require_env() -> dict[str, str]:
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in ENV_VARS:
        value = os.environ.get(name)
        if value is None or not value.strip():
            missing.append(name)
        else:
            values[name] = value.strip()
    if missing:
        raise DataverseError("Missing environment variable(s): " + ", ".join(missing))
    return values


def organization_url(org_url: str) -> str:
    """Return the environment root. A path or a copied /.default scope is not a resource."""
    value = org_url.strip().strip('"').strip("'")
    value = value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    lower = value.lower()
    for suffix in ("/.default", "/api/data/v9.2", "/api/data/v9.1", "/api/data/v9.0", "/api/data"):
        if lower.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            lower = value.lower()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if ".api.crm" in host:
        host = host.replace(".api.crm", ".crm", 1)
    if parsed.scheme != "https" or not host:
        raise DataverseError(
            "DATAVERSE_ORG_URL must be the environment root, such as https://yourorg.crm5.dynamics.com"
        )
    if parsed.path not in ("", "/"):
        raise DataverseError(
            "DATAVERSE_ORG_URL must not include a path. Use only https://yourorg.crm5.dynamics.com"
        )
    return "https://" + host


def access_token(env: dict[str, str]) -> str:
    import msal

    scope = organization_url(env["DATAVERSE_ORG_URL"]) + "/.default"
    authority = "https://login.microsoftonline.com/" + env["DATAVERSE_TENANT_ID"]
    app = msal.ConfidentialClientApplication(
        env["DATAVERSE_CLIENT_ID"],
        authority=authority,
        client_credential=env["DATAVERSE_CLIENT_SECRET"],
        timeout=60,
    )
    result = app.acquire_token_for_client(scopes=[scope])
    token = result.get("access_token")
    if not token:
        description = str(result.get("error_description") or "").split("Trace ID")[0].strip()
        raise DataverseError(
            "Token acquisition failed: "
            + str(result.get("error") or "unknown_error")
            + (". " + description[:300] if description else "")
        )
    return token


class DataverseClient:
    def __init__(self, org_url: str, token: str) -> None:
        self.base_url = org_url.rstrip("/") + "/api/data/v9.2"
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }

    @classmethod
    def from_env(cls) -> "DataverseClient":
        env = require_env()
        return cls(organization_url(env["DATAVERSE_ORG_URL"]), access_token(env))

    def get(self, path_or_url: str) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else self.base_url + "/" + path_or_url.lstrip("/")
        response = requests.get(url, headers=self.headers, timeout=60)
        if not response.ok:
            raise DataverseError(f"GET failed: HTTP {response.status_code}: {response.text[:500]}")
        return response.json()

    def get_all(self, path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page: str | None = path
        while page:
            payload = self.get(page)
            rows.extend(payload.get("value") or [])
            page = payload.get("@odata.nextLink")
        return rows

    def content_hashes(self) -> dict[str, str]:
        """Match content hashes in Python. A text $filter has missed an existing row before."""
        found: dict[str, str] = {}
        for row in self.get_all("grit_signals?$select=grit_signalid,grit_contenthash"):
            content_hash = str(row.get("grit_contenthash") or "")
            signal_id = str(row.get("grit_signalid") or "")
            if not content_hash or not signal_id:
                continue
            if content_hash in found:
                raise DataverseError("More than one signal has the same content hash")
            found[content_hash] = signal_id
        return found

    def create(self, entity_set: str, body: dict[str, Any]) -> str:
        clean = {key: value for key, value in body.items() if value is not None}
        headers = {**self.headers, "Prefer": "return=representation"}
        response = requests.post(self.base_url + "/" + entity_set, headers=headers, json=clean, timeout=60)
        if not response.ok:
            raise DataverseError(f"CREATE {entity_set} failed: HTTP {response.status_code}: {response.text[:500]}")
        payload = response.json()
        expected = entity_set[:-1] + "id" if entity_set.endswith("s") else entity_set + "id"
        if payload.get(expected):
            return str(payload[expected])
        header = response.headers.get("OData-EntityId", "")
        if "(" in header and header.endswith(")"):
            return header.rsplit("(", 1)[-1][:-1]
        raise DataverseError(f"CREATE {entity_set} returned no record id")

    def patch(self, entity_set: str, record_id: str, body: dict[str, Any]) -> None:
        response = requests.patch(
            f"{self.base_url}/{entity_set}({record_id})",
            headers=self.headers,
            json=body,
            timeout=60,
        )
        if not response.ok:
            raise DataverseError(f"PATCH {entity_set} failed: HTTP {response.status_code}: {response.text[:500]}")
