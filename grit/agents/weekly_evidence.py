"""Bounded public-page verification for weekly evidence; no arbitrary agent tool execution."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import ssl
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urljoin, urlsplit


class _PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ignored = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "noscript"}:
            self.ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "noscript"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored and data.strip():
            self.parts.append(data.strip())


class _Redirect(Exception):
    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.target = target


def canonical_redirect(url: str, target: str) -> bool:
    """A redirect to the same page: HTTPS, the same host apart from a leading www., and the same path apart from a
    trailing slash, with the query unchanged. Anything else needs an explicit new reference."""
    try:
        before, after = urlsplit(url), urlsplit(target)
        hosts = [(item.hostname or "").removeprefix("www.") for item in (before, after)]
        return (after.scheme == "https" and after.port in {None, 443} and not after.username and not after.password
                and bool(hosts[0]) and hosts[0] == hosts[1] and before.path.rstrip("/") == after.path.rstrip("/")
                and before.query == after.query)
    except ValueError:
        return False


def verify_page(url: str, *, redirects: int = 1) -> dict[str, Any]:
    """Fetch one public HTTPS response with a pinned validated IP and verified TLS hostname. One canonical redirect
    (see canonical_redirect) is followed, and the target is validated like the original."""
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password and parsed.port in {None, 443}
        host = (parsed.hostname or "").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        valid, host = False, ""
    if not valid:
        return {"status": "NOT_CHECKED", "reason": "A public HTTPS source on port 443 is required."}
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
            return {"status": "BLOCKED", "reason": "The source resolves to a nonpublic address."}
        address = addresses[0][4][0]
        # Pin the checked IP while retaining hostname certificate verification and SNI.
        with socket.create_connection((address, 443), timeout=15) as connection:
            with ssl.create_default_context().wrap_socket(connection, server_hostname=host) as secure:
                path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
                if parsed.query:
                    path += "?" + quote(parsed.query, safe="=&%:@!$'()*+,;/?-._~")
                secure.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: GRIT-Scan evidence\r\nAccept: text/html,text/plain\r\nConnection: close\r\n\r\n".encode("ascii"))
                response = http.client.HTTPResponse(secure)
                response.begin()
                location = response.getheader("Location") if 300 <= response.status < 400 else None
                redirect = urljoin(url, location) if location else None
                if response.status != 200:
                    if redirect and redirects > 0 and canonical_redirect(url, redirect):
                        raise _Redirect(redirect)
                    return {"status": "NOT_CHECKED", "http_status": response.status,
                            "reason": "Only a redirect to the same page is followed; the source needs an explicit new reference." if 300 <= response.status < 400 else "The source did not return HTTP 200."}
                content_type = response.headers.get_content_type()
                if content_type not in {"text/html", "text/plain"}:
                    return {"status": "NOT_CHECKED", "reason": f"Unsupported page type: {content_type}"}
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    return {"status": "NOT_CHECKED", "reason": "The response exceeds the 1 MiB extraction bound."}
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    text = body.decode(charset, errors="strict")
                except (UnicodeError, LookupError):
                    return {"status": "NOT_CHECKED", "reason": "The page character encoding could not be decoded."}
    except _Redirect as moved:
        result = verify_page(moved.target, redirects=redirects - 1)
        return {**result, "url": url, "final_url": moved.target, "redirects_followed": True} if result["status"] == "SUCCEEDED" else result
    except (OSError, http.client.HTTPException, UnicodeError) as exc:
        return {"status": "FAILED", "reason": f"Public-page verification failed: {type(exc).__name__}"}
    if content_type == "text/html":
        parser = _PageText()
        parser.feed(text)
        text = "\n".join(parser.parts)
    if not text.strip():
        return {"status": "NOT_CHECKED", "reason": "No readable page text was extracted."}
    return {"status": "SUCCEEDED", "url": url, "text": text, "body_hash": hashlib.sha256(body).hexdigest(),
            "scope": "EXTRACTED_PAGE", "redirects_followed": False, "content_type": content_type}
