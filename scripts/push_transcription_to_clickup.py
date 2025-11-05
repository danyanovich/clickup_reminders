#!/usr/bin/env python3
from __future__ import annotations

"""Utility to push a call transcription into ClickUp and adjust task status."""

import argparse  # Разбор аргументов командной строки
import json  # Работа с JSON-файлами
import os  # Доступ к переменным окружения
import sys  # Управление путями импорта
from pathlib import Path  # Удобная работа с путями
from typing import Any, Dict, List, Mapping, Sequence, Tuple  # Аннотации типов

import requests  # HTTP-запросы к ClickUp API


# Ensure project modules are importable when the script is called directly.
ROOT = Path(__file__).resolve().parents[1]  # Корневая директория проекта
if str(ROOT) not in sys.path:  # Гарантируем доступ к локальным модулям
    sys.path.insert(0, str(ROOT))


def load_transcription_text(recording_sid: str | None, file_path: str | None) -> str:
    """Load transcription text from provided file or saved transcription folder."""
    if file_path:  # Приоритет — явный указанный файл
        p = Path(file_path)  # Преобразуем путь в объект Path
        with open(p, "r", encoding="utf-8") as fh:  # Открываем JSON с транскрипцией
            data = json.load(fh)  # Загружаем данные
        # Try common keys
        return (
            data.get("transcription_text")
            or data.get("transcription")
            or data.get("text")
            or ""
        )
    if recording_sid:  # Если передан SID записи, ищем файл в стандартной папке
        p = ROOT / "transcriptions" / f"{recording_sid}.json"
        with open(p, "r", encoding="utf-8") as fh:  # Открываем сохранённый ранее JSON
            data = json.load(fh)
        return (
            data.get("transcription_text")
            or data.get("transcription")
            or data.get("text")
            or ""
        )
    return ""  # Возвращаем пустую строку, если источник не найден


def fetch_task(clickup_token: str, task_id: str) -> Dict[str, Any]:
    """Fetch the latest task payload from ClickUp."""
    url = f"https://api.clickup.com/api/v2/task/{task_id}"  # Формируем URL запроса задачи
    headers = {"Authorization": clickup_token, "Content-Type": "application/json"}  # Заголовки для авторизации
    resp = requests.get(url, headers=headers, timeout=30)  # Выполняем запрос к ClickUp
    resp.raise_for_status()  # Бросаем исключение при ошибке HTTP
    return resp.json()  # Возвращаем JSON-ответ как словарь


def _extract(payload: Mapping[str, Any], paths: Sequence[Sequence[str]]) -> str | None:
    """Return the first non-empty string value found along candidate key paths."""
    for path in paths:  # Перебираем возможные пути к значению
        node: Any = payload  # Начинаем с корня словаря
        for key in path:  # Спускаемся по ключам
            if isinstance(node, Mapping) and key in node:
                node = node[key]  # Переходим глубже
            else:
                node = None  # Если ключа нет, прерываем путь
                break
        if node is None:  # Если путь не сработал — переходим к следующему
            continue
        if isinstance(node, Mapping) and "value" in node:  # Поддержка формата {value: ...}
            candidate = node["value"]
        else:
            candidate = node  # В остальных случаях берем найденный узел
        if isinstance(candidate, str) and candidate:  # Проверяем, что значение строка и не пустая
            return candidate
    return None  # Если ничего не нашли, возвращаем None


