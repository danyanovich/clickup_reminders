from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import pytz

from .models import ReminderTask, ReminderConfig, Secrets
from ..clickup import ClickUpClient

LOGGER = logging.getLogger(__name__)

class ClickUpEngine:
    """Consolidated business logic for ClickUp operations."""

    def __init__(self, config: ReminderConfig, secrets: Secrets):
        self.config = config
        self.secrets = secrets
        self.client = ClickUpClient(
            api_key=secrets.clickup_api_key,
            team_id=secrets.clickup_team_id
        )
        self.tz = pytz.timezone(getattr(config.working_hours, "timezone", "Europe/Moscow"))

    def fetch_pending_reminders(self) -> List[ReminderTask]:
        """Fetch all tasks that need a reminder based on config."""
        # 1. Resolve List ID if needed
        list_id = getattr(self.config, "reminders_list_id", None)
        if not list_id:
            list_id = self.client.find_list_id(self.config.reminder_list_name, self.config.clickup_space_ids)
        
        if not list_id:
            LOGGER.warning(f"Could not find ClickUp list: {self.config.reminder_list_name}")
            return []

        # 2. Fetch tasks
        raw_tasks = self.client.fetch_tasks(list_id=list_id)
        
        # 3. Filter and Normalize
        pending: List[ReminderTask] = []
        now = datetime.now(self.tz)
        
        for task in raw_tasks:
            # Basic status filter (not closed)
            status_obj = task.get("status") or {}
            if status_obj.get("type") == "closed":
                continue
                
            # Due date filter
            due_raw = task.get("due_date")
            if not due_raw:
                continue
                
            due_dt = datetime.fromtimestamp(int(due_raw) / 1000, tz=self.tz)
            if due_dt > now:
                continue
                
            # Normalize
            pending.append(self._normalize_task(task))
            
        return pending

    def _normalize_task(self, task: Dict[str, Any]) -> ReminderTask:
        status_obj = task.get("status") or {}
        assignees = task.get("assignees", [])
        assignee_name = "—"
        assignee_id = None
        if assignees:
            assignee_name = assignees[0].get("username", "—")
            assignee_id = str(assignees[0].get("id", ""))

        due_raw = task.get("due_date")
        due_human = "—"
        if due_raw:
            due_dt = datetime.fromtimestamp(int(due_raw) / 1000, tz=self.tz)
            due_human = due_dt.strftime("%Y-%m-%d %H:%M")

        return ReminderTask(
            task_id=str(task["id"]),
            name=str(task.get("name", "Без названия")),
            status=status_obj.get("status") or status_obj.get("name") or "—",
            due_human=due_human,
            assignee=assignee_name,
            assignee_id=assignee_id,
            url=f"https://app.clickup.com/t/{task['id']}",
            description=str(task.get("description") or task.get("text_content") or "").strip() or None
        )

    def update_task_status(self, task_id: str, status: str) -> bool:
        """Update task status and add an audit comment."""
        try:
            success = self.client.update_task_status(task_id, status)
            if success:
                self.client.add_comment(task_id, f"Статус обновлен через Reminder Engine: {status}")
            return success
        except Exception as exc:
            LOGGER.error(f"Failed to update task {task_id} status: {exc}")
            return False
