from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .models import Secrets

SECRETS_ENV_VAR = "SECRETS_PATH"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SECRETS_PATH = PROJECT_ROOT / ".venv" / "bin" / "secrets.json"
LEGACY_SECRETS_PATH = PROJECT_ROOT.parent / ".venv" / "bin" / "secrets.json"

ENV_SECRET_KEYS = {
    "clickup_api_key": "CLICKUP_API_KEY",
    "clickup_team_id": "CLICKUP_TEAM_ID",
    "twilio_sid": "TWILIO_ACCOUNT_SID",
    "twilio_token": "TWILIO_AUTH_TOKEN",
    "twilio_phone": "TWILIO_PHONE_NUMBER",
    "openai_api_key": "OPENAI_API_KEY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
}


def load_secrets(path: Path | str | None = None) -> Secrets:
    """
    Load secrets from environment variables or a json secrets file.

    Environment variables take precedence. If any are missing we fall back to
    the secrets json file whose path can be overridden via SECRETS_PATH.
    """
    env_values = _collect_env_values()
    if all(env_values.values()):
        return Secrets(**env_values)  # type: ignore[arg-type]

    secrets_path = _resolve_secrets_path(path)
    if not secrets_path.exists():
        missing = [ENV_SECRET_KEYS[field] for field, value in env_values.items() if not value]
        raise FileNotFoundError(
            f"Secrets file not found at {secrets_path} and missing environment variables: {', '.join(missing)}"
        )

    with open(secrets_path, "r", encoding="utf-8") as fh:
        payload: Dict[str, Any] = json.load(fh)

    resolved = {
        "clickup_api_key": _extract(payload, (("clickup", "api_key"), ("telegram", "secrets", "clickup_api_key"))),
        "clickup_team_id": _extract(payload, (("clickup", "team_id"), ("telegram", "secrets", "clickup_team_id"))),
        "twilio_sid": _extract(payload, (("twilio", "account_sid"), ("twilio", "secrets", "account_sid"))),
        "twilio_token": _extract(payload, (("twilio", "auth_token"), ("twilio", "secrets", "auth_token"))),
        "twilio_phone": _extract(payload, (("twilio", "phone_number"), ("twilio", "secrets", "phone_number"))),
        "openai_api_key": _extract(payload, (("openai", "api_key"), ("openai", "secrets", "api_key"))),
        "telegram_bot_token": _extract(payload, (("telegram", "bot_token"), ("telegram", "secrets", "bot_token"))),
        "telegram_chat_id": _extract(payload, (("telegram", "chat_id"), ("telegram", "secrets", "chat_id"))),
        "telegram_group_chat_id": _extract(payload, (("telegram", "group_chat_id"), ("telegram", "secrets", "group_chat_id"))),
    }

    missing_fields = [field for field, value in resolved.items() if not value]
    if missing_fields:
        raise KeyError(f"Missing secret values in file {secrets_path}: {', '.join(missing_fields)}")

    # Fill in any missing env values using the loaded file content prior to cast.
    for key, value in resolved.items():
        if not env_values.get(key):
            env_values[key] = value

    return Secrets(**env_values)  # type: ignore[arg-type]


def _collect_env_values() -> Dict[str, str | None]:
    return {field: os.getenv(env_key) for field, env_key in ENV_SECRET_KEYS.items()}


def _extract(payload: Mapping[str, Any], paths: Iterable[Sequence[str]]) -> str | None:
    for path in paths:
        node: Any = payload
        for key in path:
            if isinstance(node, Mapping) and key in node:
                node = node[key]
            else:
                node = None
                break
        if node is None:
            continue
        if isinstance(node, Mapping) and "value" in node:
            candidate = node["value"]
        else:
            candidate = node
        if isinstance(candidate, str):
            return candidate
    return None


def _resolve_secrets_path(path: Path | str | None) -> Path:
    """Resolve secrets file path supporting both new and legacy layouts."""
    if path:
        return Path(path).expanduser()

    env_override = os.getenv(SECRETS_ENV_VAR)
    if env_override:
        return Path(env_override).expanduser()

    for candidate in (DEFAULT_SECRETS_PATH, LEGACY_SECRETS_PATH):
        resolved = candidate.expanduser()
        if resolved.exists():
            return resolved

    return DEFAULT_SECRETS_PATH.expanduser()