def load_clickup_and_openai() -> tuple[str, str, str | None]:
    """Return (api_key, team_id, openai_key). Uses env first, then secrets file.

    Secrets search order mirrors other scripts: $SECRETS_PATH, <repo>/.venv/bin/secrets.json,
    <repo>/../.venv/bin/secrets.json, ~/.config/abacusai_auth_secrets.json
    """
    api_key = os.getenv("CLICKUP_API_KEY")  # Пробуем взять ключ ClickUp из окружения
    team_id = os.getenv("CLICKUP_TEAM_ID")  # Пробуем взять ID команды из окружения
    openai_key = os.getenv("OPENAI_API_KEY")  # Ключ OpenAI (опционально)

    candidates: list[Path] = []  # Места поиска secrets.json
    env_path = os.getenv("SECRETS_PATH")  # Пользовательский путь к секретам
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(ROOT / ".venv/bin/secrets.json")  # Локальный secrets в репозитории
    candidates.append(ROOT.parent / ".venv/bin/secrets.json")  # Фолбэк на родительскую директорию
    candidates.append(Path.home() / ".config/abacusai_auth_secrets.json")  # Глобальный secrets

    for path in candidates:  # Проходим по всем возможным файлам
        try:
            if not path.exists():  # Пропускаем несуществующие пути
                continue
            with open(path, "r", encoding="utf-8") as fh:  # Читаем файл секретов
                payload: Dict[str, Any] = json.load(fh)
            api_key = api_key or _extract(payload, (("clickup", "api_key"), ("telegram", "secrets", "clickup_api_key")))  # Берем ключ ClickUp
            team_id = team_id or _extract(payload, (("clickup", "team_id"), ("telegram", "secrets", "clickup_team_id")))  # Берем team id
            openai_key = openai_key or _extract(payload, (("openai", "api_key"), ("openai", "secrets", "api_key")))  # Берем ключ OpenAI
            if api_key and team_id:  # Как только нашли оба значения — выходим
                break
        except Exception:
            continue  # Игнорируем ошибки чтения и двигаемся дальше

    if not api_key or not team_id:
        raise RuntimeError("Missing ClickUp credentials. Set env vars or provide SECRETS_PATH to a json file.")  # Сообщаем о нехватке данных
    return api_key, team_id, openai_key  # Возвращаем найденные ключи


def resolve_team_id(api_key: str, team_id: str | None) -> str | None:
    """Optionally validate supplied team id against ClickUp /team listing."""
    try:
        headers = {"Authorization": api_key, "Content-Type": "application/json"}  # Заголовки для запроса
        resp = requests.get("https://api.clickup.com/api/v2/team", headers=headers, timeout=15)  # Запрашиваем команды
        resp.raise_for_status()  # Проверяем успешность ответа
        teams = resp.json().get("teams", [])  # Извлекаем список команд
        ids = [str(t.get("id")) for t in teams if t.get("id")]  # Получаем список доступных ID
        if team_id and str(team_id) in ids:  # Если переданный ID валиден
            return str(team_id)
        return ids[0] if ids else team_id  # Иначе возвращаем первый доступный ID
    except Exception:
        return team_id  # При ошибке оставляем исходное значение


def analyze_status_with_openai(openai_key: str | None, transcript: str, task_name: str) -> str:
    """Turn raw transcript into one of the predefined status labels."""
    # If no key, naive heuristic
    if not openai_key:
        t = transcript.lower()  # Приводим к нижнему регистру для поиска ключевых слов
        if any(x in t for x in ["выполн", "сделал", "готово", "готова", "done", "complete"]):
            return "ВЫПОЛНЕНО"  # Считаем выполненной, если нашли маркеры завершения
        return "НЕЯСНО"  # Иначе оставляем статус неопределённым

    try:
        from openai import OpenAI  # lazy import
        client = OpenAI(api_key=openai_key)  # Создаем клиента для GPT
        prompt = (
            "Ты анализируешь ответ сотрудника на напоминание о задаче.\n\n"
            f"Задача: \"{task_name}\"\n"
            f"Ответ сотрудника: \"{transcript}\"\n\n"
            "Определи статус задачи и верни ТОЛЬКО ОДИН из вариантов:\n"
            "- ВЫПОЛНЕНО\n- НЕ_ВЫПОЛНЕНО\n- В_РАБОТЕ\n- ПЕРЕЗВОНИТЬ\n- НЕЯСНО\n\n"
            "Ответ (одним словом):"
        )  # Формируем подсказку для модели
        resp = client.chat.completions.create(
            model="gpt-4o-mini",  # Используем лёгкую модель GPT-4o
            messages=[
                {"role": "system", "content": "Ты помощник для анализа голосовых ответов сотрудников."},  # Роль системы
                {"role": "user", "content": prompt},  # Основной запрос
            ],
            temperature=0.2,  # Минимум случайности
            max_tokens=20,  # Ограничиваем длину ответа
        )
        return (resp.choices[0].message.content or "НЕЯСНО").strip().upper()  # Возвращаем нормализованный ответ
    except Exception:
        return "НЕЯСНО"  # При ошибке считаем ответ непонятным


