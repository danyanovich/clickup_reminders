#!/usr/bin/env python3
"""
Скрипт для проверки доступных статусов в ClickUp
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

def get_list_statuses(api_key, list_id):
    """Получить все доступные статусы для списка"""
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json"
    }
    
    url = f"https://api.clickup.com/api/v2/list/{list_id}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    list_data = response.json()
    return list_data.get("statuses", [])

def main():
    print("="*60)
    print("🔍 ПРОВЕРКА СТАТУСОВ В CLICKUP")
    print("="*60)
    
    # Загружаем конфигурацию
    config = load_config()
    secrets = load_secrets()
    
    api_key = os.getenv("CLICKUP_API_KEY") or secrets["clickup"]["api_key"]
    list_id = config["clickup_list_id"]
    
    print(f"\n📋 List ID: {list_id}")
    
    # Получаем статусы
    try:
        statuses = get_list_statuses(api_key, list_id)
        
        print(f"\n✅ Найдено статусов: {len(statuses)}\n")
        print("Доступные статусы:")
        print("-" * 60)
        
        for status in statuses:
            status_name = status.get("status", "")
            status_type = status.get("type", "")
            status_color = status.get("color", "")
            
            print(f"  • Название: '{status_name}'")
            print(f"    Тип: {status_type}")
            print(f"    Цвет: {status_color}")
            print()
        
        print("="*60)
        print("📝 РЕКОМЕНДУЕМЫЙ МАППИНГ ДЛЯ config.json:")
        print("="*60)
        print('"status_mapping": {')
        
        # Ищем подходящие статусы
        status_names = [s.get("status", "") for s in statuses]
        
        for ai_status in ["ВЫПОЛНЕНО", "НЕ_ВЫПОЛНЕНО", "В_РАБОТЕ", "НЕЯСНО", "ПЕРЕЗВОНИТЬ"]:
            # Пытаемся найти наиболее подходящий статус
            if ai_status == "ВЫПОЛНЕНО":
                candidates = [s for s in status_names if "ВЫПОЛН" in s.upper() or "COMPLETE" in s.upper() or "DONE" in s.upper()]
            elif ai_status == "В_РАБОТЕ":
                candidates = [s for s in status_names if "РАБОТ" in s.upper() or "PROGRESS" in s.upper()]
            elif ai_status == "НЕ_ВЫПОЛНЕНО":
                candidates = [s for s in status_names if "ДОРАБОТ" in s.upper() or "TODO" in s.upper() or "DO" in s.upper()]
            else:
                candidates = [s for s in status_names if "ПОСТАВЛ" in s.upper() or "TODO" in s.upper() or "DO" in s.upper()]
            
            if candidates:
                print(f'  "{ai_status}": "{candidates[0]}",')
            else:
                print(f'  "{ai_status}": "{status_names[0] if status_names else "UNKNOWN"}",  # ⚠️ НУЖНО УТОЧНИТЬ')
        
        print('}')
        print()
        
    except Exception as e:
        print(f"\n❌ Ошибка: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
