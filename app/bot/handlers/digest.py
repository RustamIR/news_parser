"""Выдача дайджеста: по кнопке в рубрике и командой /digest."""
from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards import CatCB, back_to_category, feedback_kb
from app.bot.utils import safe_edit
from app.db import repo

router = Router(name="digest")

DIGEST_LIMIT = 10
DIGEST_HOURS = 24


@router.callback_query(CatCB.filter(F.action == "digest"))
async def cb_digest(call: CallbackQuery, callback_data: CatCB) -> None:
    cat_id = callback_data.cat_id
    category = await repo.get_category(cat_id)
    items = await repo.digest(cat_id, limit=DIGEST_LIMIT, since_hours=DIGEST_HOURS)

    header = f"<b>{texts.category_title(category)} — дайджест за сутки</b>"
    if not items:
        await call.answer()
        await safe_edit(
            call,
            f"{header}\n\nПодходящих новостей пока нет.\n"
            "Попробуйте «🔄 Проверить сейчас» или ослабьте порог в настройках.",
            back_to_category(cat_id),
        )
        return

    await call.answer()
    await safe_edit(call, f"{header}\n\nНиже {len(items)} материалов:",
                    back_to_category(cat_id))
    await _send_items(call.message, items, category)


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    any_found = False
    for category in await repo.list_categories(only_enabled=True):
        items = await repo.digest(category["id"], limit=DIGEST_LIMIT,
                                  since_hours=DIGEST_HOURS)
        if not items:
            continue
        any_found = True
        await message.answer(
            f"<b>{texts.category_title(category)} — дайджест за сутки</b>"
        )
        await _send_items(message, items, category)

    if not any_found:
        await message.answer(
            "За последние сутки ничего подходящего не нашлось.\n"
            "Проверьте источники и темы в /start."
        )


async def _send_items(message: Message, items: list[dict], category: dict) -> None:
    for item in items:
        item["category"] = texts.category_title(category)
        await message.answer(texts.format_item(item),
                             reply_markup=feedback_kb(item["id"]),
                             disable_web_page_preview=True)
        await repo.set_item_status(item["id"], "sent")
        await asyncio.sleep(0.05)
