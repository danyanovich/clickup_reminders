#!/usr/bin/env python3
"""
Flask Webhook Server for Twilio Real-Time Transcription
Обработка webhook'ов от Twilio для транскрипции звонков
"""

import os
import json
from datetime import datetime
from flask import Flask, request, Response, jsonify
import requests
import tempfile
from twilio.twiml.voice_response import VoiceResponse
import pytz
from openai import OpenAI

app = Flask(__name__)

# Пути к директориям
PROJECT_ROOT = Path(__file__).resolve().parent
LOGS_DIR = PROJECT_ROOT / "var" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
# Assuming CALL_DATA_DIR and TRANSCRIPTIONS_DIR are still needed and defined relative to PROJECT_ROOT
CALL_DATA_DIR = PROJECT_ROOT / "call_data"
TRANSCRIPTIONS_DIR = PROJECT_ROOT / "transcriptions"

os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
os.makedirs(CALL_DATA_DIR, exist_ok=True)


# Timezone
TZ = pytz.timezone("Europe/Lisbon")

# Убеждаемся что директории существуют
# The previous os.makedirs calls are now handled by the new path definitions and LOGS_DIR.mkdir()
# os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
# os.makedirs(CALL_DATA_DIR, exist_ok=True)
# os.makedirs(LOGS_DIR, exist_ok=True)


def log_message(message: str, level: str = "INFO"):
    """Логирование сообщений"""
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    print(log_entry)
    
    # Запись в файл
    log_file = os.path.join(LOGS_DIR, f"webhook_server_{datetime.now(TZ).strftime('%Y-%m-%d')}.log")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(log_entry + "\n")


def _load_config():
    """Читает конфигурацию через единый модуль."""
    try:
        return load_cfg()
    except Exception as exc:
        log_message(f"Ошибка загрузки конфигурации: {exc}", "ERROR")
        return None

def _get_secrets():
    """Читает секреты через единый модуль."""
    try:
        return load_secs()
    except Exception as exc:
        log_message(f"Ошибка загрузки секретов: {exc}", "ERROR")
        return None


CONFIG = _load_config()
VOICE_SETTINGS = CONFIG.get("voice_settings", {})
VOICE_LANGUAGE = VOICE_SETTINGS.get("language", "ru-RU")
VOICE_NAME = VOICE_SETTINGS.get("voice", "Polly.Tatyana")


