#!/usr/bin/env python3
"""
High level Telegram reminder helpers.

This module centralises the logic that fetches ClickUp tasks, pushes reminder
messages to Telegram (with inline actions), and processes callback events coming
from the Telegram Bot API.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
try:  # pragma: no cover - support both package and script execution
    from .core import ReminderTask, DeliveryStats, load_config as load_raw_config, load_secrets
    from .core.telegram_utils import format_task_message, build_task_keyboard, format_group_summary
    from .telephony import TwilioService
except ImportError:  # pragma: no cover - script mode fallback
    from core import ReminderTask, DeliveryStats, load_config as load_raw_config, load_secrets
    from core.telegram_utils import format_task_message, build_task_keyboard, format_group_summary
    from telephony import TwilioService  # type: ignore

LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
CHAT_ID_CACHE_PATH = PROJECT_ROOT / "var" / "telegram_chat_id.txt"
CALLBACK_LOG_PATH = PROJECT_ROOT / "var" / "telegram_callback_log.jsonl"


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing."""



def _normalise_ids(values: Iterable[Any]) -> List[str]:
    cleaned: List[str] = []
    for candidate in values:
        if candidate is None:
            continue
        normalized = str(candidate).strip()
        if not normalized:
            continue
        if "<" in normalized or ">" in normalized:
            continue
        if normalized.lower().startswith("optional"):
            continue
        if not normalized.replace("-", "").isdigit():
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def _normalise_ids(values: Iterable[Any]) -> List[str]:
    cleaned: List[str] = []
    for candidate in values:
        if candidate is None:
            continue
        normalized = str(candidate).strip()
        if not normalized:
            continue
        if "<" in normalized or ">" in normalized:
            continue
        if normalized.lower().startswith("optional"):
            continue
        if not normalized.replace("-", "").isdigit():
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def load_runtime_credentials(config_obj: Any) -> Dict[str, Any]:
    """Load ClickUp and Telegram credentials from env/secrets using unified core logic."""
    try:
        s = load_secrets()
    except Exception as exc:
        raise ConfigurationError(f"Failed to load secrets: {exc}")

    # Map core Secrets model to the legacy dict expected by TelegramReminderService
    # We use config_obj.telegram if available for fallbacks
    telegram_cfg = getattr(config_obj, "telegram", {}) or {}
    
    env = {
        "clickup_api_key": s.clickup_api_key,
        "clickup_team_id": s.clickup_team_id,
        "clickup_team_ids": getattr(config_obj, "clickup_team_ids", []),
        "clickup_space_ids": getattr(config_obj, "clickup_space_ids", []),
        "telegram_bot_token": s.telegram_bot_token,
        "telegram_chat_id": s.telegram_chat_id or telegram_cfg.get("chat_id"),
        "telegram_group_chat_id": s.telegram_group_chat_id or telegram_cfg.get("group_chat_id"),
        "twilio_account_sid": s.twilio_sid,
        "twilio_auth_token": s.twilio_token,
        "twilio_phone_number": s.twilio_phone,
        "twilio_to_alex": os.getenv("TWILIO_TO_ALEX"), # alex override remains from env
    }
    
    # Ensure team_ids is a list
    if not env["clickup_team_ids"] and env["clickup_team_id"]:
        env["clickup_team_ids"] = [env["clickup_team_id"]]
        
    return env


def _format_due(due_raw: Any, timezone_name: str) -> str:
    if not due_raw:
        return "Не указан"
    try:
        timestamp = int(due_raw) / 1000
        tz = pytz.timezone(timezone_name)
        dt = datetime.fromtimestamp(timestamp, tz)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(due_raw)


