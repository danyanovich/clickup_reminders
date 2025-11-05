#!/usr/bin/env python3
"""
Telegram Notification Module
Модуль для отправки уведомлений в Telegram
"""

import os
import logging
from typing import Optional, Dict, Any
import requests


class TelegramNotifier:
    """Класс для отправки уведомлений в Telegram"""
    
    def __init__(self, bot_token: str, chat_id: Optional[str] = None):
        """
        Инициализация Telegram нотификатора
        
        Args:
            bot_token: Токен Telegram бота
            chat_id: ID чата/группы для отправки сообщений (опционально)
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.logger = logging.getLogger(__name__)
        
    def send_message(
        self, 
        text: str, 
        chat_id: Optional[str] = None,
        parse_mode: str = "HTML",
        disable_notification: bool = False
    ) -> Dict[str, Any]:
        """
        Отправка текстового сообщения в Telegram
        
        Args:
            text: Текст сообщения
            chat_id: ID чата (если не указан, используется из конфига)
            parse_mode: Режим парсинга (HTML, Markdown, MarkdownV2)
            disable_notification: Отключить звуковое уведомление
            
        Returns:
            Ответ от Telegram API
        """
        target_chat_id = chat_id or self.chat_id
        if not target_chat_id:
            raise ValueError("Chat ID не указан ни в конструкторе, ни в параметрах")
        
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            if result.get("ok"):
                self.logger.info(f"Сообщение отправлено в чат {target_chat_id}")
                return result
            else:
                self.logger.error(f"Ошибка отправки: {result.get('description')}")
                return result
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Ошибка при отправке сообщения: {e}")
            return {"ok": False, "error": str(e)}
    
    def send_task_reminder(
        self,
        task_name: str,
        assignee: str,
        due_date: str,
        task_url: Optional[str] = None,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Отправка напоминания о задаче
        
        Args:
            task_name: Название задачи
            assignee: Исполнитель
            due_date: Срок выполнения
            task_url: Ссылка на задачу
            chat_id: ID чата
            
        Returns:
            Ответ от Telegram API
        """
        message = f"🔔 <b>Напоминание о задаче</b>\n\n"
        message += f"📋 <b>Задача:</b> {task_name}\n"
        message += f"👤 <b>Исполнитель:</b> {assignee}\n"
        message += f"⏰ <b>Срок:</b> {due_date}\n"
        
        if task_url:
            message += f"\n🔗 <a href='{task_url}'>Открыть задачу</a>"
        
        return self.send_message(message, chat_id=chat_id)
    
    def send_task_status_update(
        self,
        task_name: str,
        old_status: str,
        new_status: str,
        assignee: str,
        transcript: Optional[str] = None,
        task_url: Optional[str] = None,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Отправка уведомления об обновлении статуса задачи
        
        Args:
            task_name: Название задачи
            old_status: Старый статус
            new_status: Новый статус
            assignee: Исполнитель
            transcript: Транскрипт ответа (опционально)
            task_url: Ссылка на задачу
            chat_id: ID чата
            
        Returns:
            Ответ от Telegram API
        """
        # Эмодзи для статусов
        status_emoji = {
            "completed": "✅",
            "in progress": "🔄",
            "pending": "⏳",
            "blocked": "🚫"
        }
        
        emoji = status_emoji.get(new_status.lower(), "📝")
        
        message = f"{emoji} <b>Обновление статуса задачи</b>\n\n"
        message += f"📋 <b>Задача:</b> {task_name}\n"
        message += f"👤 <b>Исполнитель:</b> {assignee}\n"
        message += f"📊 <b>Статус:</b> {old_status} → {new_status}\n"
        
        if transcript:
            # Ограничиваем длину транскрипта
            short_transcript = transcript[:200] + "..." if len(transcript) > 200 else transcript
            message += f"\n💬 <b>Ответ:</b> {short_transcript}\n"
        
        if task_url:
            message += f"\n🔗 <a href='{task_url}'>Открыть задачу</a>"
        
        return self.send_message(message, chat_id=chat_id)
    
    def send_call_notification(
        self,
        task_name: str,
        assignee: str,
        phone: str,
        call_status: str,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Отправка уведомления о звонке
        
        Args:
            task_name: Название задачи
            assignee: Исполнитель
            phone: Номер телефона
            call_status: Статус звонка (initiated, completed, failed, etc.)
            chat_id: ID чата
            
        Returns:
            Ответ от Telegram API
        """
        status_emoji = {
            "initiated": "📞",
            "ringing": "📱",
            "in-progress": "☎️",
            "completed": "✅",
            "busy": "📵",
            "no-answer": "❌",
            "failed": "⚠️"
        }
        
        emoji = status_emoji.get(call_status.lower(), "📞")
        
        message = f"{emoji} <b>Уведомление о звонке</b>\n\n"
        message += f"📋 <b>Задача:</b> {task_name}\n"
        message += f"👤 <b>Исполнитель:</b> {assignee}\n"
        message += f"📱 <b>Телефон:</b> {phone}\n"
        message += f"📊 <b>Статус:</b> {call_status}\n"
        
        return self.send_message(message, chat_id=chat_id)
    
    def send_sms_notification(
        self,
        task_name: str,
        assignee: str,
        phone: str,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Отправка уведомления об отправке SMS
        
        Args:
            task_name: Название задачи
            assignee: Исполнитель
            phone: Номер телефона
            chat_id: ID чата
            
        Returns:
            Ответ от Telegram API
        """
        message = f"📨 <b>Отправлено SMS-напоминание</b>\n\n"
        message += f"📋 <b>Задача:</b> {task_name}\n"
        message += f"👤 <b>Исполнитель:</b> {assignee}\n"
        message += f"📱 <b>Телефон:</b> {phone}\n"
        
        return self.send_message(message, chat_id=chat_id)
    
    def send_error_notification(
        self,
        error_message: str,
        context: Optional[str] = None,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Отправка уведомления об ошибке
        
        Args:
            error_message: Сообщение об ошибке
            context: Контекст ошибки (опционально)
            chat_id: ID чата
            
        Returns:
            Ответ от Telegram API
        """
        message = f"⚠️ <b>Ошибка в системе напоминаний</b>\n\n"
        message += f"❌ <b>Ошибка:</b> {error_message}\n"
        
        if context:
            message += f"\n📝 <b>Контекст:</b> {context}\n"
        
        return self.send_message(message, chat_id=chat_id)
    
    def test_connection(self) -> bool:
        """
        Проверка подключения к Telegram API
        
        Returns:
            True если подключение успешно, False иначе
        """
        try:
            url = f"{self.base_url}/getMe"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            if result.get("ok"):
                bot_info = result.get("result", {})
                self.logger.info(f"Подключение к боту успешно: @{bot_info.get('username')}")
                return True
            else:
                self.logger.error(f"Ошибка подключения: {result.get('description')}")
                return False
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Ошибка при проверке подключения: {e}")
            return False


def create_telegram_notifier(config: Dict[str, Any], secrets: Dict[str, Any]) -> Optional[TelegramNotifier]:
    """
    Создание экземпляра TelegramNotifier из конфигурации
    
    Args:
        config: Конфигурация приложения
        secrets: Секреты приложения
        
    Returns:
        Экземпляр TelegramNotifier или None если Telegram не настроен
    """
    telegram_config = config.get("telegram", {})
    telegram_secrets = secrets.get("telegram", {})
    
    # Проверяем, включен ли Telegram
    if not telegram_config.get("enabled", False):
        return None
    
    # Получаем токен бота
    bot_token = telegram_secrets.get("bot_token")
    if not bot_token:
        logging.warning("Telegram bot token не найден в secrets")
        return None
    
    # Получаем chat_id
    chat_id = telegram_config.get("chat_id") or telegram_secrets.get("chat_id")
    
    try:
        notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)
        
        # Проверяем подключение
        if notifier.test_connection():
            logging.info("Telegram notifier успешно инициализирован")
            return notifier
        else:
            logging.warning("Не удалось подключиться к Telegram API")
            return None
            
    except Exception as e:
        logging.error(f"Ошибка при создании Telegram notifier: {e}")
        return None
