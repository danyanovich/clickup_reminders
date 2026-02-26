from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class WorkingHours:
    """Working hours boundaries in 24h format."""

    start: int
    end: int


@dataclass
class ReminderConfig:
    """Runtime configuration loaded from json."""

    reminder_list_name: str
    phone_mapping: Dict[str, str]
    working_hours: WorkingHours
    clickup_workspace_id: Optional[str] = None
    clickup_team_ids: List[str] = field(default_factory=list)
    clickup_space_ids: List[str] = field(default_factory=list)
    check_interval_minutes: int = 30
    call_timeout_seconds: int = 30
    max_retries: int = 1
    telegram: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Secrets:
    """Holds API credentials required by the reminder system."""

    clickup_api_key: str
    clickup_team_id: str
    twilio_sid: str
    twilio_token: str
    twilio_phone: str
    openai_api_key: str
    telegram_bot_token: str
    telegram_chat_id: Optional[str] = None
    telegram_group_chat_id: Optional[str] = None


@dataclass
class TaskEnvelope:
    """Container that groups a ClickUp task payload with derived metadata."""

    raw: Dict[str, Any]
    description: str
    recipient: str

    @property
    def task_id(self) -> str:
        return self.raw["id"]

    @property
    def name(self) -> str:
        return self.raw["name"]


@dataclass
class CallResult:
    """Twilio voice call outcome."""

    success: bool
    status: str
    call_sid: Optional[str] = None
    error: Optional[str] = None


@dataclass
class SMSResult:
    """Twilio SMS outcome."""

    success: bool
    message_sid: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AnalysisResult:
    """Outcome of the OpenAI based answer analysis."""

    completed: bool
    confidence: float
    summary: str
    transcript: str = ""


@dataclass
class ProcessedTask:
    """Information that lands in the generated reports."""

    task_id: str
    task_name: str
    recipient: str
    phone: str
    call_success: bool
    analysis: Optional[AnalysisResult] = None
    logged_at: datetime = field(default_factory=datetime.now)


@dataclass
class ReminderTask:
    """Normalized structure representing a ClickUp reminder task."""

    task_id: str
    name: str
    status: str
    due_human: str
    assignee: str
    url: str
    assignee_id: Optional[str] = None
    description: Optional[str] = None


@dataclass
class DeliveryStats:
    timestamp: datetime
    timezone: str
    total_tasks: int
    delivered_tasks: int
    per_chat_counts: Dict[str, int]
    per_chat_assignees: Dict[str, List[str]]
    missing_tasks: int
    broadcast_all: bool
    requested_chat: Optional[str]
    callbacks_processed: int = 0
    voice_calls: int = 0
    voice_failures: int = 0
    sms_sent: int = 0
    user_actions: List[str] = field(default_factory=list)
    failed_actions: List[str] = field(default_factory=list)
