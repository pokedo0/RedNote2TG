from __future__ import annotations

import asyncio

from rednote2tg.config import load_config
from rednote2tg.db import NoteStore
from rednote2tg.logging import configure_logging
from rednote2tg.media import MediaDownloader
from rednote2tg.scheduler import PublishJobRunner, RuntimeState, create_scheduler, register_handlers
from rednote2tg.telegram_publisher import TelegramPublisher
from rednote2tg.xhs_source import XhsSource


async def async_main(config_path: str = "config/config.yaml") -> None:
    config = load_config(config_path)
    configure_logging(config.logging)

    from aiogram import Bot, Dispatcher

    bot = Bot(token=config.telegram.bot_token)
    dispatcher = Dispatcher()
    store = NoteStore(config.storage.sqlite_path)
    source = XhsSource(config.xhs, config.sources)
    downloader = MediaDownloader(config.storage.media_temp_dir)
    publisher = TelegramPublisher(
        bot,
        config.telegram.channel_id,
        config.publishing.caption_parse_mode,
        retry_after_padding_seconds=config.publishing.telegram_retry_after_padding_seconds,
    )
    runner = PublishJobRunner(config, source, store, downloader, publisher)
    state = RuntimeState(config, runner, store, None, config_path)
    scheduler = create_scheduler(config, state)
    state.scheduler = scheduler
    register_handlers(dispatcher, state)

    scheduler.start()
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="run_once", description="立即运行一次采集和发布任务"),
        BotCommand(command="status", description="查看当前系统运行状态"),
        BotCommand(command="start_tasks", description="开始定时爬取任务"),
        BotCommand(command="stop_tasks", description="停止定时爬取任务"),
        BotCommand(command="note", description="私聊抓取一个小红书笔记链接"),
        BotCommand(command="ping", description="检测小红书Cookie是否有效"),
        BotCommand(command="update_cookie", description="更新小红书Cookie"),
        BotCommand(command="reload", description="热加载采集配置"),
    ])
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        store.close()


def run() -> None:
    asyncio.run(async_main())
