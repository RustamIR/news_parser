"""Настройки рубрики: интервал опроса, порог релевантности, мгновенная отправка."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot import texts
from app.bot.keyboards import CatCB, SetCB, settings_menu
from app.bot.utils import safe_edit
from app.db import repo
from app.scheduler import ParserScheduler

router = Router(name="settings")


def _view(category: dict) -> str:
    return (
        f"<b>{texts.category_title(category)} — настройки</b>\n\n"
        f"⏱ <b>Интервал опроса</b> — как часто бот ходит за новыми постами.\n"
        f"🎚 <b>Порог релевантности</b> — ниже этой оценки новость не показывается. "
        f"Выше порог — меньше шума, но выше риск пропустить.\n"
        f"📨 <b>Присылать сразу</b> — отправлять подходящее по мере появления, "
        f"а не только по кнопке «Дайджест».\n\n"
        f"Сейчас: каждые {category['poll_interval_min']} мин, "
        f"порог {category['min_relevance']}%, "
        f"мгновенная отправка {'включена' if category['autosend'] else 'выключена'}."
    )


@router.callback_query(CatCB.filter(F.action == "settings"))
async def cb_settings(call: CallbackQuery, callback_data: CatCB) -> None:
    await call.answer()
    category = await repo.get_category(callback_data.cat_id)
    await safe_edit(call, _view(category), settings_menu(category))


@router.callback_query(SetCB.filter())
async def cb_apply_setting(call: CallbackQuery, callback_data: SetCB,
                           scheduler: ParserScheduler) -> None:
    cid = callback_data.cat_id
    category = await repo.get_category(cid)
    if not category:
        await call.answer("Рубрика не найдена", show_alert=True)
        return

    if callback_data.action == "interval":
        await repo.update_category(cid, poll_interval_min=callback_data.value)
        await scheduler.reload()
        note = f"Опрос каждые {callback_data.value} мин"
    elif callback_data.action == "relevance":
        await repo.update_category(cid, min_relevance=callback_data.value)
        note = f"Порог: {callback_data.value}%"
    else:
        new_value = 0 if category["autosend"] else 1
        await repo.update_category(cid, autosend=new_value)
        note = "Мгновенная отправка включена" if new_value else "Только по дайджесту"

    await call.answer(note)
    category = await repo.get_category(cid)
    await safe_edit(call, _view(category), settings_menu(category))
