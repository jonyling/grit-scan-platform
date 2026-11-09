"""Workplace Safety & Health functional track (spec 47).

Public entry points used by the CLI and package writer:

- ``grit.wsh.digest.write_digest_pack`` — monthly Word/Markdown digest
- ``grit.wsh.alerts.draft_wsh_alerts`` — pack-local serious / regulatory drafts
- ``grit.wsh.reason.run_reasoned_review`` — Assessment → Critic over the wide prefilter
"""

from grit.wsh.alerts import draft_wsh_alerts
from grit.wsh.digest import build_digest, write_digest_pack
from grit.wsh.reason import run_reasoned_review

__all__ = [
    "build_digest",
    "draft_wsh_alerts",
    "run_reasoned_review",
    "write_digest_pack",
]
