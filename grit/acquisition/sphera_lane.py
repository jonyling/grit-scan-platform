"""Sphera supplier alerts from secrets/sphera.env.

The file holds an OAuth password grant and one SPHERA_API_URL per resource. A live daily run
reads event messages and the names of directly affected suppliers. Score cards and Sphera's
own scores are not read. The status text never includes a value from the file.
"""

from __future__ import annotations

import html
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from grit.settings import SECRETS_DIR

PLACEHOLDER = SECRETS_DIR / "sphera.env"
PAGE_SIZE = 100
MAX_EVENT_PAGES = 10
MAX_OBJECT_PAGES = 10
TEXT_CHARS = 3000
_REQUIRED = ("SPHERA_AUTH_URL", "SPHERA_CLIENT_ID", "SPHERA_CLIENT_SECRET", "SPHERA_USERNAME", "SPHERA_PASSWORD")
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


class SpheraFetchError(RuntimeError):
    """Sphera did not return an event list. This is not an empty day."""


def ensure_placeholder(path: Path | None = None) -> None:
    """Write the placeholder only when the file is absent. An existing file is not read or replaced."""
    file = path or PLACEHOLDER
    if file.is_file():
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(sphera_env_example(), encoding="utf-8")


def sphera_status(path: Path | None = None) -> str:
    file = path or PLACEHOLDER
    if not file.is_file():
        return "not configured (secrets/sphera.env is missing)"
    try:
        licence = _licence(file)
    except OSError:
        return "not configured (secrets/sphera.env could not be read)"
    if licence is None:
        return "waiting for a licence (secrets/sphera.env has no auth URL, client, username, password, or event_messages URL)"
    return "configured; event messages are read on a live daily run; Sphera scores are not read"


def sphera_configured(path: Path | None = None) -> bool:
    file = path or PLACEHOLDER
    if not file.is_file():
        return False
    try:
        return _licence(file) is not None
    except OSError:
        return False


def sphera_env_example() -> str:
    """The placeholder text. Written only when the file is absent."""
    return os.linesep.join([
        "# Sphera Supply Chain Risk Management. GRIT-Scan reads event messages and supplier names.",
        "# It does not read score cards. Do not commit this file.",
        "SPHERA_AUTH_URL=",
        "SPHERA_API_URL=",
        "SPHERA_CLIENT_ID=",
        "SPHERA_CLIENT_SECRET=",
        "SPHERA_USERNAME=",
        "SPHERA_PASSWORD=",
        "",
    ])


def alert_pages(day: date, lookback_days: int, *, path: Path | None = None, getter: Getter | None = None) -> list[dict[str, Any]]:
    """Event messages reported on or after the start of the lookback, newest first. A failed fetch raises."""
    since = datetime.combine(day - timedelta(days=max(0, lookback_days)), time.min, tzinfo=timezone.utc)
    from grit.pool import utc_now

    return [
        {"url": item["url"], "title": item["title"], "text": item["text"], "publisher": item["publisher"], "published_at": item["published_at"]}
        for item in _alerts(since, path=path, getter=getter, fetched_at=utc_now())
    ]


def _alerts(since: datetime, *, path: Path | None, getter: Getter | None, fetched_at: str) -> list[dict[str, Any]]:
    from grit.acquisition.adapters import sphera

    licence = _require(path)
    fetch = getter or _client(licence)
    names: dict[str, str] = {}
    if licence.get("risk_objects_url"):
        for payload in _pages(fetch, licence["risk_objects_url"], MAX_OBJECT_PAGES):
            names.update(_names(payload))
    records: list[dict[str, Any]] = []
    for payload in _pages(fetch, licence["event_messages_url"], MAX_EVENT_PAGES):
        older = False
        for item in payload["data"]:
            record = _record(item, names)
            if record is None:
                continue
            if record.pop("_when") < since:
                older = True
                continue
            records.append(record)
        if older:
            break
    return [
        {"title": item.title, "text": item.text, "url": item.url, "publisher": item.publisher, "published_at": item.published_at}
        for item in sphera(records, fetched_at=fetched_at)
    ]


def _pages(fetch: Getter, url: str, limit: int):
    page = 1
    while page <= limit:
        payload = fetch(url, {"page[number]": str(page), "page[size]": str(PAGE_SIZE)})
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise SpheraFetchError(f"Sphera {_resource(url)} returned no data list")
        yield payload
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        try:
            last = int(meta.get("total_pages") or page)
        except (TypeError, ValueError):
            last = page
        if page >= last or not data:
            break
        page += 1


