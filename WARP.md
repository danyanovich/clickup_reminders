# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

Project: ClickUp reminders with Telegram first, Twilio/SMS fallback, and optional webhook server.

Quick commands
- Install deps
  - python3 -m pip install -r requirements.txt
- Run one-off Telegram reminders (as in CI)
  - python3 send_telegram_reminders.py --verbose
  - Optional flags: --chat-id <id>, --summary-chat-id <id>, --limit N, --poll-seconds 5 --final-poll-seconds 30
- Run long‑polling Telegram bot (listens to /start and inline buttons)
  - python3 telegram_bot.py --initial-send --verbose
- Trigger Twilio voice calls for pending reminders
  - Dry run: python3 send_twilio_calls.py --dry-run
  - Target a person: python3 send_twilio_calls.py --assignee "Alex" --verbose
- Run webhook server for Twilio callbacks (optional path)
  - python3 webhook_server.py
  - Or: ./start_webhook_server.sh start|stop|restart|status|logs|follow
- Tests (pytest)
  - All: pytest -q
  - Specific file: pytest -q test_telegram_reminder_service.py
  - Single test: pytest -q test_telegram_reminder_service.py::TelegramReminderServiceTest::test_handle_callback_updates_status_and_notifies
  - Pattern: pytest -q -k "status_mapping"
- CI parity (roughly mirrors GitHub Actions)
  - python3 -m pip install -r requirements.txt
  - pytest --maxfail=1 --disable-warnings -q test_telegram_reminder_service.py test_status_mapping.py
  - python3 send_telegram_reminders.py --verbose

Configuration and secrets
- Primary config file: config.json (copy from config.example.json). Override path via CONFIG_PATH.
- Secrets are resolved from environment first, then from a JSON file (SECRETS_PATH). Expected env keys include:
  - CLICKUP_API_KEY, CLICKUP_TEAM_ID or CLICKUP_TEAM_IDS (comma‑separated), optional CLICKUP_SPACE_IDS
  - TELEGRAM_BOT_TOKEN, optional TELEGRAM_CHAT_ID, TELEGRAM_GROUP_CHAT_ID
  - TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, optional TWILIO_TO_ALEX
  - OPENAI_API_KEY (only needed for transcription/analysis paths)
- Telegram routing and per‑assignee channels can be configured in config.json under telegram.

High‑level architecture
- telegram_reminder_service.py
  - Core orchestration service (TelegramReminderService). Loads config (CONFIG_PATH) and credentials (env/SECRETS_PATH), builds status mappings and channel preferences, and resolves ClickUp workspace/space IDs.
  - Fetches tasks from ClickUp (via ClickUpClient), sends reminders to Telegram with inline action buttons, polls and handles callbacks to update statuses in ClickUp, and logs a compact delivery report. Supports per‑assignee routing to specific chats and channel preferences (telegram/voice/sms) with sane defaults.
  - Integrates optional Twilio voice/SMS via TwilioService for fallback when there is no Telegram interaction; supports --assignee filters and dry‑runs.
  - Artifacts and state: caches default chat id (var/telegram_chat_id.txt), appends callback audit log (var/telegram_callback_log.jsonl).
- send_telegram_reminders.py
  - CLI wrapper around TelegramReminderService to perform a single “send reminders” pass, poll for callbacks before/after, and optionally trigger Twilio fallback (used by CI). Accepts overrides for target chats and polling windows.
- telegram_bot.py
  - Long‑polling bot that continuously processes updates: /start to list current reminders; inline button callbacks to update statuses. Useful for always‑on operation.
- clickup.py
  - Minimal ClickUp API client (fetch tasks by list or tag, update task/status, comments) used by TelegramReminderService. Handles pagination and tag variants (#tag and tag).
- telephony.py
  - TwilioService wrapper to place grouped calls (builds TwiML and records) and send SMS. Returns simple CallResult/SMSResult models consumed by TelegramReminderService.
- webhook_server.py (optional path)
  - Flask webhook endpoints for Twilio: generate TwiML, accept recording/transcription callbacks, and persist artifacts under BASE_DIR (transcriptions/, call_data/, logs/). Can be run locally or managed via start_webhook_server.sh.
- reminder_system.py and helper scripts (legacy/auxiliary path)
  - An alternative monolithic flow that places calls and performs Whisper/GPT analysis, persisting under var/. Aux scripts include processing historical recordings and SMS replies.

GitHub Actions (summary)
- Workflow: .github/workflows/processrec.yml
  - Prepares config.runtime.json from secret CONFIG_JSON or config.json, installs dependencies, runs a subset of pytest (telegram service + status mapping), verifies presence of required secrets, then calls send_telegram_reminders.py --verbose.
  - Python 3.11 on ubuntu‑latest; caches pip.

Development notes specific to this repo
- There is no dedicated linter/formatter configured in the repo; pytest is included. If you add linting, prefer lightweight, zero‑config defaults and update CI accordingly.
- Many scripts read and write under var/ (logs, transcriptions, callback logs). Ensure the working directory is the repo root or set BASE_DIR accordingly.
- For Telegram: once you interact with the bot (/start), your chat id is cached in var/telegram_chat_id.txt so one‑off runs can target you without TELEGRAM_CHAT_ID.
