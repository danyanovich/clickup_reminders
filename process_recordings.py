#!/usr/bin/env python3
"""
Вспомогательный скрипт для выгрузки исторических записей из Twilio.

Возможности:
  * Скачивание mp3-файлов звонков в каталог `recordings/`
  * (опционально) автоматическая транскрипция через OpenAI Whisper
  * (опционально) простая аналитика ответа через GPT

Пример запуска:
    python3 process_recordings.py --hours 72 --limit 50 --save-audio

Учётные данные берутся из переменных окружения. Если их нет, то
скрипт попробует загрузить `config.json` и `secrets` как reminder_system.
"""

import argparse  # Парсер аргументов командной строки
import json  # Работа с JSON-файлами
import os  # Доступ к переменным окружения и путям
from datetime import datetime, timedelta  # Работа с датами и интервалами времени
from pathlib import Path  # Удобная работа с путями
from typing import Dict, Optional  # Аннотации типов для читаемости

import requests  # HTTP-запросы к Twilio и ClickUp
from openai import OpenAI  # Клиент OpenAI для транскрипции и анализа
from twilio.rest import Client  # SDK Twilio для получения записей

# Derive paths relative to script location so it works both locally and on server.
BASE_DIR = os.getenv("BASE_DIR") or os.path.dirname(os.path.abspath(__file__))  # Определяем корневую директорию
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")  # Путь до конфигурации
DEFAULT_SECRETS_PATH = os.path.join(os.path.dirname(BASE_DIR), ".venv", "bin", "secrets.json")  # Стандартный secrets.json
SECRETS_PATH = os.getenv("SECRETS_PATH") or DEFAULT_SECRETS_PATH  # Приоритет переменной окружения для секретов
TRANSCRIPTIONS_DIR = os.path.join(BASE_DIR, "transcriptions")  # Каталог для JSON с транскрипциями
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")  # Каталог для mp3-записей
LAST_RUN_FILE = os.path.join(BASE_DIR, "var", "last_recording_check.txt")  # Файл с timestamp последней проверки

# Ensure artifact directories exist before first run.
os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)  # Создаем папку для транскрипций, если её нет
os.makedirs(RECORDINGS_DIR, exist_ok=True)  # Создаем папку для записей, если её нет
os.makedirs(os.path.dirname(LAST_RUN_FILE), exist_ok=True)  # Создаем папку var, если её нет


def _load_json(path: str) -> Dict:
    """Safe JSON reader that returns empty dict when file missing."""
    if not os.path.exists(path):  # Если файла нет, возвращаем пустой словарь
        return {}
    with open(path, "r", encoding="utf-8") as f:  # Открываем файл в режиме чтения
        return json.load(f)  # Парсим JSON и возвращаем данные


