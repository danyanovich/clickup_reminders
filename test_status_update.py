#!/usr/bin/env python3
"""
Тестовый скрипт для проверки обновления статуса задачи в ClickUp
"""

import os
import json
import requests
from pathlib import Path

def load_config():
    """Загрузка конфигурации"""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_secrets():
    """Загрузка секретов"""
    secrets_path = Path(__file__).parent / ".venv" / "bin" / "secrets.json"
    with open(secrets_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_task_current_status(api_key, task_id):
    """Получить текущий статус задачи"""
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json"
    }
    
    url = f"https://api.clickup.com/api/v2/task/{task_id}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    task_data = response.json()
    return task_data.get("status", {}).get("status", "unknown")

def update_task_status(api_key, task_id, new_status):
    """Обновить статус задачи"""
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json"
    }
    
    url = f"https://api.clickup.com/api/v2/task/{task_id}"
    data = {"status": new_status}
    
    print(f"\n📤 Отправка запроса:")
    print(f"   URL: {url}")
    print(f"   Данные: {json.dumps(data, ensure_ascii=False)}")
    
    response = requests.put(url, headers=headers, json=data)
    
    print(f"\n📥 Ответ:")
    print(f"   Статус код: {response.status_code}")
    
    if response.status_code == 200:
        print(f"   ✅ Успешно!")
        return True
    else:
        print(f"   ❌ Ошибка: {response.text}")
        return False

def get_tasks_from_list(api_key, list_id):
    """Получить задачи из списка"""
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json"
    }
    
    url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    return response.json().get("tasks", [])

def main():
    print("="*60)
    print("🧪 ТЕСТ ОБНОВЛЕНИЯ СТАТУСА ЗАДАЧИ")
    print("="*60)
    
    # Загружаем конфигурацию
    config = load_config()
    secrets = load_secrets()
    
    api_key = os.getenv("CLICKUP_API_KEY") or secrets["clickup"]["api_key"]
    list_id = config["clickup_list_id"]
    status_mapping = config["clickup"]["status_mapping"]
    
    print(f"\n📋 List ID: {list_id}")
    print(f"\n🗺️  Маппинг статусов:")
    for ai_status, clickup_status in status_mapping.items():
        print(f"   {ai_status} → '{clickup_status}'")
    
    # Получаем задачи из списка
    print(f"\n🔍 Получаем задачи из списка...")
    tasks = get_tasks_from_list(api_key, list_id)
    
    if not tasks:
        print("   ⚠️ Нет задач в списке")
        return
    
    print(f"   Найдено задач: {len(tasks)}")
    print("\n   Доступные задачи:")
    for i, task in enumerate(tasks[:5], 1):  # Показываем первые 5
        task_id = task.get("id")
        task_name = task.get("name", "Без названия")
        current_status = task.get("status", {}).get("status", "unknown")
        print(f"   {i}. [{task_id}] {task_name}")
        print(f"      Текущий статус: '{current_status}'")
    
    # Выбираем первую задачу для теста
    test_task = tasks[0]
    task_id = test_task.get("id")
    task_name = test_task.get("name", "Без названия")
    current_status = test_task.get("status", {}).get("status", "unknown")
    
    print("\n" + "="*60)
    print(f"🎯 ТЕСТОВАЯ ЗАДАЧА:")
    print(f"   ID: {task_id}")
    print(f"   Название: {task_name}")
    print(f"   Текущий статус: '{current_status}'")
    
    # Выбираем новый статус (не тот же самый)
    test_status = status_mapping["ВЫПОЛНЕНО"]  # "выполнена"
    if current_status == test_status:
        # Если уже "выполнена", меняем на "поставлена"
        test_status = status_mapping["ПОСТАВЛЕНА"] if "ПОСТАВЛЕНА" in status_mapping else status_mapping["НЕЯСНО"]
    
    print(f"\n🔄 Пытаемся изменить статус на: '{test_status}'")
    
    input("\n⚠️  Нажмите Enter чтобы ИЗМЕНИТЬ статус задачи (или Ctrl+C для отмены)...")
    
    # Обновляем статус
    success = update_task_status(api_key, task_id, test_status)
    
    if success:
        print("\n⏳ Проверяем изменения...")
        import time
        time.sleep(2)
        
        new_status = get_task_current_status(api_key, task_id)
        print(f"\n📊 РЕЗУЛЬТАТ:")
        print(f"   Было: '{current_status}'")
        print(f"   Стало: '{new_status}'")
        
        if new_status == test_status:
            print(f"\n   ✅ УСПЕХ! Статус изменен на '{test_status}'")
            print(f"\n   🔗 Проверьте в ClickUp: задача '{task_name}'")
        else:
            print(f"\n   ⚠️ Статус не совпал. Ожидали '{test_status}', получили '{new_status}'")
    
    print("\n" + "="*60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Отменено пользователем")
    except Exception as e:
        print(f"\n\n❌ Ошибка: {str(e)}")
        import traceback
        traceback.print_exc()