def _names(payload: dict[str, Any]) -> dict[str, str]:
    """Supplier id to name. total_risk_score is not copied."""
    found: dict[str, str] = {}
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        name = _plain(attrs.get("name"))
        if item.get("id") and name:
            found[str(item["id"])] = name
    return found


def _record(item: Any, names: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    when = _when(attrs.get("reported_at") or attrs.get("created_at"))
    title = _plain(attrs.get("subject"))
    if when is None or not title:
        return None
    affected = _affected(item, names)
    body = _plain(attrs.get("body"))
    if affected:
        label = "Affected supplier" if len(affected) == 1 else "Affected suppliers"
        body = f"{label}: {'; '.join(affected)}. {body}".strip()
    url = str(attrs.get("source_url") or "").strip()
    if url.startswith(("http://", "https://")):
        from grit.acquisition.brave_plan import canonical_url

        url = canonical_url(url)
    else:
        url = f"https://api.riskmethods.net/v2/event_messages/{item.get('id')}"
    return {
        "title": title,
        "text": body[:TEXT_CHARS],
        "url": url,
        "publisher": _plain(attrs.get("source_name")) or "Sphera",
        "published_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "_when": when,
    }


def _affected(item: dict[str, Any], names: dict[str, str]) -> list[str]:
    relationships = item.get("relationships") if isinstance(item.get("relationships"), dict) else {}
    link = relationships.get("affected_risk_objects") if isinstance(relationships.get("affected_risk_objects"), dict) else {}
    rows = link.get("data") if isinstance(link.get("data"), list) else []
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = names.get(str(row.get("id") or ""))
        if name and name not in found:
            found.append(name)
    return found


def _when(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _plain(value: Any) -> str:
    text = _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", str(value or "")))).strip()
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def _require(path: Path | None) -> dict[str, str]:
    file = path or PLACEHOLDER
    try:
        licence = _licence(file)
    except OSError as exc:
        raise SpheraFetchError("secrets/sphera.env could not be read") from exc
    if licence is None:
        raise SpheraFetchError("Sphera is not configured")
    return licence


def _licence(file: Path) -> dict[str, str] | None:
    values, urls = _assignments(file)
    resources = {_resource(url): url for url in urls}
    if any(not values.get(key) for key in _REQUIRED) or "event_messages" not in resources:
        return None
    licence = {key.removeprefix("SPHERA_").lower(): values[key] for key in _REQUIRED}
    licence["event_messages_url"] = resources["event_messages"]
    if "risk_objects" in resources:
        licence["risk_objects_url"] = resources["risk_objects"]
    return licence


def _assignments(file: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    urls: list[str] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.startswith("export "):
            text = text[7:].strip()
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key == "SPHERA_API_URL":
            if value:
                urls.append(value)
            continue
        if key and value and key not in values:
            values[key] = value
    return values, urls


def _resource(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _client(licence: dict[str, str]) -> Getter:
    import requests

    try:
        response = requests.post(
            licence["auth_url"],
            data={
                "grant_type": "password",
                "username": licence["username"],
                "password": licence["password"],
                "client_id": licence["client_id"],
                "client_secret": licence["client_secret"],
            },
            headers={"Accept": "application/json", "User-Agent": "GRIT-Scan sphera"},
            timeout=45,
        )
    except requests.RequestException as exc:
        raise SpheraFetchError("Sphera auth request failed") from exc
    if response.status_code != 200:
        raise SpheraFetchError(f"Sphera auth failed: HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise SpheraFetchError("Sphera auth did not return JSON") from exc
    token = body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise SpheraFetchError("Sphera auth returned no access token")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.api+json", "User-Agent": "GRIT-Scan sphera"}

    def fetch(url: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            got = requests.get(url, params=params, headers=headers, timeout=45)
        except requests.RequestException as exc:
            raise SpheraFetchError("Sphera request failed") from exc
        resource = _resource(url)
        if got.status_code != 200:
            raise SpheraFetchError(f"Sphera returned HTTP {got.status_code} for {resource}")
        try:
            payload = got.json()
        except ValueError as exc:
            raise SpheraFetchError(f"Sphera did not return JSON for {resource}") from exc
        if not isinstance(payload, dict):
            raise SpheraFetchError(f"Sphera JSON was not an object for {resource}")
        return payload

    return fetch
