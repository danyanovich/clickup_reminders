#!/usr/bin/env python3
"""
Тестовый скрипт для проверки маппинга статусов
"""

def test_status_mapping():
    """Проверяет, что все статусы правильно мапятся"""
    
    # Симуляция пустого config
    clickup_config = {}
    
    # Симуляция логики из reminder_system.py
    raw_mapping = clickup_config.get("status_mapping", {})
    status_mapping = {key.upper(): value for key, value in raw_mapping.items()}
    
    # Устанавливаем дефолтные значения
    status_mapping.setdefault("ВЫПОЛНЕНО", clickup_config.get("completed_status", "complete"))
    status_mapping.setdefault("НЕ_ВЫПОЛНЕНО", clickup_config.get("pending_status", "to do"))
    status_mapping.setdefault("В_РАБОТЕ", clickup_config.get("in_progress_status", "in progress"))
    status_mapping.setdefault("НЕЯСНО", clickup_config.get("unclear_status", "to do"))
    status_mapping.setdefault("ПЕРЕЗВОНИТЬ", clickup_config.get("callback_status", "to do"))
    
    print("=" * 60)
    print("ТЕСТ: Маппинг статусов (пустой config)")
    print("=" * 60)
    
    test_statuses = ["ВЫПОЛНЕНО", "НЕ_ВЫПОЛНЕНО", "В_РАБОТЕ", "НЕЯСНО", "ПЕРЕЗВОНИТЬ"]
    
    all_ok = True
    for status in test_statuses:
        clickup_status = status_mapping.get(status)
        if clickup_status:
            print(f"✅ {status:20} -> {clickup_status}")
        else:
            print(f"❌ {status:20} -> НЕТ МАППИНГА!")
            all_ok = False
    
    print("=" * 60)
    if all_ok:
        print("✅ ВСЕ СТАТУСЫ ИМЕЮТ МАППИНГ")
    else:
        print("❌ НЕКОТОРЫЕ СТАТУСЫ НЕ ЗАМАПИРОВАНЫ!")
    print("=" * 60)
    
    return all_ok


if __name__ == "__main__":
    success = test_status_mapping()
    exit(0 if success else 1)