def load_call_data(call_id: str) -> dict:
    """Загрузка данных звонка"""
    call_data_file = os.path.join(CALL_DATA_DIR, f"{call_id}.json")
    
    if not os.path.exists(call_data_file):
        log_message(f"Файл данных звонка не найден: {call_id}", "WARNING")
        return None
    
    try:
        with open(call_data_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log_message(f"Ошибка загрузки данных звонка {call_id}: {str(e)}", "ERROR")
        return None


def save_transcription(call_sid: str, transcription_text: str, status: str = "completed"):
    """Сохранение транскрипции"""
    transcription_data = {
        "call_sid": call_sid,
        "transcription_text": transcription_text,
        "timestamp": datetime.now(TZ).isoformat(),
        "status": status
    }
    
    transcription_file = os.path.join(TRANSCRIPTIONS_DIR, f"{call_sid}.json")
    
    try:
        with open(transcription_file, 'w', encoding='utf-8') as f:
            json.dump(transcription_data, f, ensure_ascii=False, indent=2)
        
        log_message(f"Транскрипция сохранена: {call_sid}")
        return True
    except Exception as e:
        log_message(f"Ошибка сохранения транскрипции: {str(e)}", "ERROR")
        return False


@app.route('/twiml/<call_id>', methods=['GET', 'POST'])
def generate_twiml(call_id):
    """
    Генерация TwiML для звонка
    Этот endpoint вызывается Twilio когда звонок начинается
    """
    log_message(f"Получен запрос TwiML для call_id: {call_id}")
    
    # Загружаем данные звонка
    call_data = load_call_data(call_id)
    
    if not call_data:
        log_message(f"Данные звонка не найдены для call_id: {call_id}", "ERROR")
        # Возвращаем базовый TwiML
        response = VoiceResponse()
        response.say("Извините, произошла ошибка. Попробуйте позже.", language='ru-RU')
        return Response(str(response), mimetype='text/xml')
    
    # Извлекаем данные
    task_name = call_data.get('task_name', 'задача')
    recipient_name = call_data.get('recipient_name', 'коллега')
    
    # Создаем TwiML
    response = VoiceResponse()
    
    # Приветствие и вопрос
    message = f"Привет {recipient_name}! Напоминание о задаче: {task_name}. Задача выполнена?"
    response.say(message, language=VOICE_LANGUAGE, voice=VOICE_NAME)
    
    # Записываем ответ с транскрипцией
    response.record(
        transcribe=True,
        transcribe_callback=f'/transcription',
        language='ru-RU',
        play_beep=False,
        max_length=60,
        timeout=5,
        action='/recording-complete',
        method='POST'
    )
    
    # Если не дождались ответа
    response.say("Спасибо за уделённое время. До свидания!", language=VOICE_LANGUAGE, voice=VOICE_NAME)
    
    log_message(f"TwiML сгенерирован для call_id: {call_id}")
    
    return Response(str(response), mimetype='text/xml')


@app.route('/transcription', methods=['POST'])
def handle_transcription():
    """
    Обработка транскрипции от Twilio
    Twilio отправляет POST запрос с транскрипцией после завершения записи
    """
    try:
        # Получаем данные от Twilio
        call_sid = request.form.get('CallSid', '')
        transcription_text = request.form.get('TranscriptionText', '')
        transcription_status = request.form.get('TranscriptionStatus', 'completed')
        recording_sid = request.form.get('RecordingSid', '')
        
        log_message(f"Получена транскрипция для CallSid: {call_sid}")
        log_message(f"Статус транскрипции: {transcription_status}")
        log_message(f"Текст транскрипции: {transcription_text}")
        
        # Сохраняем транскрипцию
        if transcription_text:
            save_transcription(call_sid, transcription_text, transcription_status)
        else:
            log_message(f"Пустая транскрипция для CallSid: {call_sid}", "WARNING")
            # Сохраняем пустую транскрипцию со статусом
            save_transcription(call_sid, "", "empty")
        
        return Response('OK', status=200)
    
    except Exception as e:
        log_message(f"Ошибка обработки транскрипции: {str(e)}", "ERROR")
        return Response('Error', status=500)


@app.route('/recording-complete', methods=['POST'])
def recording_complete():
    """
    Обработка завершения записи
    Вызывается после окончания записи, но ДО транскрипции
    """
    try:
        call_sid = request.form.get('CallSid', '')
        recording_sid = request.form.get('RecordingSid', '')
        recording_url = request.form.get('RecordingUrl', '')

        log_message(f"Запись завершена для CallSid: {call_sid}")
        log_message(f"RecordingSid: {recording_sid}")

        if not recording_url:
            log_message("RecordingUrl не предоставлен в запросе", "WARNING")
            response = VoiceResponse()
            return Response(str(response), mimetype='text/xml')

        # Попытка скачать wav-версию записи, иначе оригинальный URL
        audio_url_candidates = [recording_url + '.wav', recording_url + '.mp3', recording_url]

        tw_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        tw_token = os.environ.get('TWILIO_AUTH_TOKEN')
        auth = (tw_sid, tw_token) if tw_sid and tw_token else None

        r = None
        for url in audio_url_candidates:
            try:
                log_message(f"Пробуем скачать запись по URL: {url}")
                r = requests.get(url, auth=auth, stream=True, timeout=30)
                r.raise_for_status()
                audio_url = url
                break
            except Exception as e:
                log_message(f"Не удалось скачать по {url}: {str(e)}", "DEBUG")
                r = None

        if not r:
            log_message("Не удалось скачать запись с Twilio", "ERROR")
            response = VoiceResponse()
            return Response(str(response), mimetype='text/xml')

        # Сохраняем во временный файл
        suffix = os.path.splitext(audio_url)[1] or '.wav'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    tf.write(chunk)
            tmp_path = tf.name

        log_message(f"Запись сохранена во временный файл: {tmp_path}")

        # Транскрипция через OpenAI Whisper
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            log_message('OPENAI_API_KEY не задан', 'ERROR')
            # удаляем временный файл и возвращаем
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            response = VoiceResponse()
            return Response(str(response), mimetype='text/xml')

        transcription_text = ''
        try:
            client = OpenAI(api_key=api_key)
            with open(tmp_path, 'rb') as audio_file:
                log_message('Отправка файла в OpenAI Whisper для распознавания')
                resp = client.audio.transcriptions.create(
                    model='whisper-1',
                    file=audio_file
                )
                if hasattr(resp, 'text'):
                    transcription_text = resp.text
                elif isinstance(resp, dict):
                    transcription_text = resp.get('text', '')
                else:
                    transcription_text = str(resp)

            log_message(f"Транскрипция получена (длина {len(transcription_text)}): {transcription_text[:80]}")

            # Сохраняем транскрипцию в локальную систему
            save_transcription(call_sid or recording_sid or 'unknown', transcription_text, status='completed')

        except Exception as e:
            log_message(f"Ошибка при транскрипции/отправке в Whisper: {str(e)}", 'ERROR')
            save_transcription(call_sid or recording_sid or 'unknown', '', status='error')
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        # Возвращаем простой ответ TwiML
        response = VoiceResponse()
        return Response(str(response), mimetype='text/xml')
    
    except Exception as e:
        log_message(f"Ошибка обработки завершения записи: {str(e)}", "ERROR")
        response = VoiceResponse()
        return Response(str(response), mimetype='text/xml')


@app.route('/call-status', methods=['POST'])
def call_status():
    """
    Отслеживание статуса звонка
    Twilio отправляет обновления статуса звонка
    """
    try:
        call_sid = request.form.get('CallSid', '')
        call_status = request.form.get('CallStatus', '')
        
        log_message(f"Статус звонка {call_sid}: {call_status}")
        
        # Можно сохранять статусы для мониторинга
        
        return Response('OK', status=200)
    
    except Exception as e:
        log_message(f"Ошибка обработки статуса звонка: {str(e)}", "ERROR")
        return Response('Error', status=500)


@app.route('/health', methods=['GET'])
def health_check():
    """Проверка здоровья сервера"""
    return {
        'status': 'healthy',
        'timestamp': datetime.now(TZ).isoformat(),
        'transcriptions_dir': os.path.exists(TRANSCRIPTIONS_DIR),
        'call_data_dir': os.path.exists(CALL_DATA_DIR)
    }


@app.route('/', methods=['GET'])
def index():
    """Главная страница"""
    return """
    <html>
    <head><title>Twilio Webhook Server</title></head>
    <body>
        <h1>🎙️ Twilio Webhook Server</h1>
        <p>Сервер работает и готов принимать webhook'и от Twilio</p>
        <h2>Available Endpoints:</h2>
        <ul>
            <li><code>GET /health</code> - Health check</li>
            <li><code>GET/POST /twiml/&lt;call_id&gt;</code> - Генерация TwiML</li>
            <li><code>POST /transcription</code> - Приём транскрипций</li>
            <li><code>POST /recording-complete</code> - Завершение записи</li>
            <li><code>POST /call-status</code> - Статус звонка</li>
        </ul>
    </body>
    </html>
    """


if __name__ == '__main__':
    log_message("="*60)
    log_message("🚀 ЗАПУСК WEBHOOK СЕРВЕРА")
    log_message("="*60)
    log_message(f"Transcriptions directory: {TRANSCRIPTIONS_DIR}")
    log_message(f"Call data directory: {CALL_DATA_DIR}")
    log_message(f"Logs directory: {LOGS_DIR}")
    
    port = int(os.getenv("WEBHOOK_PORT", "5000"))
    host = os.getenv("WEBHOOK_HOST", "127.0.0.1")
    # Запускаем сервер
    try:
        app.run(
            host=host,
            port=port,
            debug=False,  # В продакшене должно быть False
            threaded=True
        )
    except Exception as exc:
        log_message(f"Ошибка запуска Flask сервера: {exc}", "ERROR")
        raise
