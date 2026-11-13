"""App-only Microsoft Graph for the daily review: SharePoint list items and mail with attachments.

One Entra app serves both. It needs Sites.Selected, granted write on the review site only, and Mail.Send, limited by an
Exchange application access policy to the sender mailbox. Credentials stay in the environment, and no error text carries
one. Tests pass a transport, so nothing here touches the network in a test.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"
TIMEOUT_SECONDS = 60
CREDENTIAL_ENV = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET")
SITE_ENV = "GRIT_SP_SITE_ID"
SENDER_ENV = "GRAPH_MAIL_SENDER"
INLINE_ATTACHMENTS_MAX = 3 * 1024 * 1024  # Graph refuses a sendMail body much over 4 MB; larger files go through an upload session
UPLOAD_CHUNK = 320 * 1024 * 10  # a multiple of 320 KiB, as Graph requires
RETRY_STATUSES = {429, 500, 502, 503, 504}

Transport = Callable[..., tuple[int, Any, Mapping[str, str]]]


class GraphError(RuntimeError):
    """A Graph call failed. The text never carries a credential or a token. status is the HTTP status, or None when no
    answer came back."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def uncertain(self) -> bool:
        """No answer, or a server error: Graph may have carried out the request. A 4xx answer, 429 included, means it did not."""
        return self.status is None or self.status >= 500