def _assignee_identity(task: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    assignees = task.get("assignees") or []
    if assignees and isinstance(assignees, list):
        first = assignees[0]
        name = first.get("username") or first.get("email") or first.get("name")
        if name:
            raw_id = first.get("id")
            assignee_id = str(raw_id).strip() if raw_id is not None else None
            return str(name), assignee_id or None

    watchers = task.get("watchers") or []
    if watchers and isinstance(watchers, list) and len(watchers) == 1:
        creator = task.get("creator") or {}
        creator_id = str(creator.get("id")).strip() if creator and creator.get("id") is not None else None
        for watcher in watchers:
            watcher_raw_id = watcher.get("id")
            watcher_id = str(watcher_raw_id).strip() if watcher_raw_id is not None else None
            if watcher_id and creator_id and watcher_id == creator_id:
                continue
            name = watcher.get("username") or watcher.get("email") or watcher.get("name")
            if name and name != "ClickBot":
                return str(name), watcher_id

    custom_fields = task.get("custom_fields") or []
    for field in custom_fields:
        if isinstance(field, dict) and field.get("name", "").lower() == "assignee":
            value = field.get("value")
            if isinstance(value, str) and value:
                return value, None

    return "—", None


class TelegramReminderService:
    """Business logic that orchestrates ClickUp and Telegram interactions."""

    def __init__(
        self,
        config: Dict[str, Any],
        credentials: Dict[str, str],
        session: Optional[requests.Session] = None,
    ):
        self.config = config
        self.credentials = credentials
        self.session = session or requests.Session()
        self.telegram_config = self.config.get("telegram") or {}
        config_chat_raw = self.telegram_config.get("chat_id")
        self._config_chat_id = str(config_chat_raw).strip() if config_chat_raw is not None else None
        if self._config_chat_id == "":
            self._config_chat_id = None
        config_group_chat_raw = self.telegram_config.get("group_chat_id")
        self._config_group_chat_id = (
            str(config_group_chat_raw).strip() if config_group_chat_raw is not None else None
        )
        if self._config_group_chat_id == "":
            self._config_group_chat_id = None
        group_chat_url_raw = self.telegram_config.get("group_chat_url")
        self.group_chat_url = str(group_chat_url_raw).strip() if group_chat_url_raw else None
        self.bot_token = credentials["telegram_bot_token"]
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

        configured_chat = credentials.get("telegram_chat_id")
        self._configured_default_chat = str(configured_chat).strip() if configured_chat else None
        cached_chat = self._load_cached_chat_id()
        if self._configured_default_chat:
            self.default_chat_id = self._configured_default_chat
        else:
            self.default_chat_id = cached_chat

        configured_group_chat = credentials.get("telegram_group_chat_id") or self.telegram_config.get(
            "group_chat_id"
        )
        if configured_group_chat is not None:
            group_chat_str = str(configured_group_chat).strip()
            self.group_chat_id = group_chat_str or None
        else:
            self.group_chat_id = None

        self.team_ids = self._resolve_team_ids()
        if not self.team_ids:
            raise ConfigurationError("Не указаны идентификаторы ClickUp workspace/team.")

        self.clickup_clients = [
            ClickUpClient(
                api_key=credentials["clickup_api_key"],
                team_id=team_id,
            )
            for team_id in self.team_ids
        ]
        self.clickup_client = self.clickup_clients[0]

        self.clickup_config = self.config.get("clickup", {}) or {}
        self.status_mapping = self._build_status_mapping()
        self.completed_statuses = self._build_completed_statuses()
        self.reminder_tags = self._resolve_reminder_tags()
        self.reminders_list_id = self.clickup_config.get("list_id") or self.config.get("clickup_list_id")
        self.reminders_list_name = (
            self.clickup_config.get("reminders_list_name")
            or self.config.get("reminder_list_name")
            or "Напоминания"
        )
        self.space_ids = self._resolve_space_ids()
        (
            self.assignee_chat_map_by_id,
            self.assignee_chat_map_by_name,
        ) = self._build_assignee_chat_map()
        self.status_actions = self._build_status_actions()
        self.status_action_map = {action["code"]: action for action in self.status_actions}
        self.status_action_by_key = {action["key"]: action for action in self.status_actions}
        self.chat_shortcuts = []
        for action in self.status_actions:
            if action["key"] not in self.status_mapping:
                normalized = action["key"].replace("_", " ").lower()
                LOGGER.warning(
                    "Status mapping key '%s' missing in config; defaulting to '%s'.",
                    action["key"],
                    normalized,
                )
                self.status_mapping[action["key"]] = normalized
        tz_name = config.get("working_hours", {}).get("timezone") or "UTC"
        self.timezone_name = tz_name
        self.callback_log_path = self._resolve_callback_log_path()
        self._processed_callback_ids: set[str] = self._load_processed_callback_ids()
        self.phone_mapping = self._build_phone_mapping()
        self._apply_phone_overrides()
        self.channel_preferences = self._build_channel_preferences()
        self.channel_defaults = self._build_channel_defaults()
        self.twilio_service: Optional[TwilioService] = None
        self.twilio_from_phone: Optional[str] = None
        self._webhook_cleared = False
        self._init_twilio_service()
        self._last_delivery_stats: Optional[DeliveryStats] = None
        self._recent_user_actions: List[str] = []
        self._recent_failed_actions: List[str] = []
        self._warn_chat_configuration()
        self._callback_log_max_bytes = self._resolve_callback_log_max_bytes()
        self._callback_log_max_entries = self._resolve_callback_log_max_entries()

        # Initialize ClickUp Engine
        try:
            from core.engine import ClickUpEngine
            from core import load_config as load_raw_cfg, load_secrets as load_raw_secs
        except ImportError:
            from .core.engine import ClickUpEngine
            from .core import load_config as load_raw_cfg, load_secrets as load_raw_secs

        self.engine = ClickUpEngine(load_raw_cfg(), load_raw_secs())

    @classmethod
    def from_environment(cls) -> "TelegramReminderService":
        config = load_raw_config()
        credentials = load_runtime_credentials(config)
        return cls(config=config, credentials=credentials)

    def _resolve_team_ids(self) -> List[str]:
        team_ids_raw = self.credentials.get("clickup_team_ids")
        team_ids: List[str] = []

        if isinstance(team_ids_raw, (list, tuple, set)):
            candidates = team_ids_raw
        elif isinstance(team_ids_raw, str):
            candidates = [team_ids_raw]
        else:
            candidates = []

        for candidate in candidates:
            normalized = str(candidate).strip()
            if normalized and normalized not in team_ids:
                team_ids.append(normalized)

        fallback = self.credentials.get("clickup_team_id")
        if fallback:
            fallback_str = str(fallback).strip()
            if fallback_str and fallback_str not in team_ids:
                team_ids.insert(0, fallback_str)
        return team_ids

    def _resolve_space_ids(self) -> List[str]:
        candidates: List[Any] = []
        for source in (
            self.clickup_config.get("space_ids"),
            self.config.get("clickup_space_ids"),
            self.credentials.get("clickup_space_ids"),
        ):
            if not source:
                continue
            if isinstance(source, (list, tuple, set)):
                values = source
            else:
                values = [source]
            candidates.extend(values)
        return _normalise_ids(candidates)

    def _build_status_mapping(self) -> Dict[str, str]:
        clickup_section = self.clickup_config
        mapping = {key.upper(): value for key, value in clickup_section.get("status_mapping", {}).items()}
        mapping.setdefault("ВЫПОЛНЕНО", clickup_section.get("completed_status", "complete"))
        mapping.setdefault("НЕ_ВЫПОЛНЕНО", clickup_section.get("pending_status", "to do"))
        mapping.setdefault("В_РАБОТЕ", clickup_section.get("in_progress_status", "in progress"))
        mapping.setdefault("ПОСТАВЛЕНА", clickup_section.get("pending_status", "поставлена"))
        mapping.setdefault("НА_ДОРАБОТКЕ", clickup_section.get("callback_status", "на доработке"))
        mapping.setdefault("ОТМЕНЕНА", clickup_section.get("cancelled_status", "отменена"))
        return mapping

    def _apply_phone_overrides(self) -> None:
        override_alex = str(self.credentials.get("twilio_to_alex") or "").strip()
        if not override_alex:
            return

        for alias in ("alex", "алекс"):
            normalized_alias = self._normalize_assignee_name(alias)
            if normalized_alias:
                self.phone_mapping[normalized_alias] = override_alex

    @staticmethod
    def _normalize_channel_name(channel: str) -> Optional[str]:
        if not channel:
            return None
        normalized = str(channel).strip().lower()
        if not normalized:
            return None
        if normalized in {"telegram", "tg"}:
            return "telegram"
        if normalized in {"voice", "call", "twilio", "phone"}:
            return "voice"
        if normalized in {"sms", "text", "message"}:
            return "sms"
        return normalized

    def _build_channel_preferences(self) -> Dict[str, Tuple[str, ...]]:
        telegram_cfg = self.config.get("telegram") or {}
        raw_channels = telegram_cfg.get("channels") or {}
        if not isinstance(raw_channels, dict):
            return {}

        preferences: Dict[str, Tuple[str, ...]] = {}
        for raw_name, raw_values in raw_channels.items():
            if raw_name is None:
                continue

            if isinstance(raw_name, str):
                aliases = [part.strip() for part in raw_name.split("|") if part.strip()]
            else:
                aliases = [str(raw_name).strip()]

            if isinstance(raw_values, str):
                values_iterable = [raw_values]
            elif isinstance(raw_values, (list, tuple, set)):
                values_iterable = raw_values
            else:
                continue

            normalized_channels: List[str] = []
            for entry in values_iterable:
                channel = self._normalize_channel_name(entry)
                if channel and channel not in normalized_channels:
                    normalized_channels.append(channel)

            if not normalized_channels:
                continue

            channel_tuple = tuple(normalized_channels)
            for alias in aliases:
                normalized_alias = self._normalize_assignee_name(alias)
                if normalized_alias:
                    preferences[normalized_alias] = channel_tuple

        return preferences

    def _build_channel_defaults(self) -> Tuple[str, ...]:
        telegram_cfg = self.config.get("telegram") or {}
        raw_defaults = telegram_cfg.get("channel_defaults")
        normalized: List[str] = []
        if isinstance(raw_defaults, (list, tuple, set)):
            for entry in raw_defaults:
                channel = self._normalize_channel_name(entry)
                if channel and channel not in normalized:
                    normalized.append(channel)
        elif isinstance(raw_defaults, str):
            channel = self._normalize_channel_name(raw_defaults)
            if channel:
                normalized.append(channel)

        if not normalized:
            normalized = ["telegram", "voice", "sms"]

        return tuple(normalized)

    def _channels_for_assignee(self, name: str, assignee_id: Optional[str]) -> Tuple[str, ...]:
        normalized = self._normalize_assignee_name(name)
        if normalized:
            channels = self.channel_preferences.get(normalized)
            if channels:
                return channels

        if assignee_id:
            direct = self.channel_preferences.get(str(assignee_id).strip().lower())
            if direct:
                return direct

        return self.channel_defaults

    def channels_for_task(self, task: ReminderTask) -> Tuple[str, ...]:
        return self._channels_for_assignee(task.assignee, task.assignee_id)

    def task_requires_channel(self, task: ReminderTask, channel: str) -> bool:
        canonical = self._normalize_channel_name(channel)
        if not canonical:
            return False
        return canonical in self.channels_for_task(task)

    def _channel_enabled(self, channel: str, task: ReminderTask) -> bool:
        canonical = self._normalize_channel_name(channel)
        if canonical == "telegram":
            return True
        if canonical == "voice":
            recipient = self._resolve_twilio_recipient(task)
            return bool(recipient and recipient in self.phone_mapping)
        if canonical == "sms":
            recipient = self._resolve_twilio_recipient(task)
            return bool(recipient and recipient in self.phone_mapping)
        return False

    def _resolve_twilio_recipient(self, task: ReminderTask) -> Optional[str]:
        normalized = self._normalize_assignee_name(task.assignee)
        if normalized and normalized in self.phone_mapping:
            return normalized

        # If assignee is empty or "—", always check description
        description = task.description or ""
        if isinstance(description, str) and description:
            lowered = description.lower()
            # First try all phone_mapping aliases
            for alias in self.phone_mapping.keys():
                if alias and alias in lowered:
                    return alias
            # Then try common names
            for alias in ("alex", "алекс"):
                if alias in lowered and alias in self.phone_mapping:
                    return alias
        return None

    def _build_completed_statuses(self) -> set[str]:
        statuses = {self.status_mapping.get("ВЫПОЛНЕНО", "complete").lower()}
        completed = self.clickup_config.get("completed_status")
        if completed:
            statuses.add(str(completed).lower())
        statuses.add("complete")
        statuses.add("done")
        return statuses

    def _resolve_reminder_tags(self) -> List[str]:
        tags_cfg = (
            self.clickup_config.get("reminder_tags")
            or self.clickup_config.get("reminder_tag")
            or self.config.get("reminder_tags")
            or self.config.get("reminder_tag")
        )

        if isinstance(tags_cfg, str):
            raw_tags: Iterable[str] = (tags_cfg,)
        elif isinstance(tags_cfg, (list, tuple, set)):
            raw_tags = (str(tag) for tag in tags_cfg)
        else:
            raw_tags = ()

        tags: List[str] = []
        seen: set[str] = set()
        for candidate in raw_tags:
            normalized = str(candidate).strip()
            if not normalized or normalized in seen:
                continue
            tags.append(normalized)
            seen.add(normalized)
        return tags

    def _build_assignee_chat_map(self) -> Tuple[Dict[str, Tuple[str, ...]], Dict[str, Tuple[str, ...]]]:
        telegram_cfg = self.config.get("telegram") or {}
        mapping_cfg = telegram_cfg.get("assignee_chat_map") or telegram_cfg.get("assignee_chats") or {}
        if not isinstance(mapping_cfg, dict):
            return {}, {}

        ids_map: Dict[str, Tuple[str, ...]] = {}
        names_map: Dict[str, Tuple[str, ...]] = {}

        for raw_name, raw_chat_ids in mapping_cfg.items():
            if raw_name is None:
                continue

            if isinstance(raw_name, str):
                name_candidates = [part.strip() for part in raw_name.split("|") if part.strip()]
            else:
                name_candidates = [str(raw_name).strip()]

            extra_aliases: List[str] = []
            explicit_ids: List[str] = []
            chat_source = raw_chat_ids

            if isinstance(raw_chat_ids, dict):
                alias_field = raw_chat_ids.get("aliases")
                if isinstance(alias_field, str):
                    extra_aliases.extend(part.strip() for part in alias_field.split("|") if part.strip())
                elif isinstance(alias_field, (list, tuple, set)):
                    for alias in alias_field:
                        alias_str = str(alias).strip()
                        if alias_str:
                            extra_aliases.append(alias_str)

                id_field = raw_chat_ids.get("ids") or raw_chat_ids.get("assignee_ids") or raw_chat_ids.get("user_ids")
                if isinstance(id_field, str):
                    explicit_ids.extend(part.strip() for part in id_field.split("|") if part.strip())
                elif isinstance(id_field, (list, tuple, set)):
                    for entry in id_field:
                        entry_str = str(entry).strip()
                        if entry_str:
                            explicit_ids.append(entry_str)

                chat_candidates = (
                    raw_chat_ids.get("chat_ids")
                    or raw_chat_ids.get("chats")
                    or raw_chat_ids.get("telegram_ids")
                    or raw_chat_ids.get("chat_id")
                    or raw_chat_ids.get("telegram_id")
                )
                if isinstance(chat_candidates, (list, tuple, set)):
                    chat_source = chat_candidates
                elif chat_candidates is not None:
                    chat_source = (chat_candidates,)
                else:
                    chat_source = ()

            name_candidates.extend(extra_aliases)

            if not name_candidates:
                continue

            if isinstance(chat_source, (list, tuple, set)):
                chat_iterable = chat_source
            else:
                chat_iterable = (chat_source,)

            chats: List[str] = []
            seen_chat: set[str] = set()
            for chat in chat_iterable:
                chat_str = str(chat).strip()
                if not chat_str or chat_str in seen_chat:
                    continue
                chats.append(chat_str)
                seen_chat.add(chat_str)

            if not chats:
                continue

            chat_tuple = tuple(chats)

            def store_id(identifier: str) -> None:
                token = identifier.strip()
                if token:
                    ids_map[token] = chat_tuple

            def store_name(alias: str) -> None:
                normalized = self._normalize_assignee_name(alias)
                if normalized:
                    names_map[normalized] = chat_tuple

            for name in name_candidates:
                if not name:
                    continue
                token = name.strip()
                if not token:
                    continue
                lowered = token.lower()
                id_candidate: Optional[str] = None
                if token.isdigit():
                    id_candidate = token
                elif lowered.startswith("id:"):
                    _, _, suffix = token.partition(":")
                    candidate = suffix.strip()
                    if candidate:
                        id_candidate = candidate
                if id_candidate:
                    store_id(id_candidate)
                elif token:
                    store_name(token)

            for identifier in explicit_ids:
                identifier_str = str(identifier).strip()
                if not identifier_str:
                    continue
                if identifier_str.isdigit():
                    store_id(identifier_str)
                elif identifier_str.lower().startswith("id:"):
                    _, _, suffix = identifier_str.partition(":")
                    store_id(suffix.strip())
                else:
                    store_name(identifier_str)

        return ids_map, names_map

    def _build_phone_mapping(self) -> Dict[str, str]:
        """
        Normalise phone mapping config so we can correlate assignees with numbers.

        Supports legacy keys like ``contacts`` and allows multiple aliases separated by ``|``.
        """
        raw_mapping = (
            self.config.get("phone_mapping")
            or self.config.get("contacts")
            or self.config.get("assignee_phones")
            or {}
        )
        if not isinstance(raw_mapping, dict):
            return {}

        mapping: Dict[str, str] = {}
        for raw_name, raw_phone in raw_mapping.items():
            if raw_phone is None:
                continue
            phone = str(raw_phone).strip()
            if not phone:
                continue

            if isinstance(raw_name, str):
                aliases = [part.strip() for part in raw_name.split("|") if part.strip()]
            else:
                aliases = [str(raw_name).strip()]

            for alias in aliases:
                normalized = self._normalize_assignee_name(alias)
                if not normalized or normalized in mapping:
                    continue
                mapping[normalized] = phone

        return mapping

    def _init_twilio_service(self) -> None:
        account_sid = str(self.credentials.get("twilio_account_sid") or "").strip()
        auth_token = str(self.credentials.get("twilio_auth_token") or "").strip()
        phone_number = str(self.credentials.get("twilio_phone_number") or "").strip()

        if not (account_sid and auth_token and phone_number):
            self.twilio_service = None
            self.twilio_from_phone = None
            return

        try:
            self.twilio_service = TwilioService(account_sid, auth_token)
            self.twilio_from_phone = phone_number
        except Exception as exc:  # pragma: no cover - network/sdk guard
            LOGGER.warning("Failed to initialise Twilio client: %s", exc)
            self.twilio_service = None
            self.twilio_from_phone = None

    def _generate_action_code(self, index: int, used: set[str]) -> str:
        base = f"a{index}"
        counter = 0
        candidate = base
        while candidate in used:
            counter += 1
            candidate = f"{base}{counter}"
        used.add(candidate)
        return candidate

    def _build_status_actions(self) -> List[Dict[str, str]]:
        telegram_cfg = self.config.get("telegram") or {}
        raw_actions = telegram_cfg.get("status_buttons") or telegram_cfg.get("status_actions")
        actions: List[Dict[str, str]] = []
        used_codes: set[str] = set()

        if isinstance(raw_actions, list):
            for idx, entry in enumerate(raw_actions):
                if isinstance(entry, str):
                    key = entry.strip().upper()
                    if not key:
                        continue
                    text = key.title()
                    code = self._generate_action_code(idx, used_codes)
                    action: Dict[str, Any] = {"code": code, "key": key, "text": text}
                elif isinstance(entry, dict):
                    key = str(
                        entry.get("key")
                        or entry.get("status")
                        or entry.get("value")
                        or entry.get("name")
                        or ""
                    ).strip().upper()
                    if not key:
                        continue
                    text = str(entry.get("text") or entry.get("label") or key.title())
                    raw_code = entry.get("code")
                    if raw_code:
                        code = str(raw_code).strip()
                        if not code or code in used_codes:
                            code = self._generate_action_code(idx, used_codes)
                        else:
                            used_codes.add(code)
                    else:
                        code = self._generate_action_code(idx, used_codes)

                    action = dict(entry)
                    action["code"] = code
                    action["key"] = key
                    action["text"] = text
                else:
                    continue

                # Ensure callbacks always have minimal required fields
                if "code" not in action or "key" not in action or "text" not in action:
                    continue

                actions.append(action)

        if not actions:
            actions = [
                {"code": "d", "key": "ВЫПОЛНЕНО", "text": "✅ Выполнено"},
                {"code": "n", "key": "НЕ_ВЫПОЛНЕНО", "text": "❌ Не выполнено"},
                {"code": "p", "key": "В_РАБОТЕ", "text": "🔄 В работе"},
            ]
            used_codes.update(action["code"] for action in actions)

        return actions

    def _update_delivery_stats(
        self,
        deliveries: Dict[str, Sequence[ReminderTask]],
        total_tasks: int,
        *,
        requested_chat: Optional[str],
        broadcast_all: bool,
    ) -> None:
        try:
            tz = pytz.timezone(self.timezone_name)
        except Exception:  # pragma: no cover - fallback
            tz = pytz.UTC

        timestamp = datetime.now(tz)
        per_chat_counts: Dict[str, int] = {chat: len(tasks) for chat, tasks in deliveries.items()}
        per_chat_assignees: Dict[str, List[str]] = {}
        delivered_ids: Set[str] = set()

        for chat, tasks in deliveries.items():
            assignees = {task.assignee or "—" for task in tasks}
            per_chat_assignees[chat] = sorted(assignees)
            for task in tasks:
                delivered_ids.add(task.task_id)

        delivered_tasks = len(delivered_ids)
        missing_tasks = max(total_tasks - delivered_tasks, 0)
        requested_chat_str = str(requested_chat).strip() if requested_chat is not None else None

        self._last_delivery_stats = DeliveryStats(
            timestamp=timestamp,
            timezone=self.timezone_name,
            total_tasks=total_tasks,
            delivered_tasks=delivered_tasks,
            per_chat_counts=per_chat_counts,
            per_chat_assignees=per_chat_assignees,
            missing_tasks=missing_tasks,
            broadcast_all=broadcast_all,
            requested_chat=requested_chat_str,
            user_actions=list(self._recent_user_actions),
            failed_actions=list(self._recent_failed_actions),
        )

        if deliveries:
            detail = ", ".join(f"{chat}:{len(tasks)}" for chat, tasks in deliveries.items())
        else:
            detail = "no deliveries"
        LOGGER.info(
            "Telegram deliveries: total=%s delivered=%s missing=%s chats=%s",
            total_tasks,
            delivered_tasks,
            missing_tasks,
            detail,
        )

    def _build_chat_shortcuts(self) -> List[Dict[str, str]]:
        return []

    def _register_user_action(self, entry: str) -> None:
        if not entry:
            return
        self._recent_user_actions.append(entry)
        if len(self._recent_user_actions) > 50:
            self._recent_user_actions = self._recent_user_actions[-50:]
        if self._last_delivery_stats is not None:
            self._last_delivery_stats.user_actions.append(entry)

    def _register_failed_action(self, entry: str) -> None:
        if not entry:
            return
        self._recent_failed_actions.append(entry)
        if len(self._recent_failed_actions) > 50:
            self._recent_failed_actions = self._recent_failed_actions[-50:]
        if self._last_delivery_stats is not None:
            self._last_delivery_stats.failed_actions.append(entry)

    @staticmethod
    def _format_actor_label(actor: Dict[str, Any]) -> str:
        if not isinstance(actor, dict):
            return ""
        username = str(actor.get("username") or "").strip()
        if username:
            if not username.startswith("@"):
                username = f"@{username}"
            return username
        first_name = str(actor.get("first_name") or "").strip()
        last_name = str(actor.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part)
        if full_name:
            return full_name
        user_id = actor.get("id")
        if user_id is not None:
            return f"id={user_id}"
        return ""

    def _load_cached_chat_id(self) -> Optional[str]:
        try:
            if CHAT_ID_CACHE_PATH.exists():
                value = CHAT_ID_CACHE_PATH.read_text(encoding="utf-8").strip()
                return value or None
        except Exception as exc:  # pragma: no cover - best effort
            LOGGER.debug("Failed to read cached chat id: %s", exc)
        return None

    def _persist_chat_id(self, chat_id: str) -> None:
        try:
            CHAT_ID_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CHAT_ID_CACHE_PATH.write_text(str(chat_id), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - best effort
            LOGGER.warning("Failed to persist chat id %s: %s", chat_id, exc)

    def _resolve_target_chat(self, override: Optional[str] = None) -> Optional[str]:
        if override:
            return str(override)
        if self.default_chat_id:
            return str(self.default_chat_id)
        cached = self._load_cached_chat_id()
        if cached:
            self.default_chat_id = cached
            return cached
        return None

    def _ensure_default_chat(self, chat_id: str) -> None:
        if not self.default_chat_id:
            self.default_chat_id = chat_id
            self._persist_chat_id(chat_id)

    def resolve_summary_chat(self, override: Optional[str] = None) -> Optional[str]:
        if override:
            LOGGER.debug("Summary chat override provided: %s", override)
            return str(override)
        if self.group_chat_id:
            LOGGER.info("Summary notifications will be sent to group chat %s.", self.group_chat_id)
            return self.group_chat_id
        fallback = self._resolve_target_chat()
        if fallback:
            LOGGER.info("Summary notifications fallback to default chat %s.", fallback)
        else:
            LOGGER.warning("Failed to resolve summary chat: no group or default chat available.")
        return fallback

    def register_callback_metrics(self, processed_callbacks: int) -> None:
        if not self._last_delivery_stats:
            return
        self._last_delivery_stats.callbacks_processed = processed_callbacks

    def register_voice_results(self, attempted: int, failures: int) -> None:
        if not self._last_delivery_stats:
            return
        self._last_delivery_stats.voice_calls = attempted
        self._last_delivery_stats.voice_failures = failures

    def generate_group_summary(self) -> Optional[str]:
        stats = self._last_delivery_stats
        if not stats:
            return None
        return format_group_summary(stats)

        if stats.per_chat_counts:
            lines.append("Чаты:")
            for chat_id, count in sorted(stats.per_chat_counts.items(), key=lambda item: item[0]):
                assignees = stats.per_chat_assignees.get(chat_id) or []
                assignee_text = f" (исполнители: {', '.join(assignees)})" if assignees else ""
                lines.append(f"  ◦ {chat_id}: {count} задач{assignee_text}")

        # Once summary is generated, clear collected actions to avoid duplicates on next run.
        self._recent_user_actions.clear()
        self._recent_failed_actions.clear()
        stats.user_actions.clear()
        stats.failed_actions.clear()

        return "\n".join(lines)

    def _warn_chat_configuration(self) -> None:
        if self._config_chat_id and self._configured_default_chat and self._config_chat_id != self._configured_default_chat:
            LOGGER.warning(
                "telegram.chat_id=%s differs from TELEGRAM_CHAT_ID=%s; using %s.",
                self._config_chat_id,
                self._configured_default_chat,
                self._configured_default_chat,
            )
        if self._config_group_chat_id and self.group_chat_id and self._config_group_chat_id != self.group_chat_id:
            LOGGER.warning(
                "telegram.group_chat_id=%s differs from TELEGRAM_GROUP_CHAT_ID=%s; using %s.",
                self._config_group_chat_id,
                self.group_chat_id,
                self.group_chat_id,
            )
        if not self.group_chat_id and not (self.default_chat_id or self._configured_default_chat):
            LOGGER.warning(
                "Telegram bot has no chat id registered; send /start from a chat or configure TELEGRAM_CHAT_ID."
            )

    def _ensure_webhook_cleared(self) -> None:
        if self._webhook_cleared:
            return
        try:
            self._telegram_post("deleteWebhook", {"drop_pending_updates": False})
        except Exception as exc:  # pragma: no cover - network guard
            LOGGER.warning("Failed to delete Telegram webhook before polling: %s", exc)
        else:
            self._webhook_cleared = True

    @staticmethod
    def _normalize_assignee_name(name: str) -> str:
        normalized = str(name or "").strip().lower()
        if not normalized:
            return ""
        return " ".join(normalized.split())

    def _chat_targets_for_task(self, task: ReminderTask) -> Tuple[str, ...]:
        assignee_id = str(task.assignee_id).strip() if task.assignee_id else None
        if assignee_id:
            direct_by_id = self.assignee_chat_map_by_id.get(assignee_id)
            if direct_by_id:
                return direct_by_id

        normalized_name = self._normalize_assignee_name(task.assignee)
        if normalized_name:
            if not self._channel_enabled("telegram", task):
                return ()
            direct_by_name = self.assignee_chat_map_by_name.get(normalized_name)
            if direct_by_name:
                return direct_by_name

        if normalized_name in {"alex", "алекс"}:
            alex_chat = self.assignee_chat_map_by_name.get("алекс") or self.assignee_chat_map_by_name.get("alex")
            if alex_chat:
                return alex_chat

        description_lower = getattr(task, "description", "")
        if isinstance(description_lower, str):
            description_lower = description_lower.lower()
            if "алекс" in description_lower or "alex" in description_lower:
                alex_chat = self.assignee_chat_map_by_name.get("алекс") or self.assignee_chat_map_by_name.get("alex")
                if alex_chat:
                    return alex_chat

        return ()

    @staticmethod
    def _voice_prompt(task: ReminderTask) -> str:
        """Compose a short Russian summary for Twilio to read out."""
        due = task.due_human if task.due_human and task.due_human != "Не указан" else "без срока"
        status = task.status or "статус неизвестен"
        return f"Задача {task.name}. Статус: {status}. Срок: {due}."

    def _resolve_callback_log_path(self) -> Optional[Path]:
        telegram_cfg = self.config.get("telegram") or {}
        raw_path = telegram_cfg.get("callback_log_path")
        if raw_path is None:
            return CALLBACK_LOG_PATH
        raw_str = str(raw_path).strip()
        if not raw_str:
            return None
        try:
            return Path(raw_str).expanduser()
        except Exception as exc:  # pragma: no cover - defensive guard
            LOGGER.warning("Failed to resolve callback log path %s: %s", raw_path, exc)
            return CALLBACK_LOG_PATH

    def _append_callback_log(self, entry: Dict[str, Any]) -> None:
        if not self.callback_log_path:
            return
        try:
            self.callback_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._prune_callback_log_if_needed()
            line = json.dumps(entry, ensure_ascii=True)
            with self.callback_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.write("\n")
            if entry.get("result") == "success":
                callback_id = entry.get("callback_id")
                if callback_id:
                    self._processed_callback_ids.add(str(callback_id))
        except Exception as exc:  # pragma: no cover - best effort
            LOGGER.debug("Failed to append callback log: %s", exc)

    def _resolve_callback_log_max_bytes(self) -> int:
        raw_limit = (self.telegram_config or {}).get("callback_log_max_mb")
        if raw_limit is None:
            return 5 * 1024 * 1024  # 5 MiB по умолчанию
        try:
            megabytes = float(raw_limit)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid callback_log_max_mb value '%s'. Using default.", raw_limit)
            return 5 * 1024 * 1024
        return max(int(megabytes * 1024 * 1024), 0)

    def _resolve_callback_log_max_entries(self) -> int:
        raw_limit = (self.telegram_config or {}).get("callback_log_max_entries")
        if raw_limit is None:
            return 5000
        try:
            value = int(raw_limit)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid callback_log_max_entries value '%s'. Using default.", raw_limit)
            return 5000
        return max(value, 0)

    def _prune_callback_log_if_needed(self) -> None:
        if not self.callback_log_path or not self.callback_log_path.exists():
            return
        if self._callback_log_max_bytes <= 0:
            try:
                self.callback_log_path.unlink()
            except OSError as exc:
                LOGGER.debug("Failed to remove callback log: %s", exc)
            return
        try:
            current_size = self.callback_log_path.stat().st_size
        except OSError as exc:
            LOGGER.debug("Failed to stat callback log: %s", exc)
            return
        if current_size <= self._callback_log_max_bytes:
            return

        if self._callback_log_max_entries <= 0:
            try:
                self.callback_log_path.unlink()
            except OSError as exc:
                LOGGER.debug("Failed to truncate callback log: %s", exc)
            return

        try:
            with self.callback_log_path.open("r", encoding="utf-8") as fh:
                tail = deque(fh, maxlen=self._callback_log_max_entries)
        except Exception as exc:
            LOGGER.debug("Failed to read callback log for pruning: %s", exc)
            return

        temp_path = self.callback_log_path.with_suffix(self.callback_log_path.suffix + ".tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as fh:
                fh.writelines(tail)
            temp_path.replace(self.callback_log_path)
        except Exception as exc:
            LOGGER.debug("Failed to rotate callback log: %s", exc)
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:  # pragma: no cover - best effort cleanup
                pass

    def _load_processed_callback_ids(self) -> set[str]:
        if not self.callback_log_path:
            return set()
        try:
            lines = self.callback_log_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return set()
        except Exception as exc:
            LOGGER.debug("Failed to prime processed callback cache: %s", exc)
            return set()

        processed: set[str] = set()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("result") != "success":
                continue
            callback_id = entry.get("callback_id")
            if callback_id:
                processed.add(str(callback_id))
        return processed

    def _is_callback_processed(self, callback_id: Optional[str]) -> bool:
        if not callback_id:
            return False
        return str(callback_id) in self._processed_callback_ids

    def ensure_callback_comments(self, max_entries: int = 20) -> None:
        """
        Verify that recent successful callback entries have a corresponding ClickUp comment.

        Raises:
            RuntimeError: if any recent task lacks the expected audit comment.
        """
        if not self.callback_log_path:
            LOGGER.debug("Callback log path is not configured; skipping comment verification.")
            return

        try:
            lines = self.callback_log_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            LOGGER.debug("Callback log file %s not found; skipping comment verification.", self.callback_log_path)
            return
        except Exception as exc:
            LOGGER.warning("Failed to read callback log %s: %s", self.callback_log_path, exc)
            return

        if not lines:
            LOGGER.debug("Callback log file %s is empty; nothing to verify.", self.callback_log_path)
            return

        recent_entries: Dict[str, Dict[str, Any]] = {}
        for line in reversed(lines):
            if len(recent_entries) >= max_entries:
                break
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                LOGGER.debug("Skipping malformed callback log line: %s", line)
                continue
            if entry.get("result") != "success":
                continue
            task_id = str(entry.get("task_id") or "").strip()
            if not task_id:
                continue
            if task_id not in recent_entries:
                recent_entries[task_id] = entry

        if not recent_entries:
            LOGGER.debug("No successful callback entries found for verification.")
            return

        missing: List[str] = []
        expected_fragment = "Статус обновлен через Telegram-бота"

        for task_id, entry in recent_entries.items():
            matched = False
            for client in self.clickup_clients:
                try:
                    comments = client.fetch_comments(task_id)
                except Exception as exc:
                    LOGGER.debug("Failed to fetch comments for task %s via team %s: %s", task_id, client.team_id, exc)
                    continue
                for comment in comments:
                    comment_text = comment.get("comment_text") or comment.get("text") or ""
                    if expected_fragment in comment_text:
                        status_suffix = entry.get("status_key")
                        if not status_suffix or f"{expected_fragment}: {status_suffix}" in comment_text:
                            matched = True
                            break
                        if expected_fragment in comment_text and not status_suffix:
                            matched = True
                            break
                if matched:
                    break
            if not matched:
                missing.append(task_id)

        if missing:
            raise RuntimeError(
                "Не найдены комментарии с отметкой Telegram для задач: " + ", ".join(sorted(set(missing)))
            )

    # --------------------------------------------------------------------- #
    # ClickUp helpers
    # --------------------------------------------------------------------- #
    def fetch_pending_tasks(self, limit: Optional[int] = None) -> List[ReminderTask]:
        """Return tasks from ClickUp that match the reminder filters and are not completed."""
        try:
            pending = self.engine.fetch_pending_reminders()
            if limit:
                pending = pending[:limit]
            return pending
        except Exception as exc:
            LOGGER.error("Failed to fetch tasks from ClickUp via engine: %s", exc)
            return []

    def update_clickup_status(self, task_id: str, status_key: str) -> None:
        """Update ClickUp task status using the engine."""
        status = self.status_mapping.get(status_key)
        if not status:
            LOGGER.error("Unknown status key: %s (mapping=%s)", status_key, self.status_mapping)
            return

        success = self.engine.update_task_status(task_id, status)
        if not success:
            LOGGER.error("Failed to update ClickUp status for %s", task_id)

    def fetch_task_details(self, task_id: str) -> Dict[str, Any]:
        try:
            return self.clickup_client.fetch_task(task_id)
        except Exception as exc:
            LOGGER.warning("Failed to fetch task %s details: %s", task_id, exc)
            return {"id": task_id, "name": f"Задача {task_id}"}

    # --------------------------------------------------------------------- #
    # Telegram helpers
    # --------------------------------------------------------------------- #
    def _telegram_post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/{method}"
        response = self.session.post(url, json=payload, timeout=15)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:  # pragma: no cover - network guard
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = response.text
            LOGGER.error("Telegram API error (%s): %s | payload=%s", method, exc, error_payload)
            raise
        data = response.json()
        if not data.get("ok"):
            LOGGER.error("Telegram API call %s failed: %s", method, data)
        return data

    def send_plain_message(self, chat_id: str, text: str) -> Dict[str, Any]:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        return self._telegram_post("sendMessage", payload)

    def send_task_message(self, chat_id: str, task: ReminderTask, ordinal: int) -> Dict[str, Any]:
        text = format_task_message(task, ordinal)
        
        telegram_cfg = self.config.get("telegram") or {}
        buttons_per_row = telegram_cfg.get("buttons_per_row", 3)
        
        reply_markup = build_task_keyboard(
            task_id=task.task_id,
            status_actions=self.status_actions,
            buttons_per_row=buttons_per_row,
            shortcuts=self.chat_shortcuts
        )
        
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
            "disable_web_page_preview": True,
        }
        return self._telegram_post("sendMessage", payload)

    def remove_inline_keyboard(self, chat_id: str, message_id: int) -> None:
        self._telegram_post(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )

    def answer_callback(self, callback_id: str, text: str, show_alert: bool = False) -> None:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        self._telegram_post("answerCallbackQuery", payload)

    def _group_tasks_by_chat(
        self,
        tasks: Sequence[ReminderTask],
    ) -> Dict[str, List[ReminderTask]]:
        deliveries: Dict[str, List[ReminderTask]] = {}

        for task in tasks:
            chat_ids = self._chat_targets_for_task(task)
            if not chat_ids:
                LOGGER.warning(
                    "No Telegram chat mapping for assignee '%s' (id=%s, task %s). Skipping.",
                    task.assignee or "—",
                    task.assignee_id or "—",
                    task.task_id,
                )
                continue

            seen: set[str] = set()
            for chat_id in chat_ids:
                chat_candidate = str(chat_id).strip()
                if not chat_candidate or chat_candidate in seen:
                    continue
                deliveries.setdefault(chat_candidate, []).append(task)
                seen.add(chat_candidate)

        return deliveries

    def _dispatch_tasks_to_chat(self, chat_id: str, tasks: Sequence[ReminderTask]) -> None:
        if not chat_id:
            return

        self._ensure_default_chat(chat_id)

        if not tasks:
            try:
                self.send_plain_message(chat_id, "✅ На данный момент нет задач, требующих внимания.")
            except Exception as exc:  # pragma: no cover - network guard
                LOGGER.error("Failed to send empty-state message to %s: %s", chat_id, exc)
            return

        preface = (
            f"📌 Найдено задач: {len(tasks)}. "
            "Отметьте статус прямо в боте — выбор обновит задачу в ClickUp."
        )
        try:
            self.send_plain_message(chat_id, preface)
        except Exception as exc:  # pragma: no cover - network guard
            LOGGER.error("Failed to send preface to chat %s: %s", chat_id, exc)
            return

        for idx, task in enumerate(tasks, start=1):
            try:
                self.send_task_message(chat_id, task, idx)
            except Exception as exc:  # pragma: no cover - network guard
                LOGGER.error("Failed to send task %s to Telegram chat %s: %s", task.task_id, chat_id, exc)

    def send_reminders(
        self,
        chat_id: Optional[str] = None,
        limit: Optional[int] = None,
        broadcast_all: bool = False,
    ) -> List[ReminderTask]:
        all_tasks = self.fetch_pending_tasks(limit=limit)
        telegram_tasks = [task for task in all_tasks if self._channel_enabled("telegram", task)]

        if chat_id is not None:
            target_chat = self._resolve_target_chat(chat_id)
            if not target_chat:
                raise ConfigurationError(
                    "Chat id not supplied. Отправьте /start боту или передайте --chat-id для send_telegram_reminders.py."
                )
            if broadcast_all:
                target_chat_str = str(target_chat)
                deliveries = {target_chat_str: list(telegram_tasks)} if telegram_tasks else {}
                self._update_delivery_stats(
                    deliveries,
                    len(telegram_tasks),
                    requested_chat=target_chat_str,
                    broadcast_all=True,
                )
                self._dispatch_tasks_to_chat(target_chat_str, telegram_tasks)
                return telegram_tasks

            target_chat_str = str(target_chat)
            deliveries = self._group_tasks_by_chat(telegram_tasks)
            bucket = deliveries.get(target_chat_str, [])

            filtered_deliveries = {target_chat_str: bucket} if bucket else {}
            self._update_delivery_stats(
                filtered_deliveries,
                len(bucket),
                requested_chat=target_chat_str,
                broadcast_all=False,
            )
            self._dispatch_tasks_to_chat(target_chat_str, bucket)
            return telegram_tasks

        if not telegram_tasks:
            LOGGER.info("No pending tasks to send.")
            self._update_delivery_stats({}, 0, requested_chat=None, broadcast_all=True)
            return []

        deliveries = {
            chat: [task for task in bucket if self._channel_enabled("telegram", task)]
            for chat, bucket in self._group_tasks_by_chat(telegram_tasks).items()
        }
        deliveries = {chat: bucket for chat, bucket in deliveries.items() if bucket}
        self._update_delivery_stats(
            deliveries,
            len(telegram_tasks),
            requested_chat=None,
            broadcast_all=True,
        )
        if not deliveries:
            LOGGER.warning(
                "Не удалось сопоставить ни одну задачу с Telegram чатами исполнителей. "
                "Проверьте telegram.assignee_chat_map в config.json."
            )
            return []

        for target_chat, bucket in deliveries.items():
            self._dispatch_tasks_to_chat(target_chat, bucket)

        return telegram_tasks

    def send_voice_reminders(
        self,
        assignees: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Place Twilio voice calls for pending reminders grouped by phone mapping.

        Args:
            assignees: Optional iterable of assignee names to target (case-insensitive).
            limit: Optional max amount of tasks to pull from ClickUp.
            dry_run: When True, skip actual Twilio API calls and only log the plan.
        """
        if not self.twilio_service or not self.twilio_from_phone:
            raise ConfigurationError(
                "Twilio credentials are not configured. "
                "Set TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_PHONE_NUMBER or provide twilio.* in secrets."
            )
        if not self.phone_mapping:
            raise ConfigurationError("phone_mapping in config.json is empty — нечего прозванивать.")

        allowed_assignees: Optional[set[str]] = None
        if assignees:
            allowed_assignees = set()
            for name in assignees:
                normalized = self._normalize_assignee_name(name)
                if normalized:
                    allowed_assignees.add(normalized)
            if not allowed_assignees:
                raise ConfigurationError(
                    "Не удалось сопоставить указанных исполнителей с phone_mapping. Проверьте написание имён."
                )

        tasks = [task for task in self.fetch_pending_tasks(limit=limit) if self._channel_enabled("twilio", task)]
        if not tasks:
            LOGGER.info("Нет задач для голосовых напоминаний.")
            return []

        grouped: Dict[str, List[ReminderTask]] = {}
        skipped: List[str] = []

        for task in tasks:
            normalized = self._normalize_assignee_name(task.assignee)
            recipient_alias = self._resolve_twilio_recipient(task)

            candidate_aliases = {alias for alias in (normalized, recipient_alias) if alias}
            if allowed_assignees is not None and (
                not candidate_aliases or candidate_aliases.isdisjoint(allowed_assignees)
            ):
                continue

            phone_key = recipient_alias or normalized
            if not phone_key:
                skipped.append(task.assignee or task.task_id)
                continue

            phone = self.phone_mapping.get(phone_key)
            if not phone and recipient_alias and normalized and normalized != recipient_alias:
                phone = self.phone_mapping.get(normalized)

            if not phone:
                skipped.append(task.assignee or task.task_id)
                continue

            grouped.setdefault(phone, []).append(task)

        if not grouped:
            LOGGER.info("Не найдено задач с телефонными номерами для голосовых напоминаний.")
            if skipped:
                LOGGER.debug(
                    "Пропущено %s задач без phone_mapping (пример: %s)",
                    len(skipped),
                    ", ".join(sorted(set(skipped))[:3]),
                )
            return []

        if skipped:
            LOGGER.debug(
                "Пропущено %s задач без phone_mapping (пример: %s)",
                len(skipped),
                ", ".join(sorted(set(skipped))[:3]),
            )

        attempted_calls = 0
        call_failures = 0
        deliveries: List[Dict[str, Any]] = []
        for phone, bucket in grouped.items():
            messages = [self._voice_prompt(task) for task in bucket]
            result = None
            if dry_run:
                LOGGER.info(
                    "Dry-run: пропущен звонок на %s (задач: %s)",
                    phone,
                    len(bucket),
                )
            else:
                result = self.twilio_service.make_call(
                    from_phone=self.twilio_from_phone,
                    to_phone=phone,
                    task_messages=messages,
                )
                status = "успех" if result.success else f"ошибка ({result.error or result.status})"
                LOGGER.info(
                    "Twilio звонок на %s — %s (задач: %s)",
                    phone,
                    status,
                    len(bucket),
                )

            call_result = {
                "success": getattr(result, "success", True),
                "status": getattr(result, "status", "unknown"),
                "sid": getattr(result, "sid", None),
            }
            deliveries.append({
                "phone": phone,
                "assignees": [task.assignee for task in bucket],
                "call_result": call_result,
            })
            attempted_calls += 1
            if not call_result["success"]:
                LOGGER.warning(
                    "Twilio call for %s reported failure: %s",
                    phone,
                    call_result.get("status") or call_result.get("error"),
                )
                call_failures += 1

        self.register_voice_results(attempted_calls, call_failures)
        return deliveries

    def _register_sms_results(self, sent: int, failed: int) -> None:
        if not self._last_delivery_stats:
            return
        self._last_delivery_stats.sms_sent += max(sent, 0)
        if failed:
            self._register_failed_action(f"SMS failures: {failed}")

    def send_sms_reminders(self, tasks: Sequence[ReminderTask]) -> List[Dict[str, Any]]:
        if not tasks:
            return []
        if not self.twilio_service or not self.twilio_from_phone:
            raise ConfigurationError("Twilio не настроен. SMS отправка невозможна.")

        grouped: Dict[str, List[ReminderTask]] = {}
        for task in tasks:
            if not self.task_requires_channel(task, "sms"):
                continue
            recipient = self._resolve_twilio_recipient(task)
            if not recipient:
                LOGGER.debug("Пропущено SMS: нет получателя для %s", task.task_id)
                continue
            phone = self.phone_mapping.get(recipient)
            if not phone:
                LOGGER.debug("Пропущено SMS: нет номера для %s", recipient)
                continue
            grouped.setdefault(phone, []).append(task)

        if not grouped:
            return []

        successful = 0
        failures = 0
        deliveries: List[Dict[str, Any]] = []
        for phone, bucket in grouped.items():
            lines = ["📋 Напоминания:"]
            for task in bucket:
                lines.append(f"- {task.name} (до {task.due_human})")
            body = "\n".join(lines)
            result = self.twilio_service.send_sms(self.twilio_from_phone, phone, body)
            deliveries.append({
                "phone": phone,
                "tasks": [task.task_id for task in bucket],
                "result": result,
            })
            if result.success:
                successful += 1
            else:
                failures += 1
                LOGGER.warning("SMS delivery to %s failed: %s", phone, result.error)

        self._register_sms_results(successful, failures)
        return deliveries

    def poll_updates_for(
        self,
        duration: float,
        poll_interval: float = 1.0,
        timeout: int = 10,
    ) -> int:
        """
        Poll Telegram updates for a limited duration to process callbacks/messages.

        Returns the amount of processed updates.
        """
        if duration <= 0:
            return 0

        self._ensure_webhook_cleared()

        deadline = time.monotonic() + duration
        offset: Optional[int] = None
        processed = 0

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            effective_timeout = min(timeout, max(1, int(remaining)))
            try:
                updates = self.get_updates(offset=offset, timeout=effective_timeout)
            except Exception as exc:  # pragma: no cover - network guard
                LOGGER.warning("Polling updates failed: %s", exc)
                sleep_for = min(poll_interval, max(0.1, deadline - time.monotonic()))
                time.sleep(sleep_for)
                continue

            if not updates:
                time.sleep(min(poll_interval, max(0.1, deadline - time.monotonic())))
                continue

            last_update_id: Optional[int] = offset
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    last_update_id = update_id

                if "message" in update:
                    self.handle_message(update["message"])
                    processed += 1
                elif "callback_query" in update:
                    self.handle_callback(update["callback_query"])
                    processed += 1
                else:
                    LOGGER.debug("Ignored update keys: %s", list(update.keys()))

            if last_update_id is not None:
                offset = last_update_id + 1

        if processed:
            LOGGER.info("Processed %s callback(s) from Telegram.", processed)
            self.register_callback_metrics(processed)
        return processed

    # ------------------------------------------------------------------ #
    # Update handlers
    # ------------------------------------------------------------------ #
    def handle_message(self, message: Dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id"))
        text = (message.get("text") or "").strip().lower()

        if chat_id and not self._configured_default_chat:
            self.default_chat_id = chat_id
            self._persist_chat_id(chat_id)

        if text in ("/start", "start", "/remind", "напомни", "напоминания"):
            self.send_plain_message(
                chat_id,
                "👋 Привет! Я бот напоминаний. Вот список актуальных задач:",
            )
            self.send_reminders(chat_id=chat_id)
        elif text in ("/help", "help", "помощь"):
            self.send_plain_message(
                chat_id,
                "ℹ️ Используйте /start, чтобы получить текущие напоминания и изменить статус задач кнопками.",
            )
        else:
            self.send_plain_message(
                chat_id,
                "⚠️ Неизвестная команда. Отправьте /start, чтобы получить список напоминаний.",
            )

    def handle_callback(self, callback: Dict[str, Any]) -> None:
        data = callback.get("data") or ""
        callback_id = callback.get("id")
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        raw_chat_id = chat.get("id")
        chat_id = str(raw_chat_id) if raw_chat_id is not None else ""
        message_id = message.get("message_id")

        if self._is_callback_processed(callback_id):
            LOGGER.debug("Callback %s already processed; skipping duplicate update.", callback_id)
            return

        if not data.startswith("s:") or data.count(":") != 2:
            if callback_id:
                self.answer_callback(callback_id, "Неизвестное действие", show_alert=True)
            return

        _, task_id, action_code = data.split(":")
        action = self.status_action_map.get(action_code)
        if not action:
            if callback_id:
                self.answer_callback(callback_id, "Действие не поддерживается", show_alert=True)
            return
        operation = action.get("operation")
        postpone_hours_raw = action.get("postpone_hours")
        postpone_hours: Optional[float] = None
        if postpone_hours_raw is not None:
            try:
                postpone_hours = float(postpone_hours_raw)
            except (TypeError, ValueError):
                postpone_hours = None

        status_key = action["key"]

        is_postpone_action = (operation == "postpone") and postpone_hours and postpone_hours > 0

        actor = callback.get("from") or {}
        base_log_entry: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "task_id": task_id,
            "callback_id": callback_id,
            "action_code": action_code,
            "status_key": status_key,
            "chat_id": str(raw_chat_id) if raw_chat_id is not None else "",
            "message_id": message_id,
            "user_id": actor.get("id"),
            "username": actor.get("username"),
            "first_name": actor.get("first_name"),
            "last_name": actor.get("last_name"),
        }

        if chat_id and not self._configured_default_chat:
            self.default_chat_id = chat_id
            self._persist_chat_id(chat_id)

        try:
            if is_postpone_action:
                self._postpone_task_due_date(task_id, postpone_hours)
            else:
                self.update_clickup_status(task_id, status_key)
        except Exception as exc:
            LOGGER.error("Failed to process workflow action for task %s: %s", task_id, exc)
            error_log = dict(base_log_entry)
            error_log["result"] = "error"
            error_log["error"] = str(exc)
            self._append_callback_log(error_log)
            actor_label = self._format_actor_label(actor)
            failure_entry = f"{actor_label or 'неизвестный пользователь'} • {task_id}: {exc}"
            self._register_failed_action(failure_entry)
            if callback_id:
                try:
                    self.answer_callback(
                        callback_id,
                        "❌ Не удалось выполнить действие",
                        show_alert=True,
                    )
                except Exception:  # pragma: no cover - best effort
                    pass
            if chat_id:
                status_message = "перенести задачу" if is_postpone_action else "обновить задачу"
                self.send_plain_message(
                    chat_id,
                    f"⚠️ Не удалось {status_message} <b>{task_id}</b>: {exc}",
                )
            group_chat = self.resolve_summary_chat()
            if group_chat:
                try:
                    self.send_plain_message(
                        group_chat,
                        (
                            "⚠️ Ошибка при обработке действия пользователя:\n"
                            f"• Задача: <b>{task_id}</b>\n"
                            f"• Пользователь: {actor_label or 'неизвестно'}\n"
                            f"• Действие: {status_key}\n"
                            f"• Ошибка: {exc}"
                        ),
                    )
                except Exception:  # pragma: no cover - best effort
                    LOGGER.debug("Failed to notify group about callback failure: %s", exc)
            return

        if not chat_id:
            chat_id = self._resolve_target_chat()

        if raw_chat_id is not None and chat_id and message_id:
            try:
                self.remove_inline_keyboard(chat_id, message_id)
            except Exception as exc:  # pragma: no cover - best effort
                LOGGER.debug("Failed to clear inline keyboard for message %s: %s", message_id, exc)

        success_log = dict(base_log_entry)
        success_log["chat_id"] = str(chat_id) if chat_id else success_log["chat_id"]
        success_log["result"] = "success"
        if is_postpone_action and postpone_hours:
            success_log["postpone_hours"] = postpone_hours
        self._append_callback_log(success_log)

        if not chat_id:
            LOGGER.warning("No chat id available to notify about task %s update", task_id)
            return

        if callback_id:
            try:
                self.answer_callback(callback_id, "Готово!")
            except Exception as exc:  # pragma: no cover - network guard
                LOGGER.debug("Failed to send callback ack for task %s: %s", task_id, exc)

        task_payload = self.fetch_task_details(task_id)
        task_name = task_payload.get("name", f"Задача {task_id}")

        if is_postpone_action and postpone_hours:
            due_raw = task_payload.get("due_date")
            due_formatted = _format_due(due_raw, self.timezone_name)
            hours_display = int(postpone_hours) if float(postpone_hours).is_integer() else postpone_hours
            self.send_plain_message(
                chat_id,
                (
                    f"⏱ Перенос выполнен! Задача <b>{task_name}</b> перенесена ещё на"
                    f" <b>{hours_display}</b> ч. Новый срок: <b>{due_formatted}</b>"
                ),
            )
        else:
            self.send_plain_message(
                chat_id,
                (
                    f"✅ Статус обновлён: <b>{task_name}</b> теперь <b>{status_key}</b>."
                ),
            )

        actor_label = self._format_actor_label(actor)
        action_label = "перенёс задачу" if is_postpone_action else f"изменил статус на {status_key}"
        notification_entry = f"{actor_label or 'неизвестный пользователь'} • {task_name} — {action_label}"
        self._register_user_action(notification_entry)


    def _postpone_task_due_date(
        self,
        task_id: str,
        postpone_hours: Optional[float],
    ) -> None:
        if not postpone_hours or postpone_hours <= 0:
            raise ValueError("postpone_hours must be positive")

        payload = self.fetch_task_details(task_id)
        due_raw = payload.get("due_date")
        due_time_raw = payload.get("due_date_time")
        timezone_name = self.timezone_name or "UTC"
        timezone = pytz.timezone(timezone_name)

        if due_raw:
            try:
                current_due = datetime.fromtimestamp(int(due_raw) / 1000, timezone)
            except Exception as exc:
                LOGGER.warning("Failed to parse existing due date for task %s: %s", task_id, exc)
                current_due = None
        else:
            current_due = None

        now = datetime.now(timezone)
        if current_due:
            base_due = current_due
        else:
            base_due = now
        new_due = base_due + timedelta(hours=postpone_hours)
        new_due_ms = int(new_due.timestamp() * 1000)

        has_due_time = False
        if isinstance(due_time_raw, bool):
            has_due_time = due_time_raw
        elif isinstance(due_time_raw, (int, float)):
            has_due_time = bool(due_time_raw)
        elif isinstance(due_time_raw, str):
            has_due_time = due_time_raw not in {"0", "false", "False", ""}

        update_payload: Dict[str, Any] = {"due_date": new_due_ms}
        if has_due_time or current_due:
            update_payload["due_date_time"] = True

        self.clickup_client.update_task(task_id, update_payload)

        try:
            self.clickup_client.add_comment(
                task_id,
                f"Дедлайн перенесён через Telegram-бота на {postpone_hours} ч, новый срок: {new_due.strftime('%Y-%m-%d %H:%M %Z')}",
            )
        except Exception as exc:
            LOGGER.warning("Failed to add postpone comment for %s: %s", task_id, exc)

    def get_updates(self, offset: Optional[int] = None, timeout: int = 30) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        response = self.session.post(f"{self.base_url}/getUpdates", json=payload, timeout=timeout + 5)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            LOGGER.error("getUpdates failed: %s", data)
            return []
        return data.get("result", [])
