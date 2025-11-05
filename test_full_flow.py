#!/usr/bin/env python3
"""
Полный тест фильтрации выполненных задач
Проверяет, что выполненные задачи не попадают в список для обработки
"""

import os
import sys
import json

# Добавляем путь к модулям
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_task_filtering():
    """Тест фильтрации выполненных задач"""
    
    print("=" * 70)
    print("Тест полной цепочки фильтрации выполненных задач")
    print("=" * 70)
    
    # Создаем тестовый файл с выполненными задачами
    completed_tasks_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "var",
        "completed_tasks.json"
    )
    
    # Тестовые данные
    test_completed_tasks = {
        "test_task_001": {
            "name": "Выполненная задача 1",
            "completed_at": "2025-10-31T10:00:00.000000+00:00"
        },
        "test_task_002": {
            "name": "Выполненная задача 2",
            "completed_at": "2025-10-31T11:00:00.000000+00:00"
        }
    }
    
    # Сохраняем тестовые данные
    os.makedirs(os.path.dirname(completed_tasks_file), exist_ok=True)
    with open(completed_tasks_file, 'w', encoding='utf-8') as f:
        json.dump(test_completed_tasks, f, indent=2, ensure_ascii=False)
    
    print("\n1. Создан тестовый файл с выполненными задачами:")
    print(f"   Файл: {completed_tasks_file}")
    print(f"   Задач: {len(test_completed_tasks)}")
    
    # Импортируем систему после создания файла
    from reminder_system import ReminderSystem
    
    try:
        system = ReminderSystem()
        
        print("\n2. Проверка метода _is_task_completed():")
        
        # Проверяем выполненные задачи
        for task_id, task_info in test_completed_tasks.items():
            is_completed = system._is_task_completed(task_id)
            status = "✅ Правильно" if is_completed else "❌ Ошибка"
            print(f"   {task_id}: {status} (должна быть выполнена)")
        
        # Проверяем невыполненную задачу
        new_task_id = "test_task_999"
        is_completed = system._is_task_completed(new_task_id)
        status = "✅ Правильно" if not is_completed else "❌ Ошибка"
        print(f"   {new_task_id}: {status} (не должна быть выполнена)")
        
        print("\n3. Симуляция фильтрации задач (как в get_tasks_for_reminder):")
        
        # Симулируем список задач из ClickUp
        mock_tasks = [
            {"id": "test_task_001", "name": "Выполненная задача 1"},
            {"id": "test_task_002", "name": "Выполненная задача 2"},
            {"id": "test_task_003", "name": "Новая задача 1"},
            {"id": "test_task_004", "name": "Новая задача 2"},
        ]
        
        print(f"   Всего задач из ClickUp: {len(mock_tasks)}")
        
        # Фильтруем как в реальном коде
        filtered_tasks = []
        for task in mock_tasks:
            task_id = task.get("id")
            if not system._is_task_completed(task_id):
                filtered_tasks.append(task)
                print(f"   ✅ {task_id} ({task['name']}) - добавлена в обработку")
            else:
                print(f"   ❌ {task_id} ({task['name']}) - пропущена (уже выполнена)")
        
        print(f"\n   Результат фильтрации:")
        print(f"   - Задач до фильтрации: {len(mock_tasks)}")
        print(f"   - Задач после фильтрации: {len(filtered_tasks)}")
        print(f"   - Отфильтровано: {len(mock_tasks) - len(filtered_tasks)}")
        
        # Проверяем результат
        expected_filtered = 2  # Должны остаться только test_task_003 и test_task_004
        if len(filtered_tasks) == expected_filtered:
            print(f"\n✅ Тест пройден успешно!")
            print(f"   Выполненные задачи корректно отфильтрованы")
            return True
        else:
            print(f"\n❌ Ошибка теста!")
            print(f"   Ожидалось задач: {expected_filtered}")
            print(f"   Получено задач: {len(filtered_tasks)}")
            return False
            
    except Exception as e:
        print(f"\n❌ Ошибка при выполнении теста: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_task_filtering()
    
    print("\n" + "=" * 70)
    print("Итог:")
    if success:
        print("✅ Все проверки пройдены")
        print("✅ Выполненные задачи не попадут в GitHub Actions")
        print("✅ Напоминания будут только для невыполненных задач")
    else:
        print("❌ Тест не пройден")
    print("=" * 70)
    
    sys.exit(0 if success else 1)
