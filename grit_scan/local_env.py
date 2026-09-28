"""Load a gitignored .env without printing values or overriding the process."""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env(path: Path | None = None) -> None:
    file = path or Path(__file__).resolve().parents[1] / ".env"
    if not file.is_file():
        return
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
        if key and value and key not in os.environ:
            os.environ[key] = value
