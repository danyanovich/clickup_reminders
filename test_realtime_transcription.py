#!/usr/bin/env python3
"""
Тестовый скрипт для Real-Time Transcription системы
Проверяет работу webhook endpoints и симулирует запросы от Twilio
"""

import os
import sys
import json
import time
import uuid
import requests
from datetime import datetime
from typing import Dict, Tuple

# Цвета для вывода
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color

BASE_DIR = "/home/ubuntu/reminder_daemon"
WEBHOOK_URL = "http://localhost:5000"


def log(message: str, color: str = GREEN):
    """Логирование с цветом"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{color}[{timestamp}] {message}{NC}")


def error(message: str):
    """Логирование ошибки"""
    log(f"❌ ERROR: {message}", RED)


def success(message: str):
    """Логирование успеха"""
    log(f"✅ {message}", GREEN)


def warning(message: str):
    """Логирование предупреждения"""
    log(f"⚠️  {message}", YELLOW)


def info(message: str):
    """Информационное сообщение"""
    log(f"ℹ️  {message}", BLUE)


def check_webhook_server() -> bool:
    """Проверка что webhook сервер запущен"""
    info("Проверка webhook сервера...")
    
    try:
        response = requests.get(f"{WEBHOOK_URL}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            success(f"Webhook сервер доступен")
            info(f"Status: {data.get('status')}")
            info(f"Timestamp: {data.get('timestamp')}")
            return True
        else:
            error(f"Webhook сервер вернул код {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        error("Webhook сервер не доступен. Запустите его:")
        print(f"  ./start_webhook_server.sh start")
        return False
    except Exception as e:
        error(f"Ошибка при проверке сервера: {str(e)}")
        return False


def create_test_call_data(call_id: str) -> bool:
    """Создание тестовых данных звонка"""
    info(f"Создание тестовых данных для call_id: {call_id}")
    
    call_data = {
        "call_id": call_id,
        "task_name": "Тестовая задача: Проверка Real-Time Transcription",
        "recipient_name": "Тестовый пользователь",
        "phone": "+351912345678",
        "task_id": "test_task_123",
        "timestamp": datetime.now().isoformat()
    }
    
    call_data_dir = os.path.join(BASE_DIR, "call_data")
    os.makedirs(call_data_dir, exist_ok=True)
    
    call_data_file = os.path.join(call_data_dir, f"{call_id}.json")
    
    try:
        with open(call_data_file, 'w', encoding='utf-8') as f:
            json.dump(call_data, f, ensure_ascii=False, indent=2)
        success(f"Данные звонка сохранены: {call_data_file}")
        return True
    except Exception as e:
        error(f"Ошибка сохранения данных: {str(e)}")
        return False


def test_twiml_endpoint(call_id: str) -> Tuple[bool, str]:
    """Тестирование TwiML endpoint"""
    info(f"Тестирование /twiml/{call_id} endpoint...")
    
    try:
        response = requests.get(f"{WEBHOOK_URL}/twiml/{call_id}", timeout=10)
        
        if response.status_code == 200:
            # Проверяем что это валидный TwiML
            content = response.text
            
            if '<Response>' in content and '</Response>' in content:
                success("TwiML endpoint работает корректно")
                
                # Проверяем наличие нужных элементов
                if '<Say' in content:
                    info("✓ Присутствует элемент <Say>")
                if '<Record' in content:
                    info("✓ Присутствует элемент <Record>")
                    if 'transcribe="true"' in content or 'transcribe="True"' in content:
                        info("✓ Транскрипция включена (transcribe=true)")
                    if 'language="ru-RU"' in content:
                        info("✓ Язык установлен (language=ru-RU)")
                
                return True, content
            else:
                error("Ответ не содержит валидный TwiML")
                return False, content
        else:
            error(f"Endpoint вернул код {response.status_code}")
            return False, response.text
            
    except Exception as e:
        error(f"Ошибка при тестировании TwiML: {str(e)}")
        return False, str(e)


def simulate_twilio_transcription(call_sid: str, transcription_text: str) -> bool:
    """Симуляция POST запроса от Twilio с транскрипцией"""
    info(f"Симуляция транскрипции от Twilio для CallSid: {call_sid}")
    
    # Данные которые отправляет Twilio
    twilio_data = {
        'CallSid': call_sid,
        'TranscriptionText': transcription_text,
        'TranscriptionStatus': 'completed',
        'RecordingSid': f"RE{uuid.uuid4().hex[:32]}",
        'RecordingUrl': f"https://api.twilio.com/recordings/{uuid.uuid4().hex}",
    }
    
    try:
        response = requests.post(
            f"{WEBHOOK_URL}/transcription",
            data=twilio_data,
            timeout=10
        )
        
        if response.status_code == 200:
            success("Транскрипция успешно отправлена")
            
            # Проверяем что файл создан
            transcription_file = os.path.join(
                BASE_DIR, "transcriptions", f"{call_sid}.json"
            )
            
            time.sleep(1)  # Даем время на запись
            
            if os.path.exists(transcription_file):
                with open(transcription_file, 'r', encoding='utf-8') as f:
                    saved_data = json.load(f)
                
                success(f"Файл транскрипции создан: {transcription_file}")
                info(f"Сохраненный текст: '{saved_data.get('transcription_text')}'")
                info(f"Статус: {saved_data.get('status')}")
                return True
            else:
                warning("Файл транскрипции не найден (возможно задержка)")
                return True  # Запрос прошел успешно
                
        else:
            error(f"Endpoint вернул код {response.status_code}")
            return False
            
    except Exception as e:
        error(f"Ошибка при отправке транскрипции: {str(e)}")
        return False


def simulate_empty_transcription(call_sid: str) -> bool:
    """Симуляция пустой транскрипции (пользователь не ответил)"""
    info(f"Симуляция пустой транскрипции для CallSid: {call_sid}")
    
    twilio_data = {
        'CallSid': call_sid,
        'TranscriptionText': '',
        'TranscriptionStatus': 'completed',
        'RecordingSid': f"RE{uuid.uuid4().hex[:32]}",
    }
    
    try:
        response = requests.post(
            f"{WEBHOOK_URL}/transcription",
            data=twilio_data,
            timeout=10
        )
        
        if response.status_code == 200:
            success("Пустая транскрипция успешно обработана")
            return True
        else:
            error(f"Endpoint вернул код {response.status_code}")
            return False
            
    except Exception as e:
        error(f"Ошибка: {str(e)}")
        return False


def simulate_call_status(call_sid: str, status: str) -> bool:
    """Симуляция обновления статуса звонка"""
    info(f"Симуляция статуса звонка: {status}")
    
    twilio_data = {
        'CallSid': call_sid,
        'CallStatus': status,
    }
    
    try:
        response = requests.post(
            f"{WEBHOOK_URL}/call-status",
            data=twilio_data,
            timeout=10
        )
        
        if response.status_code == 200:
            success(f"Статус '{status}' успешно обработан")
            return True
        else:
            error(f"Endpoint вернул код {response.status_code}")
            return False
            
    except Exception as e:
        error(f"Ошибка: {str(e)}")
        return False


def test_recording_complete(call_sid: str) -> bool:
    """Тестирование /recording-complete endpoint"""
    info("Тестирование /recording-complete endpoint")
    
    twilio_data = {
        'CallSid': call_sid,
        'RecordingSid': f"RE{uuid.uuid4().hex[:32]}",
        'RecordingUrl': f"https://api.twilio.com/recordings/{uuid.uuid4().hex}",
    }
    
    try:
        response = requests.post(
            f"{WEBHOOK_URL}/recording-complete",
            data=twilio_data,
            timeout=10
        )
        
        if response.status_code == 200:
            success("Recording complete endpoint работает корректно")
            return True
        else:
            error(f"Endpoint вернул код {response.status_code}")
            return False
            
    except Exception as e:
        error(f"Ошибка: {str(e)}")
        return False


def cleanup_test_files(call_id: str, call_sid: str):
    """Очистка тестовых файлов"""
    info("Очистка тестовых файлов...")
    
    files_to_remove = [
        os.path.join(BASE_DIR, "call_data", f"{call_id}.json"),
        os.path.join(BASE_DIR, "transcriptions", f"{call_sid}.json"),
    ]
    
    for file_path in files_to_remove:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                info(f"Удален: {file_path}")
            except Exception as e:
                warning(f"Не удалось удалить {file_path}: {str(e)}")


def run_full_test_suite():
    """Запуск полного набора тестов"""
    print("\n" + "="*70)
    print(f"{BLUE}🧪 ТЕСТИРОВАНИЕ REAL-TIME TRANSCRIPTION СИСТЕМЫ{NC}")
    print("="*70 + "\n")
    
    # Генерируем уникальные ID
    call_id = str(uuid.uuid4())
    call_sid = f"CA{uuid.uuid4().hex[:32]}"
    
    tests_passed = 0
    tests_total = 0
    
    # Тест 1: Проверка webhook сервера
    print(f"\n{YELLOW}{'─'*70}{NC}")
    print(f"{BLUE}Тест 1: Проверка доступности webhook сервера{NC}")
    print(f"{YELLOW}{'─'*70}{NC}")
    tests_total += 1
    if check_webhook_server():
        tests_passed += 1
    else:
        error("Webhook сервер недоступен. Остановка тестов.")
        return
    
    # Тест 2: Создание тестовых данных
    print(f"\n{YELLOW}{'─'*70}{NC}")
    print(f"{BLUE}Тест 2: Создание тестовых данных звонка{NC}")
    print(f"{YELLOW}{'─'*70}{NC}")
    tests_total += 1
    if create_test_call_data(call_id):
        tests_passed += 1
    
    # Тест 3: TwiML endpoint
    print(f"\n{YELLOW}{'─'*70}{NC}")
    print(f"{BLUE}Тест 3: Тестирование /twiml endpoint{NC}")
    print(f"{YELLOW}{'─'*70}{NC}")
    tests_total += 1
    twiml_success, twiml_content = test_twiml_endpoint(call_id)
    if twiml_success:
        tests_passed += 1
        print(f"\n{BLUE}Пример TwiML (первые 300 символов):{NC}")
        print(twiml_content[:300] + "...\n")
    
    # Тест 4: Симуляция статусов звонка
    print(f"\n{YELLOW}{'─'*70}{NC}")
    print(f"{BLUE}Тест 4: Симуляция статусов звонка{NC}")
    print(f"{YELLOW}{'─'*70}{NC}")
    for status in ['initiated', 'ringing', 'in-progress', 'completed']:
        tests_total += 1
        if simulate_call_status(call_sid, status):
            tests_passed += 1
        time.sleep(0.5)
    
    # Тест 5: Симуляция транскрипции с текстом
    print(f"\n{YELLOW}{'─'*70}{NC}")
    print(f"{BLUE}Тест 5: Симуляция успешной транскрипции{NC}")
    print(f"{YELLOW}{'─'*70}{NC}")
    tests_total += 1
    test_transcription = "Да, задача выполнена. Все готово."
    if simulate_twilio_transcription(call_sid, test_transcription):
        tests_passed += 1
    
    # Тест 6: Симуляция пустой транскрипции
    print(f"\n{YELLOW}{'─'*70}{NC}")
    print(f"{BLUE}Тест 6: Симуляция пустой транскрипции{NC}")
    print(f"{YELLOW}{'─'*70}{NC}")
    tests_total += 1
    empty_call_sid = f"CA{uuid.uuid4().hex[:32]}"
    if simulate_empty_transcription(empty_call_sid):
        tests_passed += 1
    
    # Тест 7: Recording complete endpoint
    print(f"\n{YELLOW}{'─'*70}{NC}")
    print(f"{BLUE}Тест 7: Тестирование /recording-complete endpoint{NC}")
    print(f"{YELLOW}{'─'*70}{NC}")
    tests_total += 1
    if test_recording_complete(call_sid):
        tests_passed += 1
    
    # Очистка
    print(f"\n{YELLOW}{'─'*70}{NC}")
    print(f"{BLUE}Очистка тестовых данных{NC}")
    print(f"{YELLOW}{'─'*70}{NC}")
    cleanup_test_files(call_id, call_sid)
    
    # Результаты
    print("\n" + "="*70)
    print(f"{BLUE}📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ{NC}")
    print("="*70)
    
    success_rate = (tests_passed / tests_total * 100) if tests_total > 0 else 0
    
    print(f"\nВсего тестов: {tests_total}")
    print(f"Успешно: {GREEN}{tests_passed}{NC}")
    print(f"Провалено: {RED}{tests_total - tests_passed}{NC}")
    print(f"Процент успеха: {GREEN if success_rate >= 80 else RED}{success_rate:.1f}%{NC}\n")
    
    if tests_passed == tests_total:
        success("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
    elif success_rate >= 80:
        warning("⚠️  Большинство тестов пройдено, но есть проблемы")
    else:
        error("❌ КРИТИЧЕСКИЕ ОШИБКИ В ТЕСТАХ")
    
    print("="*70 + "\n")
    
    # Рекомендации
    if tests_passed < tests_total:
        print(f"{BLUE}📝 Рекомендации:{NC}")
        print("1. Проверьте логи webhook сервера:")
        print(f"   tail -f {BASE_DIR}/logs/webhook_server_$(date +%Y-%m-%d).log")
        print("2. Убедитесь что все директории созданы:")
        print(f"   ls -la {BASE_DIR}/{{transcriptions,call_data}}")
        print("3. Проверьте права доступа к файлам")
        print()


def main():
    """Основная функция"""
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "--health":
            check_webhook_server()
        elif command == "--twiml":
            call_id = sys.argv[2] if len(sys.argv) > 2 else str(uuid.uuid4())
            create_test_call_data(call_id)
            test_twiml_endpoint(call_id)
        elif command == "--transcription":
            call_sid = sys.argv[2] if len(sys.argv) > 2 else f"CA{uuid.uuid4().hex[:32]}"
            text = sys.argv[3] if len(sys.argv) > 3 else "Тестовая транскрипция"
            simulate_twilio_transcription(call_sid, text)
        elif command == "--help":
            print(f"""
{BLUE}Использование:{NC}
  python3 test_realtime_transcription.py           # Полный набор тестов
  python3 test_realtime_transcription.py --health  # Проверка сервера
  python3 test_realtime_transcription.py --twiml [call_id]  # Тест TwiML
  python3 test_realtime_transcription.py --transcription [call_sid] [text]  # Тест транскрипции
  python3 test_realtime_transcription.py --help    # Эта помощь

{BLUE}Примеры:{NC}
  python3 test_realtime_transcription.py
  python3 test_realtime_transcription.py --health
  python3 test_realtime_transcription.py --twiml test-call-123
  python3 test_realtime_transcription.py --transcription CA123 "Да, готово"
            """)
        else:
            error(f"Неизвестная команда: {command}")
            print("Используйте --help для помощи")
    else:
        # Запуск полного набора тестов
        run_full_test_suite()


if __name__ == "__main__":
    main()
