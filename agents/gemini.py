"""Vertex proposer. It proposes. It does not score or write state.

A renewed GCP API key uses Vertex express mode. A project and location use
the same client shape as the Group-IT service, and only with a service-account
file that is not the key pasted in chat. No secret is stored in this repo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from grit_scan.local_env import load_local_env


_JSON_CONTRACT = (
    "Return one JSON object with cited_entity_ids, evidence_references, claims, and rationale. "
    "Each claim has statement and established. Do not include a score, severity, likelihood, or workflow transition."
)



def gemini_proposer(
    agent_name: str,
    prompt: str,
    task: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    values = _settings(env)
    if client is None:
        client = _client(values)
    model = values.get("GEMINI_MODEL") or "gemini-2.5-flash"
    return _proposal(client, agent_name, prompt, task, model)


def configured_mode(env: Mapping[str, str] | None = None) -> str:
    values = _settings(env)
    if _service_account_ready(values):
        _refuse_bad_service_account(values)
        return "service-account"
    if _api_key(values):
        return "api-key"
    raise RuntimeError(_missing_message())


def _settings(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is None:
        load_local_env()
        return os.environ
    return env


def _client(values: Mapping[str, str]) -> Any:
    genai, types = _load_sdk()
    if _service_account_ready(values):
        _refuse_bad_service_account(values)
        return genai.Client(
            vertexai=True,
            project=values["GOOGLE_CLOUD_PROJECT"],
            location=values.get("GOOGLE_CLOUD_LOCATION") or "global",
            http_options=types.HttpOptions(api_version="v1"),
        )
    key = _api_key(values)
    if not key:
        raise RuntimeError(_missing_message())
    return genai.Client(vertexai=True, api_key=key)


def _proposal(client: Any, agent_name: str, prompt: str, task: dict[str, Any], model: str) -> dict[str, Any]:
    config = {
        "system_instruction": f"{prompt} {_JSON_CONTRACT}",
        "response_mime_type": "application/json",
        "temperature": 0.1,
    }
    try:
        response = client.models.generate_content(
            model=model,
            contents=json.dumps({"agent": agent_name, "task": task}, default=str),
            config=config,
        )
        text = response.text or ""
    except Exception as exc:
        raise RuntimeError(f"Gemini request failed: {_public_error(exc)}") from None
    return _parse(text)


def _parse(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemini did not return a JSON object") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Gemini did not return a JSON object")
    return parsed


def _service_account_ready(values: Mapping[str, str]) -> bool:
    return bool((values.get("GOOGLE_CLOUD_PROJECT") or "").strip() and (values.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip())


def _public_error(exc: BaseException) -> str:
    text = str(exc).split("{", 1)[0].strip()
    return text[:160] or type(exc).__name__


def _api_key(values: Mapping[str, str]) -> str:
    raw = (values.get("GOOGLE_API_KEY") or values.get("GEMINI_API_KEY") or "").strip()
    if raw and _is_not_an_api_key(raw):
        raise RuntimeError(
            "GOOGLE_API_KEY is not an API key. It looks like a private key or a JSON file. "
            "Put the short GCP API key in that variable. Do not paste a service-account file into chat."
        )
    return raw


def _is_not_an_api_key(value: str) -> bool:
    text = value.lstrip()
    return text.startswith("-----") or text.startswith("{") or "PRIVATE KEY" in text or len(text) > 200


_COMPROMISED_PRIVATE_KEY_IDS = frozenset({"872aca78deadbeef"})


def _refuse_bad_service_account(values: Mapping[str, str]) -> None:
    raw = (values.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if not raw:
        return
    file = Path(raw).expanduser()
    if not file.is_file():
        raise RuntimeError("The configured service-account file is missing. It will not be used.")
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("The configured service-account file cannot be checked. It will not be used.") from None
    key_id = str(payload.get("private_key_id") or "")
    if key_id in _COMPROMISED_PRIVATE_KEY_IDS:
        raise RuntimeError(
            "The configured service-account credential is compromised. It was refused before any call. "
            "Use a new service-account file, not the one pasted in chat."
        )


def _missing_message() -> str:
    return (
        "Gemini credential is not configured. Vertex needs GOOGLE_CLOUD_PROJECT, "
        "GOOGLE_CLOUD_LOCATION=global, and GOOGLE_APPLICATION_CREDENTIALS pointing at a new service-account file. "
        "An API key is rejected by this API. The service-account file pasted in chat will not be used."
    )


def _load_sdk() -> tuple[Any, Any]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed in this environment.") from exc
    return genai, types


if __name__ == "__main__":
    try:
        print(f"Gemini credential: {configured_mode()}")
    except RuntimeError as exc:
        print(exc)
        raise SystemExit(1) from None
