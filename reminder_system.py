#!/usr/bin/env python3
"""
ClickUp Reminder System with Twilio and OpenAI
Автоматическая система напоминаний о задачах
Version 5.0 - Исправленная версия без webhook
"""

import os
import sys
import json
import random
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pytz
import requests
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import openai
from openai import OpenAI

# Use unified config/secrets modules
try:
    from .config import load_config as load_cfg
    from .secrets import load_secrets as load_secs
    from .telegram_notifier import create_telegram_notifier
except ImportError:
    from config import load_config as load_cfg
    from secrets import load_secrets as load_secs
    from telegram_notifier import create_telegram_notifier

BASE_DIR = os.getenv("BASE_DIR") or os.path.dirname(os.path.abspath(__file__))

# Centralize artifacts under var/
VAR_DIR = os.path.join(BASE_DIR, "var")
LOG_PATH = os.path.join(VAR_DIR, "logs")
TRANSCRIPTIONS_DIR = os.path.join(VAR_DIR, "transcriptions")
CALL_DATA_DIR = os.path.join(VAR_DIR, "call_data")
RECORDINGS_DIR = os.path.join(VAR_DIR, "recordings")
SMS_CODES_FILE = os.path.join(VAR_DIR, "sms_codes.json")
COMPLETED_TASKS_FILE = os.path.join(VAR_DIR, "completed_tasks.json")

# Создаем необходимые директории
os.makedirs(LOG_PATH, exist_ok=True)
os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
os.makedirs(CALL_DATA_DIR, exist_ok=True)
os.makedirs(RECORDINGS_DIR, exist_ok=True)


