"""Live Gemini adapter. It is not configured in this repo.

No API key is read from a file. A later operator may supply a credential
through the process environment. Until then, this function refuses to call
Google.
"""

from __future__ import annotations

from typing import Any


def gemini_proposer(agent_name: str, prompt: str, task: dict[str, Any]) -> dict[str, Any]:
    del agent_name, prompt, task
    raise RuntimeError("Gemini transport is not wired. No credential is read from this repo.")
