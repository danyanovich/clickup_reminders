from .models import ReminderTask

LOGGER = logging.getLogger(__name__)
import pytz

def format_task_message(task: ReminderTask, ordinal: int) -> str:
    """Format a Telegram message for a single reminder task."""
    return (
        f"🔔 <b>Напоминание #{ordinal}</b>\n\n"
        f"📋 <b>Задача:</b> {task.name}\n"
        f"👤 <b>Исполнитель:</b> {task.assignee}\n"
        f"📊 <b>Статус:</b> {task.status}\n"
        f"⏰ <b>Срок:</b> {task.due_human}\n"
        f"🔗 <a href=\"{task.url}\">Открыть задачу</a>"
    )

def build_task_keyboard(
    task_id: str, 
    status_actions: List[Dict[str, Any]], 
    buttons_per_row: int = 3,
    shortcuts: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """Build an inline keyboard for task actions."""
    keyboard_buttons = [
        {
            "text": action["text"],
            "callback_data": f"s:{task_id}:{action['code']}",
        }
        for action in status_actions
    ]

    inline_keyboard: List[List[Dict[str, Any]]] = []
    for idx in range(0, len(keyboard_buttons), buttons_per_row):
        inline_keyboard.append(keyboard_buttons[idx : idx + buttons_per_row])

    if shortcuts:
        shortcut_buttons = [{"text": s["text"], "url": s["url"]} for s in shortcuts]
        inline_keyboard.append(shortcut_buttons)

    return {"inline_keyboard": inline_keyboard}

def format_group_summary(stats: Any) -> str:
    """Compose a high-level delivery report."""
    timestamp_local = stats.timestamp
    try:
        tz = pytz.timezone(stats.timezone)
    except Exception:  # pragma: no cover - fallback
        tz = pytz.UTC

    if timestamp_local.tzinfo is None:
        timestamp_local = tz.localize(timestamp_local)
    else:
        try:
            timestamp_local = timestamp_local.astimezone(tz)
        except Exception:  # pragma: no cover - fallback
            pass

    time_label = timestamp_local.strftime("%d.%m %H:%M")
    tz_label = timestamp_local.strftime("%Z") or stats.timezone

    lines = [f"📊 Отчёт бота ({time_label} {tz_label}):"]
    lines.append(f"• Отправлено задач: {stats.delivered_tasks}/{stats.total_tasks}")
    lines.append(f"• Чатов с уведомлениями: {len(stats.per_chat_counts)}")

    if stats.missing_tasks:
        lines.append(f"• Без уведомлений (нет чатов/фильтров): {stats.missing_tasks}")
    if stats.callbacks_processed:
        lines.append(f"• Ответов пользователей обработано: {stats.callbacks_processed}")
    if stats.voice_calls or stats.voice_failures:
        voice_line = f"• Запущено звонков: {stats.voice_calls}"
        if stats.voice_failures:
            voice_line += f" (ошибок: {stats.voice_failures})"
        lines.append(voice_line)
    if stats.sms_sent:
        lines.append(f"• SMS уведомлений: {stats.sms_sent}")

    if stats.user_actions:
        lines.append("• Ответы пользователей:")
        for entry in stats.user_actions:
            lines.append(f"  ◦ {entry}")

    if stats.failed_actions:
        lines.append("• Неудачные действия:")
        for entry in stats.failed_actions:
            lines.append(f"  ◦ {entry}")

    if stats.per_chat_counts:
        lines.append("Чаты:")
        for chat_id, count in sorted(stats.per_chat_counts.items(), key=lambda item: item[0]):
            assignees = stats.per_chat_assignees.get(chat_id) or []
            lines.append(f"  ◦ {chat_id}: {count} задач ({', '.join(assignees)})")

    return "\n".join(lines)