def graph_settings(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The credential, site and sender settings that are set. Callers decide which ones they need."""
    values = env if env is not None else os.environ
    return {name: str(values.get(name) or "").strip() for name in (*CREDENTIAL_ENV, SITE_ENV, SENDER_ENV) if str(values.get(name) or "").strip()}


def missing(settings: Mapping[str, str], *, site: bool = False, mail: bool = False) -> list[str]:
    need = list(CREDENTIAL_ENV) + ([SITE_ENV] if site else []) + ([SENDER_ENV] if mail else [])
    return [name for name in need if not settings.get(name)]


def acquire_token(settings: Mapping[str, str]) -> str:
    import msal

    app = msal.ConfidentialClientApplication(
        settings["GRAPH_CLIENT_ID"], authority="https://login.microsoftonline.com/" + settings["GRAPH_TENANT_ID"],
        client_credential=settings["GRAPH_CLIENT_SECRET"], timeout=TIMEOUT_SECONDS,
    )
    result = app.acquire_token_for_client(scopes=[SCOPE])
    token = result.get("access_token")
    if not token:
        raise GraphError("Token acquisition failed: " + str(result.get("error") or "unknown_error"), 401)
    return str(token)


def requests_transport(method: str, url: str, *, headers: Mapping[str, str], json: Any = None, data: bytes | None = None,
                       timeout: int = TIMEOUT_SECONDS) -> tuple[int, Any, Mapping[str, str]]:
    import requests

    try:
        response = requests.request(method, url, headers=dict(headers), json=json, data=data, timeout=timeout)
    except requests.RequestException as exc:
        raise GraphError(f"Graph {method} failed: {type(exc).__name__}", None) from None
    try:
        body = response.json() if response.content else None
    except ValueError:
        body = None
    return response.status_code, body, dict(response.headers)


@dataclass
class Attachment:
    name: str
    content: bytes
    content_type: str = "application/octet-stream"


class GraphClient:
    def __init__(self, token: str, *, site_id: str | None = None, sender: str | None = None, transport: Transport | None = None,
                 retries: int = 3, sleep: Callable[[float], None] = time.sleep) -> None:
        self._token = token
        self.site_id = site_id
        self.sender = sender
        self._transport = transport or requests_transport
        self._retries = retries
        self._sleep = sleep

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, site: bool = False, mail: bool = False,
                 token_getter: Callable[[Mapping[str, str]], str] | None = None, transport: Transport | None = None) -> "GraphClient":
        settings = graph_settings(env)
        gaps = missing(settings, site=site, mail=mail)
        if gaps:
            raise GraphError("Graph is not configured. Missing: " + ", ".join(gaps), 0)
        return cls((token_getter or acquire_token)(settings), site_id=settings.get(SITE_ENV), sender=settings.get(SENDER_ENV), transport=transport)

    # Plumbing -------------------------------------------------------------------------------------------------

    def call(self, method: str, path: str, *, json: Any = None, data: bytes | None = None, headers: Mapping[str, str] | None = None,
             auth: bool = True, expect: Sequence[int] = (200, 201, 202, 204)) -> Any:
        url = path if path.startswith("https://") else GRAPH + path
        sent = {"Content-Type": "application/json"} if json is not None else {}
        if auth:
            sent["Authorization"] = "Bearer " + self._token
        sent.update(headers or {})
        for attempt in range(self._retries + 1):
            status, body, reply = self._transport(method, url, headers=sent, json=json, data=data, timeout=TIMEOUT_SECONDS)
            if status in expect:
                return body
            # A POST that got a server error may have been carried out, so only a throttled POST is sent again.
            retryable = status == 429 if method == "POST" else status in RETRY_STATUSES
            if retryable and attempt < self._retries:
                wait = reply.get("Retry-After") or reply.get("retry-after")
                self._sleep(min(float(wait), 30.0) if wait and str(wait).isdigit() else 2.0 * (attempt + 1))
                continue
            code = ((body or {}).get("error") or {}).get("code") if isinstance(body, Mapping) else None
            raise GraphError(f"Graph {method} {path.split('?')[0][:80]} answered HTTP {status}" + (f" ({code})" if code else ""), status)
        raise GraphError(f"Graph {method} gave up after {self._retries + 1} attempts", None)

    def _list(self, list_id: str) -> str:
        if not self.site_id:
            raise GraphError(f"{SITE_ENV} is not set", 0)
        return f"/sites/{quote(self.site_id, safe=',.')}/lists/{quote(list_id, safe='')}"

    # SharePoint list items ------------------------------------------------------------------------------------

    def create_item(self, list_id: str, fields: Mapping[str, Any]) -> str:
        body = self.call("POST", self._list(list_id) + "/items", json={"fields": dict(fields)})
        item_id = (body or {}).get("id")
        if not item_id:
            raise GraphError("SharePoint accepted the row but returned no item id")
        return str(item_id)

    def update_fields(self, list_id: str, item_id: str, fields: Mapping[str, Any]) -> None:
        self.call("PATCH", self._list(list_id) + f"/items/{quote(str(item_id), safe='')}/fields", json=dict(fields))

    def get_item(self, list_id: str, item_id: str) -> dict[str, Any]:
        return self.call("GET", self._list(list_id) + f"/items/{quote(str(item_id), safe='')}?$expand=fields") or {}

    def item_versions(self, list_id: str, item_id: str) -> list[dict[str, Any]]:
        """Every version of a row, newest first, each with its fields."""
        base = self._list(list_id) + f"/items/{quote(str(item_id), safe='')}/versions"
        versions = list((self.call("GET", base + "?$expand=fields") or {}).get("value") or [])
        for version in versions:
            if "fields" not in version and version.get("id"):
                version["fields"] = self.call("GET", base + f"/{quote(str(version['id']), safe='')}/fields") or {}
        return versions

    def find_item(self, list_id: str, signal_id: str, review_date: str) -> str | None:
        """The id of the row for a signal and day, or None. Used after a create whose outcome is unknown."""
        query = (f"?$expand=fields&$filter=fields/SignalId eq '{signal_id.replace(chr(39), chr(39) * 2)}'"
                 f" and fields/ReviewDate eq '{review_date}'")
        body = self.call("GET", self._list(list_id) + "/items" + query, headers={"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"})
        found = [str(row["id"]) for row in (body or {}).get("value") or [] if row.get("id")]
        return found[0] if found else None

    # Mail -----------------------------------------------------------------------------------------------------

    def send_mail(self, to: Sequence[str], subject: str, html: str, attachments: Sequence[Attachment] = (), *, cc: Sequence[str] = ()) -> str:
        """Send through the sender's mailbox. Small attachments go inline; a large one goes through an upload session."""
        if not self.sender:
            raise GraphError(f"{SENDER_ENV} is not set", 0)
        mailbox = f"/users/{quote(self.sender)}"
        message = {
            "subject": subject[:250],
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": [{"emailAddress": {"address": address}} for address in to],
            "ccRecipients": [{"emailAddress": {"address": address}} for address in cc],
        }
        inline = sum(len(item.content) for item in attachments) <= INLINE_ATTACHMENTS_MAX
        if inline:
            message["attachments"] = [self._inline(item) for item in attachments]
            self.call("POST", mailbox + "/sendMail", json={"message": message, "saveToSentItems": True})
            return "INLINE"
        try:
            draft = self.call("POST", mailbox + "/messages", json=message)
            message_id = str((draft or {}).get("id") or "")
            if not message_id:
                raise GraphError("Graph created the draft but returned no id", 0)
            for item in attachments:
                if len(item.content) <= INLINE_ATTACHMENTS_MAX // 2:
                    self.call("POST", f"{mailbox}/messages/{quote(message_id, safe='')}/attachments", json=self._inline(item))
                else:
                    self._upload(mailbox, message_id, item)
        except GraphError as exc:
            raise GraphError(str(exc), 0) from None  # only a draft exists; nothing was sent
        self.call("POST", f"{mailbox}/messages/{quote(message_id, safe='')}/send")
        return "UPLOAD_SESSION"

    @staticmethod
    def _inline(item: Attachment) -> dict[str, Any]:
        return {"@odata.type": "#microsoft.graph.fileAttachment", "name": item.name, "contentType": item.content_type,
                "contentBytes": base64.b64encode(item.content).decode("ascii")}

    def _upload(self, mailbox: str, message_id: str, item: Attachment) -> None:
        session = self.call("POST", f"{mailbox}/messages/{quote(message_id, safe='')}/attachments/createUploadSession",
                            json={"AttachmentItem": {"attachmentType": "file", "name": item.name, "size": len(item.content)}})
        url = str((session or {}).get("uploadUrl") or "")
        if not url:
            raise GraphError("Graph opened no upload session", 0)
        total = len(item.content)
        for start in range(0, total, UPLOAD_CHUNK):
            chunk = item.content[start : start + UPLOAD_CHUNK]
            end = start + len(chunk) - 1
            # The upload URL carries its own authorisation. Sending the bearer token to it is refused.
            self.call("PUT", url, data=chunk, auth=False, expect=(200, 201),
                      headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{end}/{total}"})