def post_comment(clickup_token: str, task_id: str, comment_text: str) -> None:
    """Attach a plain comment with the transcription text."""
    url = f"https://api.clickup.com/api/v2/task/{task_id}/comment"  # URL для комментария
    headers = {"Authorization": clickup_token, "Content-Type": "application/json"}  # Авторизация по токену
    requests.post(url, headers=headers, json={"comment_text": comment_text}, timeout=30)  # Отправляем комментарий в ClickUp


def _get_list_statuses(clickup_token: str, task_id: str) -> Tuple[str | None, List[Dict[str, Any]]]:
    """Return list id and statuses so we can map labels to actual ClickUp states."""
    try:
        task = fetch_task(clickup_token, task_id)  # Получаем свежие данные по задаче
        list_id = None  # ID списка, к которому относится задача
        if isinstance(task, dict):
            lst = task.get("list")  # Берем информацию о списке
            if isinstance(lst, dict):
                list_id = lst.get("id")  # Извлекаем ID списка
        if not list_id:
            return None, []  # Если список не найден, возвращаем пустой результат
        url = f"https://api.clickup.com/api/v2/list/{list_id}"  # URL для запроса статусов
        headers = {"Authorization": clickup_token, "Content-Type": "application/json"}  # Заголовки авторизации
        resp = requests.get(url, headers=headers, timeout=30)  # Запрашиваем описание списка
        resp.raise_for_status()  # Проверяем успешность запроса
        payload = resp.json()  # Читаем JSON ответа
        statuses = payload.get("statuses", []) if isinstance(payload, dict) else []  # Достаем статусы списка
        return str(list_id), statuses if isinstance(statuses, list) else []  # Возвращаем ID и список статусов
    except Exception:
        return None, []  # В случае ошибки возвращаем заглушку


def _choose_status_name(label: str, statuses: List[Dict[str, Any]], mapping: Dict[str, str]) -> str | None:
    """Resolve our AI label into one of the list's available status names."""
    label = label.upper()  # Приводим метку к верхнему регистру
    if label in mapping:  # Если пользователь задал прямой маппинг
        return mapping[label]

    desired_type = None  # Тип статуса, который хотим найти
    if label == "ВЫПОЛНЕНО":
        desired_type = "done"
    elif label in ("В_РАБОТЕ",):
        desired_type = "in_progress"
    else:
        desired_type = "open"

    for s in statuses:  # Пытаемся подобрать статус по типу
        if isinstance(s, dict) and s.get("type") == desired_type:
            name = s.get("status") or s.get("name")  # Берем имя статуса
            if name:
                return str(name)

    if label == "ВЫПОЛНЕНО":
        # Try any closed-type as a fallback
        for s in statuses:
            if s.get("type") == "closed":
                name = s.get("status") or s.get("name")
                if name:
                    return str(name)
        for candidate in ("complete", "done", "завершено", "выполнено"):
            for s in statuses:
                if str(s.get("status")).lower() == candidate:
                    return s.get("status")
        return "complete"  # Последний фолбэк для закрытия задачи

    for s in statuses:  # Для остальных статусов ищем любой не закрытый вариант
        if s.get("status_type") != "closed":
            return s.get("status")
    return None  # Если ничего не подошло, возвращаем None


def update_status(clickup_token: str, task_id: str, ai_label: str, mapping: Dict[str, str]) -> Tuple[bool, str]:
    """Set the task status using resolved ClickUp status name."""
    url = f"https://api.clickup.com/api/v2/task/{task_id}"  # URL для обновления задачи
    headers = {"Authorization": clickup_token, "Content-Type": "application/json"}  # Authorize request
    list_id, statuses = _get_list_statuses(clickup_token, task_id)  # Получаем статусы доступные для списка
    chosen = _choose_status_name(ai_label, statuses, mapping)  # Подбираем конкретное имя статуса
    if not chosen:
        chosen = "complete" if ai_label.upper() == "ВЫПОЛНЕНО" else None  # Фолбэк на стандартное имя
    if not chosen:
        return False, "No valid target status resolved"  # Сообщаем, что статус подобрать не удалось

    resp = requests.put(url, headers=headers, json={"status": chosen}, timeout=30)  # Отправляем обновление статуса
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}: {resp.text}"  # Возвращаем ошибку и текст ответа
    return True, chosen  # Сообщаем об успехе и фактическом статусе


