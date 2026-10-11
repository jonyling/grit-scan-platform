"""One place that names the governed workbook. The file is gitignored under secrets/."""

from __future__ import annotations

import os
from pathlib import Path

from grit.settings import ROOT, load_local_env

DEFAULT_WORKBOOK = "URS_Entity_Info_20261006.xlsx"
ENV_VAR = "GRIT_WORKBOOK"


def workbook_path(default: str | None = None, *, prefix: str | None = None) -> Path:
    """Return the workbook path. GRIT_WORKBOOK, from the process or .env, overrides the URS workbook. Another business
    area's workbook is overridden by its own variable, such as GRIT_WORKBOOK_DSC, so a URS override never feeds it."""
    load_local_env()
    variable = ENV_VAR if not prefix or prefix.upper() == "URS" else f"{ENV_VAR}_{prefix.upper()}"
    override = os.environ.get(variable, "").strip()
    path = Path(override).expanduser() if override else ROOT / "secrets" / (default or DEFAULT_WORKBOOK)
    if not path.is_file():
        raise FileNotFoundError(
            f"Workbook not found: {path}. Put the file in secrets/ or set {variable} to its path."
        )
    return path
