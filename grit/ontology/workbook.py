"""One place that names the governed workbook. The file is gitignored under secrets/."""

from __future__ import annotations

import os
from pathlib import Path

from grit.settings import ROOT, load_local_env

DEFAULT_WORKBOOK = "URS_Entity_Info_20261006.xlsx"
ENV_VAR = "GRIT_WORKBOOK"


def latest_workbook(prefix: str, folder: Path | None = None) -> Path | None:
    """The newest secrets/*PREFIX_Entity_Info*.xlsx, such as 02_URS_Entity_Info.xlsx, when a workbook was renamed or replaced.
    Excel's lock files (~$...) are skipped."""
    folder = folder or ROOT / "secrets"
    tag = f"{prefix.upper()}_ENTITY_INFO"
    found = [path for path in folder.glob("*.xlsx") if not path.name.startswith("~$") and tag in path.name.upper().replace(" ", "_")]
    return max(found, key=lambda path: (path.stat().st_mtime, path.name)) if found else None


def workbook_path(default: str | None = None, *, prefix: str | None = None) -> Path:
    """Return the workbook path. GRIT_WORKBOOK, from the process or .env, overrides the URS workbook. Another business
    area's workbook is overridden by its own variable, such as GRIT_WORKBOOK_DSC, so a URS override never feeds it. When the
    named file is gone, the newest workbook whose name carries PREFIX_Entity_Info is used."""
    load_local_env()
    variable = ENV_VAR if not prefix or prefix.upper() == "URS" else f"{ENV_VAR}_{prefix.upper()}"
    override = os.environ.get(variable, "").strip()
    path = Path(override).expanduser() if override else ROOT / "secrets" / (default or DEFAULT_WORKBOOK)
    if not override and not path.is_file():
        path = latest_workbook(prefix or "URS") or path
    if not path.is_file():
        raise FileNotFoundError(
            f"Workbook not found: {path}. Put the file in secrets/ or set {variable} to its path."
        )
    return path
