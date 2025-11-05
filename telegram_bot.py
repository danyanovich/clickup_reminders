#!/usr/bin/env python3
"""
Telegram reminder bot using long polling.

Run this script on a host with internet access. It listens for user commands
(/start, /help) and inline button presses to update ClickUp task statuses.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any, Dict

from telegram_reminder_service import ConfigurationError, TelegramReminderService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Telegram reminder bot (long polling).")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Delay between getUpdates polling cycles when no updates are returned (seconds).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Long polling timeout passed to getUpdates (seconds).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--initial-send",
        action="store_true",
        help="Send the current reminder list to the default chat on startup.",
    )
    return parser.parse_args(argv)


def handle_update(service: TelegramReminderService, update: Dict[str, Any]) -> None:
    if "message" in update:
        service.handle_message(update["message"])
    elif "callback_query" in update:
        service.handle_callback(update["callback_query"])
    else:
        logging.debug("Ignored update: %s", update.keys())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        service = TelegramReminderService.from_environment()
    except ConfigurationError as exc:
        logging.error("Configuration error: %s", exc)
        return 2

    if args.initial_send:
        try:
            service.send_reminders()
        except Exception as exc:  # pragma: no cover - network guard
            logging.warning("Failed to send initial reminders: %s", exc)

    logging.info("Telegram reminder bot started. Poll interval=%ss timeout=%ss", args.poll_interval, args.timeout)

    offset = None
    try:
        while True:
            try:
                updates = service.get_updates(offset=offset, timeout=args.timeout)
            except Exception as exc:  # pragma: no cover - network guard
                logging.error("getUpdates failed: %s", exc)
                time.sleep(max(args.poll_interval, 1))
                continue

            if updates:
                last_update_id = None
                for update in updates:
                    last_update_id = update.get("update_id", last_update_id)
                    handle_update(service, update)
                if last_update_id is not None:
                    offset = last_update_id + 1
            else:
                time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        logging.info("Bot stopped via keyboard interrupt.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