def main() -> int:
    """Script entry point coordinating reading, analysis, and ClickUp updates."""
    parser = argparse.ArgumentParser(description="Post transcription to ClickUp and update status using AI analysis.")  # Настраиваем парсер аргументов
    parser.add_argument("--task-id", required=True, help="ClickUp task id to update")  # ID задачи обязательно
    group = parser.add_mutually_exclusive_group(required=False)  # Группа аргументов, которые нельзя сочетать
    group.add_argument("--recording-sid", help="Recording SID to read from transcriptions/<sid>.json")  # SID записи для поиска файла
    group.add_argument("--file", dest="file_path", help="Path to a JSON file with transcription")  # Явный путь до файла
    parser.add_argument("--status", help="Override AI status (e.g., ВЫПОЛНЕНО, НЕ_ВЫПОЛНЕНО)")  # Пользователь может задать статус вручную
    args = parser.parse_args()  # Парсим аргументы командной строки

    # Read transcription either from explicit file or stored transcription by SID.
    text = load_transcription_text(args.recording_sid, args.file_path)  # Получаем текст транскрипции
    if not text:
        print("Transcription text is empty. Provide --file or --recording-sid with valid JSON.", file=sys.stderr)  # Сообщаем об ошибке ввода
        return 2  # Возвращаем код ошибки

    # Load credentials
    try:
        cu_key, team_id, openai_key = load_clickup_and_openai()  # Подгружаем ключи для ClickUp и OpenAI
    except Exception as e:
        print(f"Failed to load credentials: {e}", file=sys.stderr)  # Выводим сообщение, если чтение секретов провалилось
        return 2  # Прерываем выполнение

    # Optionally resolve/validate team id (not strictly needed for task-level ops)
    _ = resolve_team_id(cu_key, team_id)  # Проверяем, что team_id корректен

    # Fetch task name
    try:
        task = fetch_task(cu_key, args.task_id)  # Получаем задачу для дальнейших обновлений
    except Exception as e:
        print(f"Failed to fetch task {args.task_id}: {e}", file=sys.stderr)  # Сообщаем о неудаче запроса
        return 1  # Возвращаем код ошибки ClickUp API

    task_name = task.get("name", "")  # Фиксируем имя задачи (м.б. пустым)

    # Let user override AI decision with --status, otherwise infer from transcript.
    status = args.status or analyze_status_with_openai(openai_key, text, task_name)  # Определяем статус по транскрипции

    # Build mapping from config if present
    mapping: Dict[str, str] = {}
    cfg_path = ROOT / "config.json"
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:  # Читаем конфигурацию проекта
                cfg = json.load(fh)
            cu_cfg = cfg.get("clickup", {}) if isinstance(cfg, dict) else {}  # Выбираем секцию ClickUp
            raw_map = cu_cfg.get("status_mapping", {}) if isinstance(cu_cfg, dict) else {}  # Берем кастомный маппинг статусов
            mapping = {str(k).upper(): str(v) for k, v in raw_map.items() if isinstance(v, str)}  # Приводим ключи к верхнему регистру
        except Exception:
            mapping = {}  # Игнорируем ошибки чтения маппинга

    # Post comment and update status
    comment = f"🗣️ Транскрипция звонка\n```\n{text}\n```"  # Текст комментария с транскрипцией
    try:
        post_comment(cu_key, args.task_id, comment)  # Добавляем комментарий с транскрипцией
    except Exception as e:
        print(f"Warning: failed to post comment: {e}")  # Сообщаем, если не удалось сохранить комментарий

    ok, applied = update_status(cu_key, args.task_id, status, mapping)
    if ok:
        print(f"Updated task {args.task_id}: '{status}' → ClickUp status '{applied}'.")  # Информируем об успешном обновлении
    else:
        print(f"Failed to update task status. Reason: {applied}")  # Выводим причину ошибки обновления
    return 0  # Завершаем выполнение успешно


if __name__ == "__main__":
    raise SystemExit(main())  # Запускаем обработку при вызове скрипта напрямую
