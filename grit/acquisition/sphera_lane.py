"""Sphera supplier alerts. No API contract is documented, so this lane never calls a server.

`secrets/sphera.env` is the placeholder for a licence. When it holds SPHERA_API_URL and SPHERA_API_KEY, the daily lane
reports that the file is present and still fetches nothing. The status text never includes a value from the file.
"""

from __future__ import annotations

import os
from pathlib import Path

from grit.settings import SECRETS_DIR

PLACEHOLDER = SECRETS_DIR / "sphera.env"
_KEYS = ("SPHERA_API_URL", "SPHERA_API_KEY")


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
    found: set[str] = set()
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "not configured (secrets/sphera.env could not be read)"
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        if key.strip() in _KEYS and value.strip().strip("'\""):
            found.add(key.strip())
    if len(found) < len(_KEYS):
        return "waiting for a licence (secrets/sphera.env has no API URL and key)"
    return "licence file present; no API contract is documented, so nothing is fetched"


def sphera_env_example() -> str:
    """The placeholder text. Written only when the file is absent, and never read back here."""
    return os.linesep.join([
        "# Placeholder. GRIT-Scan does not call Sphera until an API contract is documented.",
        "# Fill these when R&A has a licence and a documented API. Do not commit this file.",
        "SPHERA_API_URL=",
        "SPHERA_API_KEY=",
        "",
    ])