def resolve_credentials() -> Dict[str, Optional[str]]:
    """Возвращает словарь с twilio/openai credentials.

    Приоритет: переменные окружения -> secrets -> config.
    """
    # Start with direct environment variables to allow overrides during runtime.
    creds = {
        "twilio_account_sid": os.getenv("TWILIO_ACCOUNT_SID", "").strip(),  # SID аккаунта Twilio
        "twilio_auth_token": os.getenv("TWILIO_AUTH_TOKEN", "").strip(),  # Auth Token Twilio
        "openai_api_key": os.getenv("OPENAI_API_KEY", "").strip() if os.getenv("OPENAI_API_KEY") else None,  # Ключ OpenAI для Whisper/GPT
    }
    
    # If we have all Twilio credentials from env, skip file reading
    if creds["twilio_account_sid"] and creds["twilio_auth_token"]:
        print("✅ Using Twilio credentials from environment variables")
        return creds
    
    # Otherwise, try to load from files
    config = _load_json(CONFIG_PATH)  # Читаем config.json, если он существует
    secrets = _load_json(SECRETS_PATH)  # Читаем secrets.json из выбранного пути

    def _section_value(section: Dict, key: str) -> Optional[str]:
        """Support both flat {key: value} and nested {secrets: {key: {value}}} layouts."""
        if not isinstance(section, dict):  # Если секция не словарь, возвращаем None
            return None
        # Новый формат
        value = section.get(key)  # Пытаемся взять значение напрямую
        if isinstance(value, dict):  # Если значение само словарь
            if "value" in value:  # И внутри есть ключ value
                return value["value"]  # Возвращаем вложенное значение
        elif value:  # Если значение строка и не пустое
            return value  # Возвращаем найденную строку
        # Старый формат
        nested = section.get("secrets")  # Проверяем вложенный словарь secrets
        if isinstance(nested, dict):
            value = nested.get(key)  # Берем ключ из вложенной структуры
            if isinstance(value, dict) and "value" in value:  # Опять проверяем вложенный value
                return value["value"]
            return value  # Возвращаем найденное значение или None
        return None  # Если ничего не нашли, возвращаем None

    # Twilio credentials may come from secrets.json/config.json if env is empty.
    if not creds["twilio_account_sid"] or not creds["twilio_auth_token"]:
        tw_section = secrets.get("twilio", {})  # Берем Twilio-секцию из секретов
        if tw_section:
            creds["twilio_account_sid"] = creds["twilio_account_sid"] or _section_value(tw_section, "account_sid")  # Пытаемся достать SID
            creds["twilio_auth_token"] = creds["twilio_auth_token"] or _section_value(tw_section, "auth_token")  # Пытаемся достать токен
        if (not creds["twilio_account_sid"] or not creds["twilio_auth_token"]) and "twilio" in config:
            tw = config["twilio"]  # Если всё ещё пусто, берем из config.json
            creds["twilio_account_sid"] = creds["twilio_account_sid"] or tw.get("account_sid")
            creds["twilio_auth_token"] = creds["twilio_auth_token"] or tw.get("auth_token")

    # OpenAI API key is optional; only needed when transcription/analysis enabled.
    if not creds["openai_api_key"]:
        creds["openai_api_key"] = _section_value(secrets.get("openai", {}), "api_key")  # Ищем ключ OpenAI в секретах
    
    return creds  # Возвращаем итоговый словарь с ключами


def save_result(recording_sid: str, data: Dict):
    """Persist transcription/analysis bundle next to other artifacts."""
    path = Path(TRANSCRIPTIONS_DIR) / f"{recording_sid}.json"  # Формируем путь файла для записи
    with open(path, "w", encoding="utf-8") as f:  # Открываем файл для записи в UTF-8
        json.dump(data, f, ensure_ascii=False, indent=2)  # Сохраняем словарь в JSON с форматированием


