"""One JSON call to the model. The message of an error never carries a credential."""

from __future__ import annotations

import json
import time
from typing import Any


class LoopError(RuntimeError):
    pass


_RESPONSE_KEYS = ("selections", "next_action", "queries", "reviewed_ids", "edits", "proposals", "issues")


def _object(value: Any) -> dict[str, Any] | None:
    """The role contract wants one object. A model often returns that object as a one-item array, a JSON string, or a bare list of selections."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[":
            try:
                return _object(json.loads(text))
            except json.JSONDecodeError:
                return None
        return None
    if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
        return None
    if len(value) == 1 and any(key in value[0] for key in _RESPONSE_KEYS):
        return value[0]
    if any("signal_id" in item for item in value):
        return {"selections": value}
    if any("issue_id" in item for item in value):
        return {"issues": value}
    if len(value) == 1:
        return value[0]
    return None


def _shape(value: Any) -> str:
    if isinstance(value, list):
        return f"a JSON array of {len(value)} items"
    if value is None:
        return "JSON null"
    if isinstance(value, str):
        return "a JSON string"
    if isinstance(value, bool):
        return "a JSON boolean"
    if isinstance(value, (int, float)):
        return "a JSON number"
    return f"a JSON {type(value).__name__}"


def call_json(
    client: Any, *, model: str, system: str, payload: dict[str, Any], retries: int = 1, pause: float = 2.0, thinking: str | None = "LOW"
) -> dict[str, Any]:
    """thinking sets the model's reasoning level. LOW keeps a call near 4 seconds. A model that rejects the setting is called without it."""
    last = "the call failed"
    config: dict[str, Any] = {"system_instruction": system, "response_mime_type": "application/json", "temperature": 0.1}
    if thinking:
        config["thinking_config"] = {"thinking_level": thinking}
    for attempt in range(1 + max(0, retries)):
        try:
            try:
                response = client.models.generate_content(model=model, contents=json.dumps(payload, ensure_ascii=False), config=config)
            except Exception as exc:
                if "thinking" not in str(exc).lower() or "thinking_config" not in config:
                    raise
                config = {key: value for key, value in config.items() if key != "thinking_config"}
                response = client.models.generate_content(model=model, contents=json.dumps(payload, ensure_ascii=False), config=config)
            text = (response.text or "").strip()
        except Exception as exc:
            last = f"the request failed: {str(exc).split('{', 1)[0].strip()[:160] or type(exc).__name__}"
            if attempt < retries:
                time.sleep(pause)
            continue
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            text = "\n".join(lines[:-1] if lines and lines[-1].strip() == "```" else lines).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            last = "the model did not return JSON"
            continue
        found = _object(value)
        if found is not None:
            return found
        last = f"the model returned {_shape(value)}, and the role needs one JSON object"
    raise LoopError(last)
