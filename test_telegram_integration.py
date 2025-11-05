#!/usr/bin/env python3
"""
Тестовый скрипт для проверки Telegram интеграции
"""

import json
import os
import sys

try:
    from telegram_notifier import create_telegram_notifier
except ImportError:
    print("❌ Ошибка: не удалось импортировать telegram_notifier")
    sys.exit(1)


def load_config():
    """Загрузка конфигурации"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    if not os.path.exists(config_path):
        print(f"❌ Файл конфигурации не найден: {config_path}")
        print("💡 Создайте config.json на основе config.example.json")
        sys.exit(1)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_secrets():
    """Загрузка секретов"""
    # Пробуем разные пути
    possible_paths = [
        os.path.join(os.path.dirname(__file__), '.venv', 'bin', 'secrets.json'),
        os.path.join(os.path.dirname(__file__), '..', '.venv', 'bin', 'secrets.json'),
        os.path.join(os.path.dirname(__file__), 'secrets.json'),
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    
    print("❌ Файл secrets.json не найден")
    print("💡 Создайте secrets.json на основе secrets.example.json")
    print(f"   Проверенные пути: {possible_paths}")
    sys.exit(1)


def test_telegram_connection():
    """Тест подключения к Telegram"""
    print("=" * 60)
    print("🧪 Тест Telegram интеграции")
    print("=" * 60)
    print()
    
    # Загрузка конфигурации
    print("📂 Загрузка конфигурации...")
    config = load_config()
    secrets = load_secrets()
    
    # Проверка наличия Telegram конфигурации
    telegram_config = config.get('telegram', {})
    if not telegram_config.get('enabled', False):
        print("⚠️  Telegram уведомления отключены в config.json")
        print("💡 Установите telegram.enabled = true для включения")
        return False
    
    print(f"✅ Telegram включен в конфигурации")
    print(f"   Chat ID: {telegram_config.get('chat_id', 'не указан')}")
    print()
    
    # Создание notifier
    print("🔧 Создание Telegram notifier...")
    telegram = create_telegram_notifier(config, secrets)
    
    if not telegram:
        print("❌ Не удалось создать Telegram notifier")
        print("💡 Проверьте:")
        print("   1. Наличие bot_token в secrets.json")
        print("   2. Правильность токена бота")
        print("   3. Наличие chat_id в config.json или secrets.json")
        return False
    
    print("✅ Telegram notifier успешно создан")
    print()
    
    # Тест подключения
    print("🔌 Проверка подключения к Telegram API...")
    if not telegram.test_connection():
        print("❌ Не удалось подключиться к Telegram API")
        print("💡 Проверьте правильность токена бота")
        return False
    
    print("✅ Подключение к Telegram API успешно")
    print()
    
    # Отправка тестового сообщения
    print("📤 Отправка тестового сообщения...")
    result = telegram.send_message(
        "🎉 <b>Тестовое сообщение</b>\n\n"
        "Это тестовое сообщение от системы напоминаний ClickUp.\n"
        "Если вы видите это сообщение, значит интеграция работает правильно! ✅"
    )
    
    if result.get("ok"):
        print("✅ Тестовое сообщение успешно отправлено!")
        print(f"   Message ID: {result.get('result', {}).get('message_id')}")
    else:
        print(f"❌ Ошибка отправки: {result.get('description')}")
        return False
    
    print()
    
    # Тест различных типов уведомлений
    print("📋 Тест различных типов уведомлений...")
    print()
    
    # 1. Уведомление о звонке
    print("1️⃣  Тест уведомления о звонке...")
    result = telegram.send_call_notification(
        task_name="[Тест] Проверить систему",
        assignee="Тестовый пользователь",
        phone="+1234567890",
        call_status="initiated"
    )
    if result.get("ok"):
        print("   ✅ Уведомление о звонке отправлено")
    else:
        print(f"   ❌ Ошибка: {result.get('description')}")
    
    # 2. Уведомление об обновлении статуса
    print("2️⃣  Тест уведомления об обновлении статуса...")
    result = telegram.send_task_status_update(
        task_name="[Тест] Проверить систему",
        old_status="to do",
        new_status="complete",
        assignee="Тестовый пользователь",
        transcript="Да, выполнил задачу",
        task_url="https://app.clickup.com/t/test123"
    )
    if result.get("ok"):
        print("   ✅ Уведомление об обновлении статуса отправлено")
    else:
        print(f"   ❌ Ошибка: {result.get('description')}")
    
    # 3. Уведомление об ошибке
    print("3️⃣  Тест уведомления об ошибке...")
    result = telegram.send_error_notification(
        error_message="Тестовая ошибка",
        context="Это тестовое уведомление об ошибке"
    )
    if result.get("ok"):
        print("   ✅ Уведомление об ошибке отправлено")
    else:
        print(f"   ❌ Ошибка: {result.get('description')}")
    
    print()
    print("=" * 60)
    print("✅ Все тесты пройдены успешно!")
    print("=" * 60)
    print()
    print("💡 Проверьте ваш Telegram чат/группу - там должны быть 5 сообщений:")
    print("   1. Тестовое сообщение")
    print("   2. Уведомление о звонке")
    print("   3. Уведомление об обновлении статуса")
    print("   4. Уведомление об ошибке")
    print()
    
    return True


if __name__ == "__main__":
    try:
        success = test_telegram_connection()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Тест прерван пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Неожиданная ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
