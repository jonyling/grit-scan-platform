"""Repository paths and the local .env loader. Every module finds files through this one place."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"
SECRETS_DIR = ROOT / "secrets"
SPECS_DIR = ROOT / "specs"
PROFILES_DIR = CONFIG_DIR / "profiles"
DB_ENV_VAR = "GRIT_DB"
DEFAULT_DB = OUTPUT_DIR / "grit.sqlite"


def load_local_env(path: Path | None = None) -> None:
    """Load the gitignored .env without printing values or overriding a variable the process already has."""
    file = path or ROOT / ".env"
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


def db_path() -> Path:
    """The signal-pool database. GRIT_DB overrides the default file under output/."""
    load_local_env()
    override = os.environ.get(DB_ENV_VAR, "").strip()
    return Path(override).expanduser() if override else DEFAULT_DB
