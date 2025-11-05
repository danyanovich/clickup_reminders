"""
Modular reminder system package.

The package exposes `ReminderSystem` as the main orchestration class used for
managing ClickUp reminders and Twilio call flows.
"""

try:  # pragma: no cover - support both package and script execution
    from .reminder_system import ReminderSystem
except ImportError:  # pragma: no cover - script mode fallback
    from reminder_system import ReminderSystem  # type: ignore

__all__ = ["ReminderSystem"]
