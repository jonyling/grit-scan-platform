"""One place that names the governed workbook. The file is gitignored under secrets/."""

from __future__ import annotations

import os
from pathlib import Path

from grit.settings import ROOT, load_local_env

DEFAULT_WORKBOOK = "URS_Entity_Info_20261006.xlsx"
ENV_VAR = "GRIT_WORKBOOK"


def workbook_path(default: str | None = None) -> Path:
    """Return the workbook path. GRIT_WORKBOOK, from the process or .env, overrides the default file in secrets/."""
    load_local_env()
    override = os.environ.get(ENV_VAR, "").strip()
    path = Path(override).expanduser() if override else ROOT / "secrets" / (default or DEFAULT_WORKBOOK)
    if not path.is_file():
        raise FileNotFoundError(
            f"Workbook not found: {path}. Put the file in secrets/ or set {ENV_VAR} to its path."
        )
    return path
