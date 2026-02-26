from __future__ import annotations

from typing import Iterable, List

from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

try:  # pragma: no cover - support both package and script execution
    from .core.models import CallResult, SMSResult
except ImportError:  # pragma: no cover - script mode fallback
    from core.models import CallResult, SMSResult  # type: ignore


class TwilioService:
    """High level helper around the Twilio client used in the reminder flow."""

    def __init__(self, account_sid: str, auth_token: str):
        self.client = Client(account_sid, auth_token)

    def make_call(self, from_phone: str, to_phone: str, task_messages: Iterable[str]) -> CallResult:
        """Place a call that includes all pending reminders."""
        messages = list(task_messages)
        twiml = self._build_twiml(messages)

        try:
            call = self.client.calls.create(
                to=to_phone,
                from_=from_phone,
                twiml=twiml,
                record=True,
                recording_status_callback_event=["completed"],
            )
        except Exception as exc:  # pragma: no cover - network calls
            return CallResult(success=False, status="error", error=str(exc))

        return CallResult(success=True, status=call.status, call_sid=call.sid)

    def send_sms(self, from_phone: str, to_phone: str, body: str) -> SMSResult:
        """Send an SMS fallback."""
        try:
            message = self.client.messages.create(to=to_phone, from_=from_phone, body=body)
        except Exception as exc:  # pragma: no cover - network calls
            return SMSResult(success=False, error=str(exc))
        return SMSResult(success=True, message_sid=message.sid)

    @staticmethod
    def _build_twiml(task_messages: List[str]) -> str:
        voice_response = VoiceResponse()
        gather = Gather(
            input="speech",
            timeout=10,
            language="ru-RU",
            speech_timeout="auto",
            action="/voice-response",
        )

        if not task_messages:
            gather.say("Здравствуйте! Напоминаний нет.", language="ru-RU")
        elif len(task_messages) == 1:
            gather.say(
                f"Здравствуйте! Напоминание о задаче: {task_messages[0]}. "
                "Что выполнили?",
                language="ru-RU",
            )
        else:
            tasks = ". ".join(f"{index + 1}. {message}" for index, message in enumerate(task_messages))
            gather.say(
                f"Здравствуйте! У вас {len(task_messages)} напоминания: {tasks}. "
                "Что выполнили?",
                language="ru-RU",
            )

        voice_response.append(gather)
        return str(voice_response)
