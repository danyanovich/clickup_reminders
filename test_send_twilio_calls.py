import logging
from typing import Any, Dict, List, Optional

import pytest

import send_twilio_calls
from send_twilio_calls import ConfigurationError


class _DummyService:
    def __init__(self, result: Optional[List[Dict[str, Any]]] = None):
        self.result = result or []
        self.captured_kwargs: Dict[str, Any] = {}

    def send_voice_reminders(
        self,
        *,
        assignees: Optional[List[str]] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> List[Dict[str, Any]]:
        self.captured_kwargs = {
            "assignees": assignees,
            "limit": limit,
            "dry_run": dry_run,
        }
        return self.result


class _DummyFactory:
    def __init__(self, service: _DummyService):
        self._service = service

    def from_environment(self):  # pragma: no cover - compatibility shim
        return self._service

    @classmethod
    def create(cls, service: _DummyService):
        class _Factory:
            _service = service

            @classmethod
            def from_environment(cls):
                return cls._service

        return _Factory


def test_main_passes_cli_arguments(monkeypatch):
    dummy_service = _DummyService()
    monkeypatch.setattr(
        send_twilio_calls,
        "TelegramReminderService",
        _DummyFactory.create(dummy_service),
    )

    exit_code = send_twilio_calls.main(
        ["--dry-run", "--assignee", "Alex", "--assignee", "Иван", "--limit", "3"]
    )

    assert exit_code == 0
    assert dummy_service.captured_kwargs["dry_run"] is True
    assert dummy_service.captured_kwargs["assignees"] == ["Alex", "Иван"]
    assert dummy_service.captured_kwargs["limit"] == 3


def test_main_returns_configuration_error(monkeypatch):
    class _BrokenFactory:
        @classmethod
        def from_environment(cls):
            raise ConfigurationError("missing Twilio credentials")

    monkeypatch.setattr(send_twilio_calls, "TelegramReminderService", _BrokenFactory)

    exit_code = send_twilio_calls.main([])

    assert exit_code == 2


def test_main_logs_deliveries(monkeypatch, caplog):
    delivery = {
        "phone": "+10000000000",
        "task_ids": ["task_1", "task_2"],
        "assignees": ["Alex", "Иван"],
    }
    dummy_service = _DummyService(result=[delivery])
    monkeypatch.setattr(
        send_twilio_calls,
        "TelegramReminderService",
        _DummyFactory.create(dummy_service),
    )

    caplog.set_level(logging.INFO)
    exit_code = send_twilio_calls.main([])

    assert exit_code == 0
    assert "Call +10000000000" in caplog.text
