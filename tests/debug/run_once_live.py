from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rednote2tg.config import load_config
from rednote2tg.db import NoteStore
from rednote2tg.logging import configure_logging
from rednote2tg.media import MediaDownloader
from rednote2tg.scheduler import PublishJobRunner, handle_run_once
from rednote2tg.telegram_publisher import TelegramPublisher
from rednote2tg.xhs_source import XhsSource


class DebugMessage:
    def __init__(self, user_id: int | None):
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)
        print(text)


async def run_live_once(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    configure_logging(config.logging)
    if args.notes_per_run is not None:
        config = replace(config, publishing=replace(config.publishing, notes_per_run=args.notes_per_run))

    from aiogram import Bot

    bot = Bot(token=config.telegram.bot_token)
    store = NoteStore(config.storage.sqlite_path)
    try:
        runner = PublishJobRunner(
            config,
            XhsSource(config.xhs, config.sources),
            store,
            MediaDownloader(config.storage.media_temp_dir),
            TelegramPublisher(bot, config.telegram.channel_id, config.publishing.caption_parse_mode),
        )
        admin_id = args.user_id
        if admin_id is None and config.telegram.admin_user_ids:
            admin_id = config.telegram.admin_user_ids[0]
        message = DebugMessage(admin_id)

        await handle_run_once(message, runner, config.telegram.admin_user_ids)
        return 0 if message.answers and message.answers[-1].startswith("run_once done:") else 1
    finally:
        store.close()
        await bot.session.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live debug for /run_once. It crawls real XHS notes and uploads them to the configured Telegram channel.",
    )
    parser.add_argument("--config", default=str(ROOT / "config.yaml"), help="Path to config.yaml.")
    parser.add_argument("--user-id", type=int, default=None, help="Telegram admin user id used for authorization.")
    parser.add_argument("--notes-per-run", type=int, default=None, help="Override publishing.notes_per_run for this debug run.")
    parser.add_argument("--yes", action="store_true", help="Required: confirm this will upload to Telegram.")
    args = parser.parse_args()
    if not args.yes:
        parser.error("live upload is blocked without --yes")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_live_once(parse_args())))
