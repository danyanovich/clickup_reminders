#!/usr/bin/env python3
"""
Скрипт для обработки существующих транскрипций и обновления статусов задач в ClickUp.

Проблема:
- process_recordings.py создает транскрипции, но НЕ обновляет задачи
- Транскрипции остаются "висеть" без применения к задачам

Решение:
- Читает все транскрипции из transcriptions/
- Находит соответствующие задачи в ClickUp по CallSid в комментариях
- Обновляет статусы задач на основе AI анализа ответов
"""

import os
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import requests
from openai import OpenAI

BASE_DIR = os.getenv("BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SECRETS_PATH = os.path.join(os.path.dirname(BASE_DIR), ".venv", "bin", "secrets.json")
TRANSCRIPTIONS_DIR = os.path.join(BASE_DIR, "transcriptions")
VAR_TRANSCRIPTIONS_DIR = os.path.join(BASE_DIR, "var", "transcriptions")


def load_json_file(path: str) -> Dict:
    """Загрузка JSON файла"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_secret_value(secrets: Dict, section: str, keys: List[str]) -> Optional[str]:
    """Извлечение секрета из разных форматов"""
    section_data = secrets.get(section, {})
    if not isinstance(section_data, dict):
        return None
    
    for key in keys:
        value = section_data.get(key)
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        elif value:
            return value
    
    return None


class TranscriptionProcessor:
    """Обработчик транскрипций для обновления задач в ClickUp"""
    
    def __init__(self):
        """Инициализация процессора"""
        config = load_json_file(CONFIG_PATH)
        
        # Приоритет: переменные окружения -> secrets файл
        self.clickup_token = os.getenv("CLICKUP_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        
        # Если нет в env, пробуем secrets файл
        if not self.clickup_token or not self.openai_key:
            secrets = load_json_file(SECRETS_PATH)
            
            if not self.clickup_token:
                self.clickup_token = get_secret_value(secrets, "clickup", ["api_key", "clickup_api_key"])
                if not self.clickup_token:
                    self.clickup_token = get_secret_value(secrets, "telegram", ["clickup_api_key"])
            
            if not self.openai_key:
                self.openai_key = get_secret_value(secrets, "openai", ["api_key"])
        
        if not self.clickup_token:
            raise KeyError("ClickUp API key not found. Set CLICKUP_API_KEY env variable or add to secrets.json")
        
        if not self.openai_key:
            raise KeyError("OpenAI API key not found. Set OPENAI_API_KEY env variable or add to secrets.json")
        
        self.openai_client = OpenAI(api_key=self.openai_key)
        
        # ClickUp config
        clickup_config = config.get("clickup", {})
        self.workspace_id = clickup_config.get("workspace_id")
        
        # Mapping статусов
        raw_mapping = clickup_config.get("status_mapping", {})
        self.status_mapping = {key.upper(): value for key, value in raw_mapping.items()}
        self.status_mapping.setdefault("ВЫПОЛНЕНО", clickup_config.get("completed_status", "complete"))
        self.status_mapping.setdefault("НЕ_ВЫПОЛНЕНО", clickup_config.get("pending_status", "to do"))
        self.status_mapping.setdefault("НЕЯСНО", clickup_config.get("unclear_status", "to do"))
        
    def _log(self, message: str, level: str = "INFO"):
        """Простое логирование"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")
    
    def analyze_transcription_with_ai(self, transcription: str, task_name: str = "") -> str:
        """Анализ транскрипции с помощью OpenAI"""
        self._log(f"Анализ транскрипции через AI...")
        
        prompt = f"""Проанализируй ответ человека на звонок-напоминание о задаче{f' "{task_name}"' if task_name else ''}.
        
Ответ человека: "{transcription}"

Определи статус выполнения задачи на основе ответа. Возможные варианты:
- ВЫПОЛНЕНО: задача уже выполнена
- НЕ_ВЫПОЛНЕНО: задача не выполнена, будет выполнена позже
- НЕЯСНО: неясный ответ, невозможно определить статус

Верни ТОЛЬКО одно слово из списка выше (ВЫПОЛНЕНО, НЕ_ВЫПОЛНЕНО или НЕЯСНО)."""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Ты анализируешь ответы на напоминания о задачах."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=50
            )
            
            result = response.choices[0].message.content.strip().upper()
            
            # Валидация ответа
            if result in ["ВЫПОЛНЕНО", "НЕ_ВЫПОЛНЕНО", "НЕЯСНО"]:
                self._log(f"AI определил статус: {result}")
                return result
            else:
                self._log(f"AI вернул неожиданный статус: {result}, использую НЕЯСНО", "WARNING")
                return "НЕЯСНО"
                
        except Exception as e:
            self._log(f"Ошибка AI анализа: {str(e)}", "ERROR")
            return "НЕЯСНО"
    
    def find_task_by_call_sid(self, call_sid: str) -> Optional[str]:
        """Поиск задачи в ClickUp по CallSid в комментариях"""
        self._log(f"Поиск задачи для CallSid: {call_sid}")
        
        if not self.workspace_id:
            self._log("Workspace ID не настроен, пропускаем поиск", "WARNING")
            return None
        
        headers = {
            "Authorization": self.clickup_token,
            "Content-Type": "application/json"
        }
        
        # Получаем все команды (teams) в workspace
        try:
            teams_url = f"https://api.clickup.com/api/v2/team"
            teams_response = requests.get(teams_url, headers=headers, timeout=10)
            teams_response.raise_for_status()
            teams = teams_response.json().get("teams", [])
            
            # Ищем team с нужным workspace_id
            target_team = None
            for team in teams:
                spaces_url = f"https://api.clickup.com/api/v2/team/{team['id']}/space"
                spaces_response = requests.get(spaces_url, headers=headers, timeout=10)
                if spaces_response.status_code == 200:
                    spaces = spaces_response.json().get("spaces", [])
                    for space in spaces:
                        if space.get("id") == self.workspace_id:
                            target_team = team
                            break
                if target_team:
                    break
            
            if not target_team:
                self._log(f"Team с workspace {self.workspace_id} не найден", "WARNING")
                return None
            
            # Получаем все spaces в team
            spaces_url = f"https://api.clickup.com/api/v2/team/{target_team['id']}/space"
            spaces_response = requests.get(spaces_url, headers=headers, timeout=10)
            spaces_response.raise_for_status()
            spaces = spaces_response.json().get("spaces", [])
            
            # Проходим по всем folders и lists
            for space in spaces:
                # Получаем folders
                folders_url = f"https://api.clickup.com/api/v2/space/{space['id']}/folder"
                folders_response = requests.get(folders_url, headers=headers, timeout=10)
                if folders_response.status_code == 200:
                    folders = folders_response.json().get("folders", [])
                    
                    for folder in folders:
                        # Получаем lists в folder
                        lists_url = f"https://api.clickup.com/api/v2/folder/{folder['id']}/list"
                        lists_response = requests.get(lists_url, headers=headers, timeout=10)
                        if lists_response.status_code == 200:
                            lists = lists_response.json().get("lists", [])
                            
                            for list_item in lists:
                                # Получаем задачи в списке
                                tasks_url = f"https://api.clickup.com/api/v2/list/{list_item['id']}/task"
                                tasks_response = requests.get(tasks_url, headers=headers, timeout=10)
                                if tasks_response.status_code == 200:
                                    tasks = tasks_response.json().get("tasks", [])
                                    
                                    # Проверяем комментарии каждой задачи
                                    for task in tasks:
                                        task_id = task.get("id")
                                        
                                        # Получаем комментарии задачи
                                        comments_url = f"https://api.clickup.com/api/v2/task/{task_id}/comment"
                                        comments_response = requests.get(comments_url, headers=headers, timeout=10)
                                        if comments_response.status_code == 200:
                                            comments = comments_response.json().get("comments", [])
                                            
                                            # Ищем CallSid в комментариях
                                            for comment in comments:
                                                comment_text = comment.get("comment_text", "")
                                                if call_sid in comment_text:
                                                    self._log(f"✓ Найдена задача {task_id}: {task.get('name')}")
                                                    return task_id
            
            self._log(f"Задача с CallSid {call_sid} не найдена", "WARNING")
            return None
            
        except Exception as e:
            self._log(f"Ошибка поиска задачи: {str(e)}", "ERROR")
            return None
    
    def update_task_status(self, task_id: str, ai_status: str):
        """Обновление статуса задачи в ClickUp"""
        clickup_status = self.status_mapping.get(ai_status, self.status_mapping.get("НЕЯСНО"))
        
        self._log(f"Обновление задачи {task_id}: AI={ai_status} -> ClickUp={clickup_status}")
        
        headers = {
            "Authorization": self.clickup_token,
            "Content-Type": "application/json"
        }
        
        url = f"https://api.clickup.com/api/v2/task/{task_id}"
        data = {"status": clickup_status}
        
        try:
            response = requests.put(url, headers=headers, json=data, timeout=10)
            response.raise_for_status()
            self._log(f"✓ Задача {task_id} обновлена успешно")
            return True
        except Exception as e:
            self._log(f"Ошибка обновления задачи: {str(e)}", "ERROR")
            return False
    
    def process_transcription_file(self, file_path: Path) -> bool:
        """Обработка одного файла транскрипции"""
        self._log(f"\n{'='*60}")
        self._log(f"Обработка файла: {file_path.name}")
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            call_sid = data.get("call_sid")
            transcription = data.get("transcription", "")
            
            if not call_sid:
                self._log("CallSid не найден в файле", "WARNING")
                return False
            
            if not transcription:
                self._log("Транскрипция пустая", "WARNING")
                return False
            
            self._log(f"CallSid: {call_sid}")
            self._log(f"Транскрипция: {transcription[:100]}...")
            
            # Ищем задачу
            task_id = self.find_task_by_call_sid(call_sid)
            if not task_id:
                self._log("Задача не найдена, пропускаем", "WARNING")
                return False
            
            # Анализируем транскрипцию
            ai_status = self.analyze_transcription_with_ai(transcription)
            
            # Обновляем статус
            success = self.update_task_status(task_id, ai_status)
            
            return success
            
        except Exception as e:
            self._log(f"Ошибка обработки файла: {str(e)}", "ERROR")
            return False
    
    def run(self):
        """Основной метод обработки всех транскрипций"""
        self._log("="*60)
        self._log("🚀 ЗАПУСК ОБРАБОТКИ ТРАНСКРИПЦИЙ")
        self._log("="*60)
        
        # Собираем все JSON файлы из обеих директорий
        transcription_files = []
        
        for directory in [TRANSCRIPTIONS_DIR, VAR_TRANSCRIPTIONS_DIR]:
            if os.path.exists(directory):
                for file in Path(directory).glob("*.json"):
                    transcription_files.append(file)
        
        if not transcription_files:
            self._log("Транскрипции не найдены")
            return
        
        self._log(f"Найдено файлов транскрипций: {len(transcription_files)}")
        
        processed = 0
        updated = 0
        
        for file_path in transcription_files:
            if self.process_transcription_file(file_path):
                updated += 1
            processed += 1
        
        self._log("")
        self._log("="*60)
        self._log(f"✅ ЗАВЕРШЕНО")
        self._log(f"Обработано: {processed}, Обновлено: {updated}")
        self._log("="*60)


def main():
    """Точка входа"""
    processor = TranscriptionProcessor()
    processor.run()


if __name__ == "__main__":
    main()