def download_recording_mp3(account_sid: str, auth_token: str, recording_sid: str, target_dir: str, timeout: int = 60) -> Optional[str]:
    """Скачивает запись Twilio и сохраняет в target_dir. Возвращает путь к файлу."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Recordings/{recording_sid}.mp3"  # Формируем URL для скачивания mp3
    response = requests.get(url, auth=(account_sid, auth_token), stream=True, timeout=timeout)  # Выполняем авторизованный GET
    response.raise_for_status()  # Проверяем, что запрос завершился без ошибок

    path = Path(target_dir) / f"{recording_sid}.mp3"  # Указываем, куда сохранить mp3
    with open(path, "wb") as fh:  # Открываем файл для записи байтов
        for chunk in response.iter_content(8192):  # Читаем ответ кусками по 8 КБ
            if chunk:  # Пропускаем пустые блоки
                fh.write(chunk)  # Записываем аудио в файл
    return str(path)  # Возвращаем путь к сохраненной записи


def build_openai_client(api_key: Optional[str]) -> Optional[OpenAI]:
    """Create OpenAI SDK client when key available."""
    if not api_key:  # Если ключа нет, клиент не создаем
        return None
    return OpenAI(api_key=api_key)  # Возвращаем экземпляр клиента OpenAI


def transcribe_with_openai(client: OpenAI, file_path: str, language: str = "ru") -> str:
    """Отправляет файл в Whisper и возвращает текст."""
    with open(file_path, "rb") as audio_file:  # Открываем аудио в бинарном режиме
        resp = client.audio.transcriptions.create(
            model="whisper-1",  # Выбираем модель Whisper
            file=audio_file,  # Передаем файл записи
            language=language  # Указываем язык распознавания
        )
    if hasattr(resp, "text"):  # Новый клиент возвращает объект с полем text
        return resp.text  # Возвращаем текстовое содержимое
    if isinstance(resp, dict):  # Защита на случай словаря
        return resp.get("text", "")  # Достаем текст из словаря
    return str(resp)  # Фолбэк на строковое представление


def analyze_with_gpt(client: OpenAI, text: str, model: str = "gpt-4o-mini") -> Dict:
    """Небольшой анализ текста разговора через Chat Completions."""
    resp = client.chat.completions.create(
        model=model,  # Выбираем модель для анализа
        messages=[
            {"role": "system", "content": "Проанализируй разговор: выдели ключевые факты, намерения и задачи."},  # Инструкции для модели
            {"role": "user", "content": text},  # Передаем текст транскрипции
        ],
        temperature=0.0,  # Используем детерминированный ответ
        max_tokens=500,  # Ограничиваем объем ответа
    )
    message = resp.choices[0].message  # Берем первое сообщение из ответа
    analysis_text = message.content if hasattr(message, "content") else str(message)  # Достаем текст анализа
    return {"analysis": analysis_text}  # Возвращаем результат в виде словаря


def get_last_check_time() -> datetime:
    """Читает timestamp последней проверки или возвращает время 1 час назад."""
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, "r") as f:
                timestamp_str = f.read().strip()
                return datetime.fromisoformat(timestamp_str)
        except Exception as e:
            print(f"⚠️  Ошибка чтения {LAST_RUN_FILE}: {e}")
    # По умолчанию проверяем записи за последний час
    return datetime.utcnow() - timedelta(hours=1)


def save_last_check_time():
    """Сохраняет текущее время как timestamp последней проверки."""
    with open(LAST_RUN_FILE, "w") as f:
        f.write(datetime.utcnow().isoformat())


def process_recent_recordings(hours: int, limit: int, analyze: bool, save_audio: bool, incremental: bool = True):
    """Main worker: pulls Twilio recordings and optionally transcribes/analyzes them."""
    creds = resolve_credentials()  # Получаем учётные данные из всех доступных источников

    if not creds["twilio_account_sid"] or not creds["twilio_auth_token"]:  # Без Twilio-ключей продолжать нельзя
        raise RuntimeError("Twilio credentials are missing. Set TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN or update secrets.")

    # Query Twilio for all recordings in the requested time window.
    client = Client(creds["twilio_account_sid"], creds["twilio_auth_token"])  # Создаем клиент Twilio
    
    # Test authentication by fetching account info
    try:
        account = client.api.accounts(creds["twilio_account_sid"]).fetch()
        print(f"✅ Twilio authentication successful. Account: {account.friendly_name}")
    except Exception as e:
        print(f"❌ Twilio authentication failed: {e}")
        raise
    
    if incremental:
        # Используем timestamp последней проверки
        since = get_last_check_time()
        print(f"🔍 Проверка новых записей с {since.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    else:
        # Используем заданный интервал в часах
        since = datetime.utcnow() - timedelta(hours=hours)
        print(f"🔍 Проверка записей за последние {hours} часов")
    
    recordings = client.recordings.list(date_created_after=since, limit=limit)  # Запрашиваем список записей

    print(f"Найдено записей: {len(recordings)}")  # Логируем количество найденных записей

    # Prepare OpenAI client only if transcription requested and key present.
    openai_client = build_openai_client(creds["openai_api_key"]) if analyze else None  # Создаем клиента OpenAI по требованию
    if analyze and not openai_client:  # Если анализ включен, но ключа нет
        print("⚠️  OPENAI_API_KEY не найден, анализ и транскрипция отключены.")  # Предупреждаем пользователя
        analyze = False  # Отключаем анализ, чтобы избежать ошибок

    for rec in recordings:  # Обрабатываем каждую найденную запись
        sid = getattr(rec, "sid", None)  # SID самой записи
        call_sid = getattr(rec, "call_sid", None)  # SID звонка, к которому относится запись
        date_created = getattr(rec, "date_created", None)  # Когда запись была создана
        print(f"\n→ Запись {sid} (CallSid: {call_sid}, дата: {date_created})")  # Выводим информацию о записи

        audio_path = None  # Путь к локальному файлу записи (если сохраним)
        if save_audio:
            try:
                # Download mp3 locally so it can be archived or further processed.
                audio_path = download_recording_mp3(
                    creds["twilio_account_sid"],
                    creds["twilio_auth_token"],
                    sid,
                    RECORDINGS_DIR
                )
                print(f"   Сохранено аудио: {audio_path}")  # Подтверждаем, что mp3 сохранено
            except Exception as e:
                print(f"   Ошибка скачивания аудио {sid}: {e}")  # Сообщаем о проблеме скачивания
                continue  # Переходим к следующей записи

        transcription = ""  # Текстовый результат транскрипции
        analysis = None  # Объект с результатами анализа GPT
        if analyze and openai_client and audio_path:  # Запускаем транскрибирование, если есть все данные
            try:
                # Whisper transcription gives us raw text for later review.
                transcription = transcribe_with_openai(openai_client, audio_path)  # Превращаем звук в текст
                print(f"   Транскрипция: {len(transcription)} символов")
                # Optional summary to quickly understand the call outcome.
                analysis = analyze_with_gpt(openai_client, transcription)  # Получаем краткий анализ ответа
            except Exception as e:
                print(f"   Ошибка при транскрипции/анализе {sid}: {e}")  # Сообщаем об ошибке распознавания

        if transcription or analysis:
            # Persist artifacts even if only one of transcription/analysis succeeded.
            entry = {
                "recording_sid": sid,  # SID записи для идентификации
                "call_sid": call_sid,  # Исходный звонок
                "timestamp_utc": datetime.utcnow().isoformat(),  # Время сохранения результата
                "transcription": transcription,  # Текст транскрипции (может быть пустым)
                "analysis": analysis,  # Результат анализа (может быть None)
            }
            save_result(sid, entry)  # Сохраняем результат в файловой системе
            print(f"   Итог сохранён в {TRANSCRIPTIONS_DIR}/{sid}.json")  # Логируем путь к файлу
    
    # Сохраняем timestamp текущей проверки для следующего запуска
    save_last_check_time()
    print(f"\n✅ Timestamp последней проверки обновлен")


def main():
    """Parse CLI flags and trigger the recording processor."""
    parser = argparse.ArgumentParser(description="Скачать исторические записи из Twilio.")  # Готовим парсер аргументов
    parser.add_argument("--hours", type=int, default=24, help="Сколько часов назад искать записи (UTC)")  # Интервал поиска
    parser.add_argument("--limit", type=int, default=100, help="Максимум записей за запуск")  # Ограничение количества записей
    parser.add_argument("--no-analyze", dest="analyze", action="store_false", help="Отключить транскрипцию и анализ")  # Флаг для отключения анализа
    parser.add_argument("--save-audio", action="store_true", help="Сохранять mp3 в папку recordings/")  # Флаг для сохранения mp3
    parser.add_argument("--no-incremental", dest="incremental", action="store_false", help="Использовать --hours вместо инкрементальной проверки")  # Флаг для отключения инкрементального режима
    args = parser.parse_args()  # Разбираем аргументы командной строки

    process_recent_recordings(hours=args.hours, limit=args.limit, analyze=args.analyze, save_audio=args.save_audio, incremental=args.incremental)  # Запускаем основную обработку


if __name__ == "__main__":
    main()  # Точка входа при запуске скрипта напрямую
