#!/usr/bin/env python3
"""
Обработка входящих SMS ответов через Twilio API
Периодически проверяет новые SMS и обновляет задачи в ClickUp
"""

import os
import sys
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from reminder_system import ReminderSystem
except ImportError:
    print("❌ Не удалось импортировать ReminderSystem")
    sys.exit(1)

# Файл для отслеживания последнего обработанного SMS
LAST_PROCESSED_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "var",
    "last_processed_sms.txt"
)

def get_last_processed_time() -> datetime:
    """Получить время последней проверки SMS"""
    try:
        if os.path.exists(LAST_PROCESSED_FILE):
            with open(LAST_PROCESSED_FILE, 'r') as f:
                timestamp_str = f.read().strip()
                return datetime.fromisoformat(timestamp_str)
    except Exception as e:
        print(f"⚠️ Ошибка чтения времени последней проверки: {e}")
    
    # По умолчанию - последний час
    return datetime.now() - timedelta(hours=1)


def save_last_processed_time(timestamp: datetime):
    """Сохранить время последней проверки SMS"""
    try:
        os.makedirs(os.path.dirname(LAST_PROCESSED_FILE), exist_ok=True)
        with open(LAST_PROCESSED_FILE, 'w') as f:
            f.write(timestamp.isoformat())
    except Exception as e:
        print(f"⚠️ Ошибка сохранения времени проверки: {e}")


def parse_sms_reply(body: str) -> Optional[tuple]:
    """Парсинг SMS ответа в формате "Номер. Текст ответа"
    
    Returns:
        (task_number, reply_text) или None если формат неверный
    
    Примеры:
        "1. Готово" -> ("1", "Готово")
        "2. Еще не сделал" -> ("2", "Еще не сделал")
    """
    try:
        body = body.strip()
        
        # Ищем паттерн: число + точка + текст
        import re
        match = re.match(r'^(\d+)\.\s*(.+)$', body)
        
        if not match:
            return None
        
        task_number = match.group(1)
        reply_text = match.group(2).strip()
        
        return task_number, reply_text
    except Exception:
        return None


def process_sms_message(system: ReminderSystem, sms: Dict) -> bool:
    """Обработка одного SMS сообщения
    
    Returns:
        True если SMS успешно обработано, False иначе
    """
    try:
        from_number = sms.get('from', '')
        body = sms.get('body', '')
        sms_sid = sms.get('sid', 'unknown')
        date_sent = sms.get('date_sent', '')
        
        system._log(f"📱 Входящее SMS от {from_number}: {body}")
        
        # Парсим ответ
        parsed = parse_sms_reply(body)
        if not parsed:
            system._log(f"⚠️ Неверный формат SMS: {body}", "WARNING")
            return False
        
        task_number, reply_text = parsed
        
        # Получаем задачу по номеру
        task_info = system._get_task_by_sms_code(task_number)
        if not task_info:
            system._log(f"⚠️ Номер {task_number} не найден или устарел", "WARNING")
            return False
        
        task_id = task_info['task_id']
        task_name = task_info['task_name']
        
        system._log(f"✅ SMS ответ: номер={task_number}, задача={task_id}, текст={reply_text}")
        
        # Используем GPT для анализа ответа (как для голосовых ответов)
        status = system.analyze_response_with_ai(reply_text, task_name)
        system._log(f"🤖 GPT анализ: {status}")
        
        # Получаем полную информацию о задаче из ClickUp
        import requests
        try:
            headers = {
                "Authorization": system.clickup_token,
                "Content-Type": "application/json"
            }
            url = f"https://api.clickup.com/api/v2/task/{task_id}"
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            task_data = response.json()
        except Exception as e:
            system._log(f"⚠️ Ошибка получения задачи: {e}", "WARNING")
            task_data = {"id": task_id, "name": task_name}
        
        # Обновляем задачу в ClickUp
        system.update_task_in_clickup(task_id, status, task_data)
        
        # Добавляем комментарий о SMS ответе
        system._post_call_result_comment(task_id, f"SMS-{sms_sid[:8]}", {
            "status": "sms_reply",
            "sms_code": task_number,
            "ai_status": status,
            "transcription": f"Ответ по SMS от {from_number}: {reply_text}",
            "sms_sent": False
        })
        
        system._log(f"✅ Задача {task_id} обновлена через SMS")
        
        # Отправляем подтверждение
        status_text = {
            "ВЫПОЛНЕНО": "отмечена как выполненная ✅",
            "НЕ_ВЫПОЛНЕНО": "отмечена как не выполненная ❌",
            "В_РАБОТЕ": "отмечена как в работе 🔄",
            "ПЕРЕЗВОНИТЬ": "отмечена как 'перезвонить' 📞",
            "НЕЯСНО": "обработана, статус неясен ⚠️"
        }
        
        confirmation_message = f"Задача '{task_name}' {status_text.get(status, 'обработана')}"
        
        try:
            system.twilio_client.messages.create(
                to=from_number,
                from_=system.twilio_phone,
                body=confirmation_message
            )
            system._log(f"📤 Отправлено подтверждение на {from_number}")
        except Exception as e:
            system._log(f"⚠️ Ошибка отправки подтверждения: {e}", "WARNING")
        
        return True
        
    except Exception as e:
        system._log(f"❌ Ошибка обработки SMS: {e}", "ERROR")
        import traceback
        system._log(traceback.format_exc(), "ERROR")
        return False


def main():
    """Основная функция - проверка и обработка входящих SMS"""
    try:
        print("🚀 Запуск обработки входящих SMS...")
        
        # Инициализируем систему
        system = ReminderSystem()
        
        # Получаем время последней проверки
        last_check = get_last_processed_time()
        system._log(f"📅 Последняя проверка: {last_check}")
        
        # Получаем входящие SMS через Twilio API
        system._log("📥 Получение входящих SMS из Twilio...")
        
        try:
            # Получаем SMS, отправленные на наш номер после последней проверки
            messages = system.twilio_client.messages.list(
                to=system.twilio_phone,
                date_sent_after=last_check,
                limit=100
            )
            
            system._log(f"📊 Найдено {len(messages)} новых SMS")
            
            if not messages:
                system._log("✅ Нет новых SMS для обработки")
                save_last_processed_time(datetime.now())
                return
            
            # Обрабатываем каждое SMS
            processed_count = 0
            for msg in messages:
                msg_dict = {
                    'sid': msg.sid,
                    'from': msg.from_,
                    'to': msg.to,
                    'body': msg.body,
                    'date_sent': msg.date_sent
                }
                
                if process_sms_message(system, msg_dict):
                    processed_count += 1
            
            system._log(f"✅ Успешно обработано SMS: {processed_count}/{len(messages)}")
            
            # Сохраняем время проверки
            save_last_processed_time(datetime.now())
            
        except Exception as e:
            system._log(f"❌ Ошибка получения SMS из Twilio: {e}", "ERROR")
            import traceback
            system._log(traceback.format_exc(), "ERROR")
            
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