class ReminderSystem:
    """Основной класс системы напоминаний"""
    
    def __init__(self):
        """Инициализация системы"""
        # Load unified config/secrets
        self.config = self._load_config()
        self.secrets = self._load_secrets()
        
        clickup_config = self.config.get("clickup", {})

        # Ключи ClickUp
        self.clickup_token = self._get_secret_value("clickup", ["api_key", "clickup_api_key"])
        if not self.clickup_token:
            # Исторический путь через telegram-секцию
            self.clickup_token = self._get_secret_value("telegram", ["clickup_api_key"])
        if not self.clickup_token:
            raise KeyError("ClickUp API key not found in secrets")
        
        # OpenAI
        self.openai_key = self._get_secret_value("openai", ["api_key"])
        if not self.openai_key:
            raise KeyError("OpenAI API key not found in secrets")
        
        # Twilio credentials - use from secrets if available, fallback to config
        self.twilio_account_sid = self._get_secret_value("twilio", ["account_sid"])
        self.twilio_auth_token = self._get_secret_value("twilio", ["auth_token"])
        self.twilio_phone = self._get_secret_value("twilio", ["phone_number"])
        if not (self.twilio_account_sid and self.twilio_auth_token and self.twilio_phone):
            twilio_config = self.config.get("twilio", {})
            self.twilio_account_sid = self.twilio_account_sid or twilio_config.get("account_sid")
            self.twilio_auth_token = self.twilio_auth_token or twilio_config.get("auth_token")
            self.twilio_phone = self.twilio_phone or twilio_config.get("phone_number")
        
        if not all([self.twilio_account_sid, self.twilio_auth_token, self.twilio_phone]):
            raise KeyError("Twilio credentials not found in secrets or config")
        
        self.twilio_client = Client(
            self.twilio_account_sid,
            self.twilio_auth_token
        )
        
        # OpenAI
        openai.api_key = self.openai_key
        self.openai_client = OpenAI(api_key=self.openai_key)
        
        # Timezone
        self.tz = pytz.timezone(self.config.get("working_hours", {}).get("timezone", "Europe/Lisbon"))

        # Mapping between AI status labels and ClickUp list statuses
        raw_mapping = clickup_config.get("status_mapping", {})
        self.status_mapping = {key.upper(): value for key, value in raw_mapping.items()}
        
        # Устанавливаем дефолтные значения для всех возможных статусов
        self.status_mapping.setdefault("ВЫПОЛНЕНО", clickup_config.get("completed_status", "complete"))
        self.status_mapping.setdefault("НЕ_ВЫПОЛНЕНО", clickup_config.get("pending_status", "to do"))
        self.status_mapping.setdefault("В_РАБОТЕ", clickup_config.get("in_progress_status", "in progress"))
        self.status_mapping.setdefault("НЕЯСНО", clickup_config.get("unclear_status", "to do"))
        self.status_mapping.setdefault("ПЕРЕЗВОНИТЬ", clickup_config.get("callback_status", "to do"))
        
        # Инициализация Telegram notifier
        self.telegram = create_telegram_notifier(self.config, self.secrets)
        if self.telegram:
            self._log("Telegram уведомления включены")
        else:
            self._log("Telegram уведомления отключены")
    
    def _load_config(self) -> Dict:
        """Загрузка конфигурации через единый модуль config.load_config"""
        try:
            cfg_obj = load_cfg()
            # adapt to dict for existing usages
            clickup_section = {
                "reminders_list_name": getattr(cfg_obj, "reminder_list_name", "Напоминания"),
            }
            workspace_id = getattr(cfg_obj, "clickup_workspace_id", None)
            if workspace_id:
                clickup_section["workspace_id"] = workspace_id
                clickup_section.setdefault("team_id", workspace_id)

            # Безопасное получение working_hours
            working_hours_obj = getattr(cfg_obj, "working_hours", None)
            if working_hours_obj:
                working_hours = {
                    "timezone": getattr(working_hours_obj, "timezone", "Europe/Lisbon"),
                    "start": getattr(working_hours_obj, "start", 10),
                    "end": getattr(working_hours_obj, "end", 18),
                    "working_days": getattr(working_hours_obj, "working_days", [0,1,2,3,4]),
                }
            else:
                working_hours = {
                    "timezone": "Europe/Lisbon",
                    "start": 10,
                    "end": 18,
                    "working_days": [0,1,2,3,4],
                }

            # Получаем Telegram конфигурацию
            telegram_obj = getattr(cfg_obj, "telegram", None)
            telegram_config = {}
            if telegram_obj:
                telegram_config = {
                    "enabled": getattr(telegram_obj, "enabled", False),
                    "chat_id": getattr(telegram_obj, "chat_id", None),
                    "notifications": getattr(telegram_obj, "notifications", {}),
                }

            return {
                "clickup": clickup_section,
                "working_hours": working_hours,
                "contacts": getattr(cfg_obj, "phone_mapping", {}),
                "voice_settings": {"language": "ru-RU"},
                "twilio": {},
                "telegram": telegram_config,
            }
        except Exception:
            # fallback to legacy local file format
            path = os.path.join(BASE_DIR, "config.json")
            with open(path, 'r', encoding='utf-8') as f:
                legacy_config = json.load(f)
                # Маппинг phone_mapping -> contacts для совместимости
                if "phone_mapping" in legacy_config and "contacts" not in legacy_config:
                    legacy_config["contacts"] = legacy_config["phone_mapping"]
                return legacy_config
    
    def _load_secrets(self) -> Dict:
        """Единая загрузка секретов через secrets.load_secrets"""
        try:
            s = load_secs()
            secrets_dict = {
                "clickup": {"api_key": s.clickup_api_key, "team_id": s.clickup_team_id},
                "openai": {"api_key": s.openai_api_key},
                "twilio": {
                    "account_sid": s.twilio_sid,
                    "auth_token": s.twilio_token,
                    "phone_number": s.twilio_phone,
                },
            }
            return secrets_dict
        except Exception:
            # fallback to legacy secrets path if present
            path = os.getenv("SECRETS_PATH") or os.path.join(os.path.dirname(BASE_DIR), ".venv", "bin", "secrets.json")
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)

    def _get_secret_value(self, section_name: str, candidate_keys: List[str]) -> Optional[str]:
        """Возвращает значение секрета с учётом старых и новых форматов."""
        section = self.secrets.get(section_name)
        if not isinstance(section, dict):
            return None

        # Формат {"secrets": {"key": {"value": "..."} } }
        nested = section.get("secrets")
        sources = []
        if isinstance(nested, dict):
            sources.append(nested)
        sources.append(section)

        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in candidate_keys:
                if key in source:
                    value = source[key]
                    if isinstance(value, dict):
                        if "value" in value:
                            return value["value"]
                    else:
                        return value
        return None
    
    def _store_transcription(self, call_sid: str, transcription_text: str, status: str):
        """Сохраняет локальную копию транскрипции для дальнейшего анализа."""
        try:
            data = {
                "call_sid": call_sid,
                "transcription_text": transcription_text,
                "status": status,
                "timestamp": datetime.now(self.tz).isoformat()
            }
            os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
            path = os.path.join(TRANSCRIPTIONS_DIR, f"{call_sid}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._log(f"Ошибка сохранения транскрипции: {exc}", "ERROR")

    def _load_completed_tasks(self) -> Dict[str, Dict]:
        """Загружает список выполненных задач из файла."""
        try:
            if os.path.exists(COMPLETED_TASKS_FILE):
                with open(COMPLETED_TASKS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}
        except Exception as exc:
            self._log(f"Ошибка загрузки выполненных задач: {exc}", "ERROR")
            return {}
    
    def _save_completed_tasks(self, completed_tasks: Dict[str, Dict]):
        """Сохраняет список выполненных задач в файл."""
        try:
            with open(COMPLETED_TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(completed_tasks, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._log(f"Ошибка сохранения выполненных задач: {exc}", "ERROR")
    
    def _mark_task_completed(self, task_id: str, task_name: str):
        """Отмечает задачу как выполненную."""
        completed_tasks = self._load_completed_tasks()
        completed_tasks[task_id] = {
            "name": task_name,
            "completed_at": datetime.now(self.tz).isoformat()
        }
        self._save_completed_tasks(completed_tasks)
        self._log(f"Задача {task_id} ({task_name}) отмечена как выполненная")
    
    def _is_task_completed(self, task_id: str) -> bool:
        """Проверяет, была ли задача уже выполнена."""
        completed_tasks = self._load_completed_tasks()
        return task_id in completed_tasks

    @staticmethod
    def _parse_time_string(value: Optional[str], default: Tuple[int, int]) -> Tuple[int, int]:
        """Парсинг строки времени формата HH:MM с запасным значением."""
        if not value:
            return default
        try:
            parts = value.split(":")
            if len(parts) != 2:
                raise ValueError("invalid time format")
            hour = int(parts[0])
            minute = int(parts[1])
            return hour, minute
        except (ValueError, TypeError):
            return default
    
    def _is_telegram_notification_enabled(self, notification_type: str) -> bool:
        """Проверяет, включен ли определенный тип Telegram уведомлений"""
        if not self.telegram:
            return False
        
        telegram_config = self.config.get("telegram", {})
        notifications = telegram_config.get("notifications", {})
        
        # По умолчанию все уведомления включены, если секция telegram.enabled = true
        return notifications.get(notification_type, True)
    
    def _log(self, message: str, level: str = "INFO"):
        """Логирование"""
        timestamp = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] [{level}] {message}"
        print(log_message)
        
        # Запись в файл
        log_file = os.path.join(LOG_PATH, f"reminders_{datetime.now(self.tz).strftime('%Y-%m-%d')}.log")
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_message + "\n")
    
    def _is_working_hours(self) -> bool:
        """Проверка рабочего времени"""
        now = datetime.now(self.tz)
        
        working_hours = self.config.get("working_hours", {})
        
        # Проверка дня недели (0 = понедельник)
        working_days = working_hours.get("working_days") or working_hours.get("days", [])
        if working_days and now.weekday() not in working_days:
            return False
        
        # Проверка времени
        start_time = working_hours.get("start")
        end_time = working_hours.get("end")
        
        # Поддержка как числового, так и строкового формата
        if isinstance(start_time, int):
            start_hour, start_minute = start_time, 0
        else:
            start_hour, start_minute = self._parse_time_string(start_time, (10, 0))
        
        if isinstance(end_time, int):
            end_hour, end_minute = end_time, 0
        else:
            end_hour, end_minute = self._parse_time_string(end_time, (18, 0))
        
        start_dt = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end_dt = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        
        return start_dt <= now < end_dt
    
    def _extract_recipient_name(self, task_name: str) -> Optional[str]:
        """Извлечение имени получателя из названия задачи"""
        # Согласно конфигурации, все задачи направляются Alex
        # Возвращаем первого (и единственного) получателя из contacts
        contacts = self.config.get("contacts", {})
        if contacts:
            return list(contacts.keys())[0]
        
        # Fallback: паттерны для извлечения имени из текста задачи
        patterns = [
            r'напомнить\s+(\w+)',
            r'позвонить\s+(\w+)',
            r'связаться\s+с\s+(\w+)',
        ]
        
        task_lower = task_name.lower()
        
        for pattern in patterns:
            match = re.search(pattern, task_lower, re.IGNORECASE)
            if match:
                name = match.group(1).lower()
                # Убираем окончания (Алексу -> Алекс)
                name = re.sub(r'[уаеёоиыэюя]+$', '', name)
                return name
        
        return None
    
    def _get_contact_info(self, recipient_name: str) -> Optional[Dict]:
        """Получение контактной информации из конфига"""
        contacts = self.config.get("contacts", {})
        
        # Новый формат: {"Alex": "+351920524916"}
        # Старый формат: {"alex": {"phone": "+351...", "language": "ru"}}
        
        # Прямой поиск (case-insensitive)
        for name, info in contacts.items():
            if name.lower() == recipient_name.lower():
                # Если info - строка (номер телефона), преобразуем в словарь
                if isinstance(info, str):
                    return {"phone": info, "language": "ru"}
                return info
        
        # Поиск с учетом окончаний
        recipient_lower = recipient_name.lower()
        for name, info in contacts.items():
            name_lower = name.lower()
            if name_lower.startswith(recipient_lower) or recipient_lower.startswith(name_lower):
                if isinstance(info, str):
                    return {"phone": info, "language": "ru"}
                return info
        
        return None
    
    def get_tasks_for_reminder(self) -> List[Dict]:
        """Получение задач для напоминания из ClickUp"""
        self._log("Получение задач из ClickUp...")
        
        headers = {
            "Authorization": self.clickup_token,
            "Content-Type": "application/json"
        }
        
        try:
            # Получаем workspace
            # Support both workspace_id and team_id
            clickup_config = self.config.get("clickup", {})
            workspace_id = (
                clickup_config.get("workspace_id")
                or clickup_config.get("team_id")
                or self.config.get("clickup_workspace_id")  # legacy format
                or self._get_secret_value("clickup", ["team_id"])
            )
            if not workspace_id:
                raise KeyError("ClickUp workspace/team id not configured")
            
            # Получаем spaces
            spaces_url = f"https://api.clickup.com/api/v2/team/{workspace_id}/space?archived=false"
            spaces_response = requests.get(spaces_url, headers=headers)
            spaces_response.raise_for_status()
            spaces = spaces_response.json()["spaces"]
            
            all_tasks = []
            reminders_list_name = (
                clickup_config.get("reminders_list_name")
                or self.config.get("reminder_list_name")  # legacy format
                or "Напоминания"  # default fallback
            )
            
            # Ищем список "Напоминания" во всех spaces
            for space in spaces:
                folders_url = f"https://api.clickup.com/api/v2/space/{space['id']}/folder?archived=false"
                folders_response = requests.get(folders_url, headers=headers)
                folders_response.raise_for_status()
                folders = folders_response.json()["folders"]
                
                # Проверяем folderless списки
                lists_url = f"https://api.clickup.com/api/v2/space/{space['id']}/list?archived=false"
                lists_response = requests.get(lists_url, headers=headers)
                lists_response.raise_for_status()
                lists = lists_response.json()["lists"]
                
                # Проверяем списки в папках
                for folder in folders:
                    folder_lists_url = f"https://api.clickup.com/api/v2/folder/{folder['id']}/list?archived=false"
                    folder_lists_response = requests.get(folder_lists_url, headers=headers)
                    folder_lists_response.raise_for_status()
                    lists.extend(folder_lists_response.json()["lists"])
                
                # Ищем список с нужным именем
                for list_item in lists:
                    if list_item["name"] == reminders_list_name:
                        list_id = list_item["id"]
                        
                        # Получаем задачи из списка
                        tasks_url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
                        params = {
                            "archived": "false",
                            "subtasks": "false",
                            "include_closed": "false"
                        }
                        tasks_response = requests.get(tasks_url, headers=headers, params=params)
                        tasks_response.raise_for_status()
                        tasks = tasks_response.json()["tasks"]
                        
                        # Фильтруем задачи с due_date в прошлом или сейчас
                        now = datetime.now(self.tz)
                        
                        for task in tasks:
                            due_date = task.get("due_date")
                            if due_date:
                                # due_date в миллисекундах
                                due_datetime = datetime.fromtimestamp(int(due_date) / 1000, tz=self.tz)
                                
                                if due_datetime <= now:
                                    # Проверяем, не была ли задача уже выполнена
                                    task_id = task.get("id")
                                    if not self._is_task_completed(task_id):
                                        all_tasks.append(task)
                                    else:
                                        self._log(f"Задача {task_id} ({task.get('name')}) уже выполнена, пропускаем")
            
            self._log(f"Найдено задач для напоминания: {len(all_tasks)}")
            return all_tasks
            
        except Exception as e:
            self._log(f"Ошибка при получении задач: {str(e)}", "ERROR")
            return []
    
    def make_call(self, phone: str, task_name: str, recipient_name: str, task_id: str) -> Tuple[str, Optional[str], str]:
        """
        Совершение звонка через Twilio с воспроизведением сообщения и записью ответа
        
        Returns:
            Tuple[status, call_sid, call_id]: статус звонка, SID звонка, уникальный ID звонка
        """
        # Генерируем уникальный call_id
        call_id = str(uuid.uuid4())
        
        self._log(f"Звонок на {phone} для задачи: {task_name}")
        self._log(f"Call ID: {call_id}")

        voice_settings = self.config.get("voice_settings", {})
        language = voice_settings.get("language", "ru-RU")
        voice = voice_settings.get("voice")

        greeting = f"Привет {recipient_name}! Напоминание о задаче: {task_name}. Задача выполнена? Ответьте после сигнала."

        response = VoiceResponse()
        say_kwargs = {"language": language}
        if voice:
            say_kwargs["voice"] = voice
        response.say(greeting, **say_kwargs)
        response.record(
            play_beep=True,
            max_length=60,
            timeout=5,
            finish_on_key="#"
        )
        response.say("Спасибо! До свидания.", **say_kwargs)

        twiml_payload = str(response)

        try:
            call = self.twilio_client.calls.create(
                to=phone,
                from_=self.twilio_phone,
                twiml=twiml_payload
            )
            
            self._log(f"Звонок инициирован: {call.sid}")
            
            # Отправляем уведомление в Telegram
            if self._is_telegram_notification_enabled("call_notifications"):
                try:
                    self.telegram.send_call_notification(
                        task_name=task_name,
                        assignee=recipient_name,
                        phone=phone,
                        call_status="initiated"
                    )
                except Exception as tg_error:
                    self._log(f"Ошибка отправки Telegram уведомления: {tg_error}", "WARNING")
            
            return "INITIATED", call.sid, call_id
            
        except Exception as e:
            self._log(f"Ошибка при звонке: {str(e)}", "ERROR")
            
            # Отправляем уведомление об ошибке в Telegram
            if self._is_telegram_notification_enabled("errors"):
                try:
                    self.telegram.send_error_notification(
                        error_message=f"Ошибка при звонке на {phone}",
                        context=f"Задача: {task_name}, Ошибка: {str(e)}"
                    )
                except Exception as tg_error:
                    self._log(f"Ошибка отправки Telegram уведомления: {tg_error}", "WARNING")
            
            return "ERROR", None, call_id
    
    def make_batch_call(self, phone: str, tasks: List[Dict], recipient_name: str) -> Tuple[str, Optional[str], str]:
        """
        Совершение звонка с напоминанием о нескольких задачах сразу
        
        Args:
            phone: номер телефона
            tasks: список задач для напоминания
            recipient_name: имя получателя
            
        Returns:
            Tuple[status, call_sid, call_id]: статус звонка, SID звонка, уникальный ID звонка
        """
        # Генерируем уникальный call_id
        call_id = str(uuid.uuid4())
        
        self._log(f"Групповой звонок на {phone} для {len(tasks)} задач")
        self._log(f"Call ID: {call_id}")

        voice_settings = self.config.get("voice_settings", {})
        language = voice_settings.get("language", "ru-RU")
        voice = voice_settings.get("voice")

        # Формируем список задач
        tasks_list = []
        for i, task in enumerate(tasks, 1):
            task_name = task["name"]
            tasks_list.append(f"{i}. {task_name}")
        
        tasks_text = ". ".join(tasks_list)
        
        greeting = f"Привет {recipient_name}! У тебя {len(tasks)} напоминаний на сегодня. {tasks_text}. Пожалуйста, скажи по каждой задаче: выполнена или нет. Отвечай после сигнала."

        response = VoiceResponse()
        say_kwargs = {"language": language}
        if voice:
            say_kwargs["voice"] = voice
        response.say(greeting, **say_kwargs)
        response.record(
            play_beep=True,
            max_length=120,  # Увеличиваем время для ответа по нескольким задачам
            timeout=10,
            finish_on_key="#"
        )
        response.say("Спасибо! До свидания.", **say_kwargs)

        twiml_payload = str(response)

        try:
            call = self.twilio_client.calls.create(
                to=phone,
                from_=self.twilio_phone,
                twiml=twiml_payload
            )
            
            self._log(f"Групповой звонок инициирован: {call.sid}")
            
            return "INITIATED", call.sid, call_id
            
        except Exception as e:
            self._log(f"Ошибка при групповом звонке: {str(e)}", "ERROR")
            return "ERROR", None, call_id
    
    def get_call_recording_and_transcribe(self, call_sid: str, recording_path: Optional[str], timeout: int = 90) -> Tuple[Optional[str], str]:
        """
        Транскрипция записи звонка с помощью OpenAI Whisper.
        """
        if not recording_path or not os.path.exists(recording_path):
            self._log("Файл записи не найден для транскрипции", "WARNING")
            return None, "NOT_FOUND"

        self._log(f"Отправка записи в OpenAI Whisper: {recording_path}")

        model_name = self.config.get("transcription_model", "whisper-1")
        language = self.config.get("transcription_language", "ru")

        try:
            with open(recording_path, "rb") as audio_file:
                response = self.openai_client.audio.transcriptions.create(
                    model=model_name,
                    file=audio_file,
                    language=language
                )

            if hasattr(response, "text"):
                transcription_text = response.text or ""
            elif isinstance(response, dict):
                transcription_text = response.get("text", "")
            else:
                transcription_text = str(response)

            transcription_text = transcription_text.strip()
            status = "SUCCESS" if transcription_text else "NO_RESPONSE"

            self._store_transcription(call_sid, transcription_text, status)

            if status == "SUCCESS":
                self._log(f"Транскрипция получена ({len(transcription_text)} символов)")
                return transcription_text, "SUCCESS"

            self._log("Пустая транскрипция - возможно, пользователь не ответил", "WARNING")
            return None, "NO_RESPONSE"

        except Exception as e:
            self._log(f"Ошибка транскрипции через OpenAI: {str(e)}", "ERROR")
            self._store_transcription(call_sid, "", "ERROR")
            return None, "ERROR"

    def download_call_recording(self, call_sid: str, timeout: int = 90) -> Tuple[Optional[str], str]:
        """Скачать аудиозапись звонка из Twilio по CallSid.

        Возвращает кортеж (path, status), где status в {"SUCCESS", "NOT_FOUND", "ERROR", "TIMEOUT"}.
        """
        try:
            self._log(f"Поиск записи звонка в Twilio для CallSid: {call_sid}")
            start_time = time.time()
            poll_interval = 3

            recording_sid = None
            last_count = 0
            while time.time() - start_time < timeout:
                # Получаем список записей для звонка
                recordings = self.twilio_client.recordings.list(call_sid=call_sid, limit=20)
                if recordings:
                    last_count = len(recordings)
                    # Берем самую позднюю запись
                    recording_sid = recordings[0].sid
                    break
                time.sleep(poll_interval)
                elapsed = int(time.time() - start_time)
                if elapsed % 10 == 0:
                    self._log(f"Ожидание появления записи... ({elapsed}s / {timeout}s, найдено: {last_count})")

            if not recording_sid:
                self._log("Запись не найдена в Twilio по истечении таймаута", "WARNING")
                return None, "TIMEOUT"

            # Формируем URL на mp3
            media_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Recordings/{recording_sid}.mp3"
            self._log(f"Скачивание записи {recording_sid} с {media_url}")

            response = requests.get(media_url, auth=(self.twilio_account_sid, self.twilio_auth_token), stream=True, timeout=60)
            if response.status_code != 200:
                self._log(f"Ошибка скачивания записи: HTTP {response.status_code}", "ERROR")
                return None, "ERROR"

            file_path = os.path.join(RECORDINGS_DIR, f"{call_sid}.mp3")
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            self._log(f"Запись сохранена: {file_path}")
            return file_path, "SUCCESS"
        except Exception as e:
            self._log(f"Исключение при скачивании записи: {str(e)}", "ERROR")
            return None, "ERROR"
    
    def send_sms(self, phone: str, task_name: str, sms_code: Optional[str] = None) -> Tuple[bool, str]:
        """Отправка SMS. Возвращает (успех, текст_сообщения)
        
        Args:
            phone: номер телефона
            task_name: название задачи
            sms_code: номер задачи для ответа (опционально)
        """
        self._log(f"Отправка SMS на {phone}")
        
        try:
            if sms_code:
                # Простой формат: номер + задача
                message = f"{sms_code}. {task_name}"
            else:
                message = f"📋 Напоминание: {task_name}\n\nСистема перезвонит позже для уточнения статуса."
            
            sms = self.twilio_client.messages.create(
                to=phone,
                from_=self.twilio_phone,
                body=message
            )
            
            self._log(f"SMS отправлено: {sms.sid}")
            return True, message
            
        except Exception as e:
            self._log(f"Ошибка отправки SMS: {str(e)}", "ERROR")
            return False, message
    
    def analyze_response_with_ai(self, transcribed_text: str, task_name: str) -> str:
        """Анализ ответа через ChatGPT"""
        self._log(f"Анализ ответа: '{transcribed_text}'")
        
        try:
            prompt = f"""Ты анализируешь ответ сотрудника на напоминание о задаче.

Задача: "{task_name}"
Ответ сотрудника: "{transcribed_text}"

Определи статус задачи и верни ТОЛЬКО ОДИН из вариантов:
- ВЫПОЛНЕНО (если задача сделана, готова, завершена)
- НЕ_ВЫПОЛНЕНО (если не сделал, забыл, не успел)
- В_РАБОТЕ (если делает сейчас, в процессе выполнения)
- ПЕРЕЗВОНИТЬ (если не может говорить, просит перезвонить)
- НЕЯСНО (если ответ непонятен или неразборчив)

Ответ (одним словом):"""

            response = self.openai_client.chat.completions.create(
                model=self.config.get("ai_model", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": "Ты помощник для анализа голосовых ответов сотрудников."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=50
            )
            
            status = response.choices[0].message.content.strip().upper()
            self._log(f"AI анализ: {status}")
            
            return status
            
        except Exception as e:
            self._log(f"Ошибка AI анализа: {str(e)}", "ERROR")
            return "НЕЯСНО"
    
    def analyze_batch_response(self, transcribed_text: str, tasks: List[Dict]) -> Dict[str, str]:
        """
        Анализ группового ответа через ChatGPT для нескольких задач
        
        Args:
            transcribed_text: транскрипция ответа
            tasks: список задач
            
        Returns:
            Dict[task_id, status]: словарь со статусами для каждой задачи
        """
        self._log(f"Анализ группового ответа: '{transcribed_text}'")
        
        try:
            # Формируем список задач для промпта
            tasks_list = []
            for i, task in enumerate(tasks, 1):
                tasks_list.append(f"{i}. {task['name']} (ID: {task['id']})")
            
            tasks_text = "\n".join(tasks_list)
            
            prompt = f"""Ты анализируешь ответ сотрудника на напоминание о нескольких задачах.

Задачи:
{tasks_text}

Ответ сотрудника: "{transcribed_text}"

Для КАЖДОЙ задачи определи статус. Верни ответ в формате JSON:
{{
    "task_id": "СТАТУС",
    ...
}}

Возможные статусы:
- ВЫПОЛНЕНО (если задача сделана, готова, завершена)
- НЕ_ВЫПОЛНЕНО (если не сделал, забыл, не успел)
- В_РАБОТЕ (если делает сейчас, в процессе выполнения)
- ПЕРЕЗВОНИТЬ (если не может говорить, просит перезвонить)
- НЕЯСНО (если ответ непонятен или неразборчив, или задача не упомянута)

Если задача не упомянута в ответе, ставь статус НЕЯСНО.

Ответ (только JSON):"""

            response = self.openai_client.chat.completions.create(
                model=self.config.get("ai_model", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": "Ты помощник для анализа голосовых ответов сотрудников. Отвечай только в формате JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=500
            )
            
            result_text = response.choices[0].message.content.strip()
            self._log(f"AI ответ: {result_text}")
            
            # Парсим JSON
            # Убираем markdown форматирование если есть
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            
            result = json.loads(result_text)
            
            # Проверяем, что все задачи имеют статус
            for task in tasks:
                if task["id"] not in result:
                    result[task["id"]] = "НЕЯСНО"
            
            self._log(f"Результаты анализа: {result}")
            return result
            
        except Exception as e:
            self._log(f"Ошибка AI анализа группового ответа: {str(e)}", "ERROR")
            # Возвращаем НЕЯСНО для всех задач
            return {task["id"]: "НЕЯСНО" for task in tasks}

    def update_task_in_clickup(self, task_id: str, status: str, task_data: Dict):
        """Обновление задачи в ClickUp"""
        # Проверяем, была ли задача уже выполнена
        if self._is_task_completed(task_id):
            self._log(f"Задача {task_id} уже была выполнена ранее. Пропускаем обновление.")
            return
        
        self._log(f"Обновление задачи {task_id}: статус={status}")
        
        headers = {
            "Authorization": self.clickup_token,
            "Content-Type": "application/json"
        }
        
        url = f"https://api.clickup.com/api/v2/task/{task_id}"
        
        now = datetime.now(self.tz)
        
        # Получаем текущее описание - handle both dict and string
        if isinstance(task_data, dict):
            current_description = task_data.get("description", "")
        else:
            current_description = ""
        
        # Добавляем запись в историю
        history_entry = f"\n\n---\n**{now.strftime('%Y-%m-%d %H:%M:%S')}** - Статус: {status}"
        new_description = current_description + history_entry
        
        update_data = {
            "description": new_description
        }

        target_status = self.status_mapping.get(status)
        if target_status:
            update_data["status"] = target_status
        elif status == "ВЫПОЛНЕНО":
            # На случай отсутствия маппинга — закрываем задачу дефолтным статусом
            update_data["status"] = "complete"
        
        if status == "ВЫПОЛНЕНО":
            # Закрываем задачу
            self._log(f"Отметка задачи {task_id} как выполненной")
            # Получаем имя задачи
            task_name = task_data.get("name", "Неизвестная задача") if isinstance(task_data, dict) else "Неизвестная задача"
            # Отмечаем задачу как выполненную, чтобы больше не проверять
            self._mark_task_completed(task_id, task_name)
            
        elif status in ["НЕ_ВЫПОЛНЕНО", "В_РАБОТЕ", "НЕЯСНО", "ПЕРЕЗВОНИТЬ"]:
            # Переносим due_date
            priority_obj = task_data.get("priority") if isinstance(task_data, dict) else None
            if priority_obj and isinstance(priority_obj, dict):
                priority = priority_obj.get("priority", "normal")
            else:
                priority = "normal"
            # Используем reminder_settings если доступно, иначе reminder_intervals
            if "reminder_settings" in self.config:
                interval_hours = self.config["reminder_settings"].get("interval_hours", 2)
            else:
                interval_hours = self.config.get("reminder_intervals", {}).get(priority, 3)
            
            # Для "В_РАБОТЕ" - напомнить через 1 час
            if status == "В_РАБОТЕ":
                interval_hours = 1
            # Для "ПЕРЕЗВОНИТЬ" - через 30 минут
            elif status == "ПЕРЕЗВОНИТЬ":
                interval_hours = 0.5
            
            next_reminder = now + timedelta(hours=interval_hours)
            
            # Проверяем рабочее время
            working_hours = self.config.get("working_hours", {})
            end_time = working_hours.get("end", 18)
            start_time = working_hours.get("start", 10)
            
            # Поддержка как числового, так и строкового формата
            if isinstance(end_time, int):
                end_hour = end_time
            else:
                end_hour = int(str(end_time).split(":")[0])
            
            if isinstance(start_time, int):
                start_hour = start_time
            else:
                start_hour = int(str(start_time).split(":")[0])
            
            if next_reminder.hour >= end_hour:
                # Переносим на следующий рабочий день
                next_reminder = next_reminder.replace(hour=start_hour, minute=0)
                next_reminder += timedelta(days=1)
                
                # Пропускаем выходные
                working_days = working_hours.get("working_days", [0,1,2,3,4])
                while working_days and next_reminder.weekday() not in working_days:
                    next_reminder += timedelta(days=1)
            
            update_data["due_date"] = int(next_reminder.timestamp() * 1000)
            self._log(f"Следующее напоминание: {next_reminder}")
        
        # Сохраняем старый статус для уведомления
        old_status = task_data.get("status", {}).get("status", "unknown") if isinstance(task_data, dict) else "unknown"
        
        # Обновляем задачу
        response = requests.put(url, headers=headers, json=update_data)
        
        if response.status_code == 200:
            self._log(f"Задача {task_id} обновлена успешно")
            
            # Отправляем уведомление в Telegram об обновлении статуса
            if self._is_telegram_notification_enabled("status_updates"):
                try:
                    task_name = task_data.get("name", "Неизвестная задача") if isinstance(task_data, dict) else "Неизвестная задача"
                    assignee_name = "Неизвестно"
                    
                    # Пытаемся извлечь имя исполнителя из названия задачи
                    assignee_match = re.search(r'\[([^\]]+)\]', task_name)
                    if assignee_match:
                        assignee_name = assignee_match.group(1)
                    
                    # Получаем URL задачи
                    task_url = f"https://app.clickup.com/t/{task_id}"
                    
                    self.telegram.send_task_status_update(
                        task_name=task_name,
                        old_status=old_status,
                        new_status=target_status or status,
                        assignee=assignee_name,
                        task_url=task_url
                    )
                except Exception as tg_error:
                    self._log(f"Ошибка отправки Telegram уведомления: {tg_error}", "WARNING")
        else:
            self._log(f"Ошибка обновления задачи: {response.text}", "ERROR")
            
            # Отправляем уведомление об ошибке в Telegram
            if self._is_telegram_notification_enabled("errors"):
                try:
                    self.telegram.send_error_notification(
                        error_message=f"Ошибка обновления задачи {task_id}",
                        context=f"Статус: {response.status_code}, Ответ: {response.text[:200]}"
                    )
                except Exception as tg_error:
                    self._log(f"Ошибка отправки Telegram уведомления: {tg_error}", "WARNING")

    def _post_call_result_comment(self, task_id: str, call_sid: str, result: Dict[str, str]):
        """Добавление комментария с результатами звонка в ClickUp.
        
        Args:
            task_id: ID задачи в ClickUp
            call_sid: SID звонка Twilio
            result: словарь с результатами, может содержать:
                - status: статус звонка (recording_found, no_recording, error)
                - transcription: текст транскрипции (если есть)
                - sms_sent: отправлено ли SMS
                - sms_text: текст SMS (если отправлено)
                - ai_status: результат AI анализа (если есть)
                - error: текст ошибки (если была)
        """
        headers = {
            "Authorization": self.clickup_token,
            "Content-Type": "application/json"
        }
        
        # Формируем текст комментария
        now = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M:%S")
        comment_parts = [
            f"📞 **Результат звонка** ({now})",
            f"CallSid: `{call_sid}`",
            ""
        ]
        
        # Статус записи
        status = result.get("status", "unknown")
        if status == "recording_found":
            comment_parts.append("✅ **Запись звонка:** Найдена")
        elif status == "no_recording":
            comment_parts.append("⚠️ **Запись звонка:** Не найдена (возможно, абонент не ответил)")
        elif status == "error":
            comment_parts.append("❌ **Запись звонка:** Ошибка")
            if result.get("error"):
                comment_parts.append(f"  - Ошибка: {result['error']}")
        
        # Транскрипция
        transcription = result.get("transcription")
        if transcription:
            comment_parts.append("")
            comment_parts.append("🗣️ **Транскрипция:**")
            comment_parts.append(f"```\n{transcription}\n```")
            
            # AI анализ
            ai_status = result.get("ai_status")
            if ai_status:
                comment_parts.append(f"🤖 **AI Анализ:** {ai_status}")
        
        # SMS
        if result.get("sms_sent"):
            comment_parts.append("")
            comment_parts.append("📱 **SMS отправлено:**")
            sms_text = result.get("sms_text", "")
            if sms_text:
                comment_parts.append(f"```\n{sms_text}\n```")
        
        comment_text = "\n".join(comment_parts)
        url = f"https://api.clickup.com/api/v2/task/{task_id}/comment"

        try:
            response = requests.post(url, headers=headers, json={"comment_text": comment_text})
            if response.status_code == 200:
                self._log(f"Комментарий с результатами добавлен к задаче {task_id}")
            else:
                self._log(f"Ошибка добавления комментария: {response.text}", "ERROR")
        except Exception as exc:
            self._log(f"Исключение при отправке комментария: {exc}", "ERROR")
    
    def _post_transcription_comment(self, task_id: str, transcription: str, call_sid: str):
        """Отправка комментария с транскрипцией в ClickUp."""
        if not transcription:
            self._log("Пропуск комментария: транскрипция пуста")
            return

        headers = {
            "Authorization": self.clickup_token,
            "Content-Type": "application/json"
        }
        comment_text = (
            f"🗣️ Транскрипция звонка (CallSid: {call_sid})\n"
            f"```\n{transcription}\n```"
        )
        url = f"https://api.clickup.com/api/v2/task/{task_id}/comment"

        try:
            response = requests.post(url, headers=headers, json={"comment_text": comment_text})
            if response.status_code == 200:
                self._log(f"Комментарий с транскрипцией добавлен к задаче {task_id}")
            else:
                self._log(f"Ошибка добавления комментария: {response.text}", "ERROR")
        except Exception as exc:
            self._log(f"Исключение при отправке комментария: {exc}", "ERROR")
    
    def _generate_sms_code(self) -> str:
        """Генерация уникального SMS кода - простой номер"""
        try:
            # Загружаем существующий маппинг
            mappings = {}
            if os.path.exists(SMS_CODES_FILE):
                with open(SMS_CODES_FILE, 'r', encoding='utf-8') as f:
                    mappings = json.load(f)
            
            # Удаляем старые коды (старше 7 дней)
            now = datetime.now(self.tz)
            mappings = {
                k: v for k, v in mappings.items()
                if datetime.fromisoformat(v.get("created_at", "2000-01-01")) > now - timedelta(days=7)
            }
            
            # Находим максимальный номер и добавляем 1
            existing_numbers = []
            for code in mappings.keys():
                try:
                    existing_numbers.append(int(code))
                except ValueError:
                    pass
            
            next_number = max(existing_numbers) + 1 if existing_numbers else 1
            return str(next_number)
            
        except Exception as e:
            self._log(f"Ошибка генерации номера: {e}", "ERROR")
            # Fallback - случайный номер от 1 до 999
            return str(random.randint(1, 999))

    def process_task(self, task: Dict):
        """Обработка одной задачи"""
        task_id = task["id"]
        task_name = task["name"]
        
        self._log(f"\n{'='*60}")
        self._log(f"Обработка задачи: {task_name}")
        
        # TODO: Временно отключено - генерация SMS номера
        # sms_code = self._generate_sms_code()
        # self._save_sms_code_mapping(sms_code, task_id, task_name)
        # self._log(f"Сгенерирован SMS номер: {sms_code}")
        sms_code = None  # SMS временно отключено
        
        # Извлекаем имя получателя
        recipient_name = self._extract_recipient_name(task_name)
        
        if not recipient_name:
            self._log(f"Не удалось извлечь имя получателя из: {task_name}", "ERROR")
            return
        
        self._log(f"Получатель: {recipient_name}")
        
        # Получаем контактную информацию
        contact_info = self._get_contact_info(recipient_name)
        
        if not contact_info:
            self._log(f"Контактная информация для {recipient_name} не найдена", "ERROR")
            return
        
        phone = contact_info["phone"]
        
        # Отправляем уведомление о начале обработки задачи в Telegram
        if self._is_telegram_notification_enabled("task_reminders"):
            try:
                task_url = f"https://app.clickup.com/t/{task_id}"
                due_date = task.get("due_date", "Не указан")
                if due_date and due_date != "Не указан":
                    try:
                        due_timestamp = int(due_date) / 1000
                        due_date = datetime.fromtimestamp(due_timestamp, self.tz).strftime("%Y-%m-%d %H:%M")
                    except:
                        pass
                
                self.telegram.send_task_reminder(
                    task_name=task_name,
                    assignee=recipient_name,
                    due_date=str(due_date),
                    task_url=task_url
                )
            except Exception as tg_error:
                self._log(f"Ошибка отправки Telegram уведомления о задаче: {tg_error}", "WARNING")
        
        # Совершаем звонок
        call_status, call_sid, call_id = self.make_call(phone, task_name, recipient_name, task_id)
        
        if call_status == "ERROR":
            self._log("Ошибка при совершении звонка. SMS временно отключено.", "ERROR")
            # TODO: Временно отключено SMS
            # sms_success, sms_text = self.send_sms(phone, task_name, sms_code)
            sms_success, sms_text = False, None
            self.update_task_in_clickup(task_id, "НЕ_ВЫПОЛНЕНО", task)
            
            # Отправляем уведомление об ошибке звонка в Telegram (в любом случае)
            if self._is_telegram_notification_enabled("call_notifications"):
                try:
                    self.telegram.send_call_notification(
                        task_name=task_name,
                        assignee=recipient_name,
                        phone=phone,
                        call_status="failed"
                    )
                except Exception as tg_error:
                    self._log(f"Ошибка отправки Telegram уведомления: {tg_error}", "WARNING")
            
            # Добавляем комментарий с результатами
            self._post_call_result_comment(task_id, call_sid or "N/A", {
                "status": "error",
                "error": "Ошибка при инициации звонка",
                "sms_sent": sms_success,
                "sms_text": sms_text if sms_success else None
            })
            return
        
        self._log(f"Звонок инициирован. CallSid: {call_sid}")
        
        # Ждем завершения звонка и получения транскрипции
        self._log("Ожидание завершения звонка и транскрипции...")
        
        # Даем время на завершение звонка и появление записи/транскрипции
        self._log("Пауза 120 секунд перед получением записи/транскрипции...")
        time.sleep(120)
        
        # Пытаемся скачать запись разговора через Twilio API
        recording_path, rec_status = self.download_call_recording(call_sid, timeout=60)
        if rec_status == "SUCCESS":
            self._log(f"Аудиозапись разговора сохранена: {recording_path}")
        else:
            self._log("Скачивание записи не удалось", "WARNING")

        if rec_status != "SUCCESS" or not recording_path:
            self._log("Нет записи для анализа. SMS временно отключено.", "WARNING")
            # TODO: Временно отключено SMS
            # sms_success, sms_text = self.send_sms(phone, task_name, sms_code)
            sms_success, sms_text = False, None
            self.update_task_in_clickup(task_id, "НЕ_ВЫПОЛНЕНО", task)
            
            # Отправляем уведомление в Telegram о проблеме с записью
            if self._is_telegram_notification_enabled("errors"):
                try:
                    self.telegram.send_error_notification(
                        error_message="Нет записи звонка для анализа",
                        context=f"Задача: {task_name}, Исполнитель: {recipient_name}, CallSid: {call_sid}"
                    )
                except Exception as tg_error:
                    self._log(f"Ошибка отправки Telegram уведомления: {tg_error}", "WARNING")
            
            # Добавляем комментарий с результатами
            self._post_call_result_comment(task_id, call_sid, {
                "status": "no_recording",
                "sms_sent": sms_success,
                "sms_text": sms_text if sms_success else None
            })
            return

        # Получаем транскрипцию
        transcription, trans_status = self.get_call_recording_and_transcribe(call_sid, recording_path, timeout=90)
        
        if trans_status in ["NOT_FOUND", "NO_RESPONSE"] or not transcription:
            self._log("Нет ответа от пользователя. SMS временно отключено.", "WARNING")
            # TODO: Временно отключено SMS
            # sms_success, sms_text = self.send_sms(phone, task_name, sms_code)
            sms_success, sms_text = False, None
            self.update_task_in_clickup(task_id, "НЕ_ВЫПОЛНЕНО", task)
            
            # Отправляем уведомление в Telegram об отсутствии ответа
            if self._is_telegram_notification_enabled("call_notifications"):
                try:
                    self.telegram.send_call_notification(
                        task_name=task_name,
                        assignee=recipient_name,
                        phone=phone,
                        call_status="no-answer"
                    )
                except Exception as tg_error:
                    self._log(f"Ошибка отправки Telegram уведомления: {tg_error}", "WARNING")
            
            # Добавляем комментарий с результатами
            self._post_call_result_comment(task_id, call_sid, {
                "status": "no_recording",
                "error": "Нет ответа от пользователя",
                "sms_sent": sms_success,
                "sms_text": sms_text if sms_success else None
            })
            return
        
        if trans_status == "ERROR":
            self._log("Ошибка получения транскрипции. SMS временно отключено.", "ERROR")
            # TODO: Временно отключено SMS
            # sms_success, sms_text = self.send_sms(phone, task_name, sms_code)
            sms_success, sms_text = False, None
            self.update_task_in_clickup(task_id, "НЕЯСНО", task)
            
            # Добавляем комментарий с результатами
            self._post_call_result_comment(task_id, call_sid, {
                "status": "error",
                "error": "Ошибка при получении транскрипции",
                "sms_sent": sms_success,
                "sms_text": sms_text if sms_success else None
            })
            return
        
        # Анализируем ответ с помощью AI
        ai_status = self.analyze_response_with_ai(transcription, task_name)
        
        # Обновляем задачу в ClickUp
        self.update_task_in_clickup(task_id, ai_status, task)
        
        # Если задача не выполнена, отправляем SMS (временно отключено)
        sms_sent = False
        sms_text = None
        if ai_status != "ВЫПОЛНЕНО":
            self._log("Задача не выполнена. SMS временно отключено.")
            # TODO: Временно отключено SMS
            # sms_sent, sms_text = self.send_sms(phone, task_name, sms_code)
        
        # Добавляем комментарий с результатами (запись найдена, есть транскрипция)
        self._post_call_result_comment(task_id, call_sid, {
            "status": "recording_found",
            "transcription": transcription,
            "ai_status": ai_status,
            "sms_sent": sms_sent,
            "sms_text": sms_text if sms_sent else None
        })
        
        self._log("Обработка задачи завершена")
    
    def process_batch_tasks(self, tasks: List[Dict], recipient_name: str, phone: str):
        """
        Обработка группы задач одним звонком
        
        Args:
            tasks: список задач для обработки
            recipient_name: имя получателя
            phone: номер телефона
        """
        self._log(f"\n{'='*60}")
        self._log(f"Групповая обработка {len(tasks)} задач для {recipient_name}")
        
        # Совершаем групповой звонок
        call_status, call_sid, call_id = self.make_batch_call(phone, tasks, recipient_name)
        
        if call_status == "ERROR":
            self._log("Ошибка при совершении группового звонка.", "ERROR")
            # Обновляем все задачи как НЕ_ВЫПОЛНЕНО
            for task in tasks:
                self.update_task_in_clickup(task["id"], "НЕ_ВЫПОЛНЕНО", task)
                self._post_call_result_comment(task["id"], call_sid or "N/A", {
                    "status": "error",
                    "error": "Ошибка при инициации группового звонка",
                    "sms_sent": False,
                    "sms_text": None
                })
            return
        
        self._log(f"Групповой звонок инициирован. CallSid: {call_sid}")
        
        # Ждем завершения звонка и получения транскрипции
        self._log("Ожидание завершения звонка и транскрипции...")
        self._log("Пауза 120 секунд перед получением записи/транскрипции...")
        time.sleep(120)
        
        # Пытаемся скачать запись разговора через Twilio API
        recording_path, rec_status = self.download_call_recording(call_sid, timeout=60)
        if rec_status == "SUCCESS":
            self._log(f"Аудиозапись разговора сохранена: {recording_path}")
        else:
            self._log("Скачивание записи не удалось", "WARNING")

        if rec_status != "SUCCESS" or not recording_path:
            self._log("Нет записи для анализа.", "WARNING")
            # Обновляем все задачи как НЕ_ВЫПОЛНЕНО
            for task in tasks:
                self.update_task_in_clickup(task["id"], "НЕ_ВЫПОЛНЕНО", task)
                self._post_call_result_comment(task["id"], call_sid, {
                    "status": "no_recording",
                    "sms_sent": False,
                    "sms_text": None
                })
            return

        # Получаем транскрипцию
        transcription, trans_status = self.get_call_recording_and_transcribe(call_sid, recording_path, timeout=90)
        
        if trans_status in ["NOT_FOUND", "NO_RESPONSE"] or not transcription:
            self._log("Нет ответа от пользователя.", "WARNING")
            # Обновляем все задачи как НЕ_ВЫПОЛНЕНО
            for task in tasks:
                self.update_task_in_clickup(task["id"], "НЕ_ВЫПОЛНЕНО", task)
                self._post_call_result_comment(task["id"], call_sid, {
                    "status": "no_recording",
                    "error": "Нет ответа от пользователя",
                    "sms_sent": False,
                    "sms_text": None
                })
            return
        
        if trans_status == "ERROR":
            self._log("Ошибка получения транскрипции.", "ERROR")
            # Обновляем все задачи как НЕЯСНО
            for task in tasks:
                self.update_task_in_clickup(task["id"], "НЕЯСНО", task)
                self._post_call_result_comment(task["id"], call_sid, {
                    "status": "error",
                    "error": "Ошибка при получении транскрипции",
                    "sms_sent": False,
                    "sms_text": None
                })
            return
        
        # Анализируем групповой ответ с помощью AI
        task_statuses = self.analyze_batch_response(transcription, tasks)
        
        # Обновляем каждую задачу в ClickUp
        for task in tasks:
            task_id = task["id"]
            ai_status = task_statuses.get(task_id, "НЕЯСНО")
            
            self._log(f"Задача {task['name']}: {ai_status}")
            self.update_task_in_clickup(task_id, ai_status, task)
            
            # Добавляем комментарий с результатами
            self._post_call_result_comment(task_id, call_sid, {
                "status": "recording_found",
                "transcription": transcription,
                "ai_status": ai_status,
                "sms_sent": False,
                "sms_text": None,
                "batch_call": True,
                "total_tasks": len(tasks)
            })
        
        self._log("Групповая обработка задач завершена")
    
    def run(self, force=False):
        """Основной цикл работы системы"""
        self._log("\n" + "="*60)
        self._log("🤖 ЗАПУСК СИСТЕМЫ НАПОМИНАНИЙ v5.0")
        self._log("="*60)
        
        # Проверка рабочего времени
        if not force and not self._is_working_hours():
            self._log("⏰ Сейчас нерабочее время. Пропуск проверки.")
            return
        
        if force:
            self._log("⚠️ ПРИНУДИТЕЛЬНЫЙ ЗАПУСК (игнорирование рабочего времени)")
        
        # Получаем задачи для напоминания
        tasks = self.get_tasks_for_reminder()
        
        if not tasks:
            self._log("✅ Нет задач для напоминания")
            return
        
        self._log(f"📋 Найдено задач для напоминания: {len(tasks)}")
        
        # Группируем задачи по получателю
        tasks_by_recipient = {}
        for task in tasks:
            task_name = task["name"]
            recipient_name = self._extract_recipient_name(task_name)
            
            if not recipient_name:
                self._log(f"⚠️ Не удалось определить получателя для задачи: {task_name}", "WARNING")
                continue
            
            if recipient_name not in tasks_by_recipient:
                tasks_by_recipient[recipient_name] = []
            
            tasks_by_recipient[recipient_name].append(task)
        
        # Обрабатываем задачи группами по получателям
        for recipient_name, recipient_tasks in tasks_by_recipient.items():
            try:
                self._log(f"\n📞 Обработка {len(recipient_tasks)} задач для {recipient_name}")
                
                # Получаем контактную информацию
                contact_info = self._get_contact_info(recipient_name)
                
                if not contact_info:
                    self._log(f"❌ Контактная информация для {recipient_name} не найдена", "ERROR")
                    continue
                
                phone = contact_info["phone"]
                
                # Обрабатываем все задачи одним звонком
                self.process_batch_tasks(recipient_tasks, recipient_name, phone)
                
            except Exception as e:
                self._log(f"Ошибка обработки задач для {recipient_name}: {str(e)}", "ERROR")
                import traceback
                self._log(traceback.format_exc(), "ERROR")
        
        self._log("\n" + "="*60)
        self._log("✅ ЗАВЕРШЕНИЕ РАБОТЫ СИСТЕМЫ")
        self._log("="*60 + "\n")


def main():
    """Точка входа"""
    try:
        print("🚀 Запуск системы напоминаний...")
        
        # Check for --force flag
        force = "--force" in sys.argv or "-f" in sys.argv
        
        system = ReminderSystem()
        system.run(force=force)
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
