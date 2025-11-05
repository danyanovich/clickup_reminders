from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable

try:  # pragma: no cover - support both package and script execution
    from .models import ReminderConfig, WorkingHours
except ImportError:  # pragma: no cover - script mode fallback
    from models import ReminderConfig, WorkingHours  # type: ignore

CONFIG_ENV_VAR = "CONFIG_PATH"
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


def load_config(path: Path | str | None = None) -> ReminderConfig:
    """
    Load reminder configuration from JSON.

    ENV override: CONFIG_PATH.
    """
    config_path = _resolve_config_path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as fh:
        payload: Dict[str, Any] = json.load(fh)

    working_hours_raw = payload.get("working_hours", {})
    working_hours = WorkingHours(
        start=int(working_hours_raw.get("start", 9)),
        end=int(working_hours_raw.get("end", 18)),
    )

    return ReminderConfig(
        reminder_list_name=payload["reminder_list_name"],
        phone_mapping=payload["phone_mapping"],
        working_hours=working_hours,
        clickup_workspace_id=payload.get("clickup_workspace_id"),
        check_interval_minutes=int(payload.get("check_interval_minutes", 30)),
        call_timeout_seconds=int(payload.get("call_timeout_seconds", 30)),
        max_retries=int(payload.get("max_retries", 1)),
    )


def _resolve_config_path(path: Path | str | None) -> Path:
    """Resolve config file location with backward compatible fallbacks."""
    candidates: Iterable[Path]

    if path:
        candidates = (Path(path),)
    else:
        env_override = os.getenv(CONFIG_ENV_VAR)
        if env_override:
            candidates = (Path(env_override),)
        else:
            legacy = PROJECT_ROOT / "reminder_system" / "config.json"
            candidates = (DEFAULT_CONFIG_PATH, legacy)

    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved.exists():
            return resolved
    return candidates[0].expanduser()
