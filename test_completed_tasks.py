#!/usr/bin/env python3
"""
Тест для проверки функциональности отслеживания выполненных задач
"""

import os
import sys
import json

# Добавляем путь к модулям
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reminder_system import ReminderSystem

def test_completed_tasks():
    """Тестирование функциональности выполненных задач"""
    
    print("=" * 60)
    print("Тест функциональности выполненных задач")
    print("=" * 60)
    
    try:
        # Инициализируем систему
        system = ReminderSystem()
        
        # Тестовые данные
        test_task_id = "test_task_123"
        test_task_name = "Тестовая задача"
        
        print(f"\n1. Проверяем, выполнена ли задача {test_task_id}...")
        is_completed = system._is_task_completed(test_task_id)
        print(f"   Результат: {'Да' if is_completed else 'Нет'}")
        
        if not is_completed:
            print(f"\n2. Отмечаем задачу {test_task_id} как выполненную...")
            system._mark_task_completed(test_task_id, test_task_name)
            print("   ✓ Задача отмечена как выполненная")
            
            print(f"\n3. Повторная проверка задачи {test_task_id}...")
            is_completed = system._is_task_completed(test_task_id)
            print(f"   Результат: {'Да' if is_completed else 'Нет'}")
            
            if is_completed:
                print("\n✅ Тест пройден успешно!")
            else:
                print("\n❌ Ошибка: задача не была сохранена как выполненная")
        else:
            print(f"\n   Задача уже была выполнена ранее")
            print("\n✅ Тест пройден (задача уже в списке выполненных)")
        
        # Показываем содержимое файла выполненных задач
        print("\n4. Содержимое файла выполненных задач:")
        completed_tasks = system._load_completed_tasks()
        print(json.dumps(completed_tasks, indent=2, ensure_ascii=False))
        
    except Exception as e:
        print(f"\n❌ Ошибка при выполнении теста: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = test_completed_tasks()
    sys.exit(0 if success else 1)
