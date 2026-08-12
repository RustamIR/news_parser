"""Точка входа: поднимает бота, парсеры и планировщик в одном процессе."""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.analysis.llm import analyzer
from app.bot import texts
from app.bot.keyboards import feedback_kb
from app.bot.handlers import routers
from app.bot.middlewares import access_middleware
from app.config import config
from app.db.database import db
from app.parsers.tg import tg_collector
from app.parsers.web import web_collector
from app.scheduler import ParserScheduler

log = logging.getLogger("news_parser")

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="form", description="Шаблон формы пополнения"),
    BotCommand(command="add", description="Добавить источники и фильтры формой"),
    BotCommand(command="digest", description="Свежий дайджест"),
    BotCommand(command="run", description="Проверить источники сейчас"),
    BotCommand(command="stats", description="Статистика фильтрации"),
    BotCommand(command="learn", description="Чему бот научился на оценках"),
    BotCommand(command="raters", description="Кто может оценивать новости"),
    BotCommand(command="target", description="Публиковать рубрику в этот чат"),
    BotCommand(command="bind", description="Назначить эту группу управляющей"),
    BotCommand(command="chatid", description="Показать ID чата"),
    BotCommand(command="help", description="Как это работает"),
]


async def resilient(label: str, call, attempts: int = 4):
    """Стартовый вызов к Telegram с ретраями.

    Сеть до api.telegram.org умеет отваливаться на секунду. Ронять из-за этого
    весь процесс незачем: сам поллинг умеет переподключаться, а стартовые
    вызовы должны вести себя так же.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except TelegramNetworkError as e:
            log.warning("%s — сеть недоступна (%d/%d): %s", label, attempt, attempts, e)
            if attempt < attempts:
                await asyncio.sleep(2 * attempt)
    return None


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def main() -> None:
    setup_logging()

    if not config.telegram_token:
        sys.exit("TELEGRAM_TOKEN не задан в .env")

    await db.connect()
    log.info("База готова: %s", config.db_path)

    bot = Bot(
        token=config.telegram_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    async def notify(chat_id: int, payload: dict) -> None:
        """Публикация новости в канал рубрики или в личку подписчику."""
        text = texts.format_item(payload)
        markup = feedback_kb(payload.get("item_id") or 0)
        try:
            await bot.send_message(chat_id, text, reply_markup=markup,
                                   disable_web_page_preview=True)
        except TelegramRetryAfter as e:
            # Уткнулись в лимит канала — ждём ровно столько, сколько просят.
            log.info("Лимит отправки в %s, пауза %s с", chat_id, e.retry_after)
            await asyncio.sleep(e.retry_after)
            await bot.send_message(chat_id, text, reply_markup=markup,
                                   disable_web_page_preview=True)

    scheduler = ParserScheduler(notify=notify)

    dp = Dispatcher(storage=MemoryStorage())
    dp["scheduler"] = scheduler
    dp.message.middleware(access_middleware())
    dp.callback_query.middleware(access_middleware())
    for router in routers:
        dp.include_router(router)

    await tg_collector.start()
    await web_collector.start()
    await analyzer.start()
    await scheduler.start()

    await resilient("Обновление списка команд", lambda: bot.set_my_commands(COMMANDS))
    me = await resilient("Проверка токена", bot.get_me)
    log.info("Бот @%s запущен", me.username if me else "?")
    if not config.admin_ids:
        log.warning("ADMIN_IDS пуст — бот отвечает любому пользователю")

    try:
        await dp.start_polling(bot)
    finally:
        log.info("Останавливаюсь…")
        scheduler.shutdown()
        await tg_collector.stop()
        await web_collector.stop()
        await analyzer.stop()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())
