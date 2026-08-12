"""Ручное управление источниками рубрики."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.forms import TGWEB_NOTE
from app.bot.keyboards import SrcCB, cancel_kb, sources_menu
from app.bot.states import AddSource
from app.bot.utils import prepare_source, safe_edit, tg_reading_limited
from app.db import repo

router = Router(name="sources")

ADD_PROMPT = (
    "Пришлите источники — по одному в строке, можно сразу несколько:\n\n"
    "✈️ <code>@breakingmash</code> — телеграм-канал через Telethon\n"
    "👀 <code>t.me/s/breakingmash</code> — публичная витрина канала, без api_id\n"
    "📡 <code>https://habr.com/ru/rss/hub/infosecurity/</code> — RSS-лента\n"
    "🌐 <code>https://www.securitylab.ru/news/</code> — обычная страница\n\n"
    "Для обычной страницы бот сам поищет RSS, а если её нет — соберёт ссылки "
    "на статьи со страницы."
)


async def _render(call: CallbackQuery, cat_id: int) -> None:
    category = await repo.get_category(cat_id)
    sources = await repo.list_sources(cat_id)
    if sources:
        body = "\n".join(texts.format_source(s) for s in sources)
    else:
        body = "Пока пусто. Добавьте телеграм-канал или сайт."
    text = (
        f"<b>{texts.category_title(category)} — источники</b>\n\n{body}\n\n"
        "<i>Нажатие на источник включает или выключает его, 🗑 — удаляет.</i>"
    )
    await safe_edit(call, text, sources_menu(cat_id, sources))


@router.callback_query(SrcCB.filter(F.action.in_({"list", "toggle", "delete"})))
async def cb_sources(call: CallbackQuery, callback_data: SrcCB) -> None:
    # Спиннер на кнопке крутится до answerCallbackQuery, поэтому отвечаем
    # сразу после быстрой записи в БД, а медленную правку шлём следом.
    if callback_data.action == "toggle":
        await repo.toggle_source(callback_data.src_id)
        await call.answer()
    elif callback_data.action == "delete":
        await repo.delete_source(callback_data.src_id)
        await call.answer("Источник удалён")
    else:
        await call.answer()
    await _render(call, callback_data.cat_id)


@router.callback_query(SrcCB.filter(F.action == "add"))
async def cb_add_source(call: CallbackQuery, callback_data: SrcCB,
                        state: FSMContext) -> None:
    await state.set_state(AddSource.waiting_input)
    await state.update_data(cat_id=callback_data.cat_id)
    await call.answer()
    await safe_edit(call, ADD_PROMPT, cancel_kb(callback_data.cat_id))


@router.message(AddSource.waiting_input, F.text)
async def on_source_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cat_id = data["cat_id"]

    added, skipped, bad, limited = [], [], [], False
    for line in message.text.splitlines():
        line = line.strip()
        if not line:
            continue
        # prepare_source заодно проверяет, что канал существует и доступен.
        kind, url, title, error = await prepare_source(line)
        if error:
            bad.append(f"{line} — {error}")
            continue

        limited = limited or (kind == "tg" and tg_reading_limited())
        source_id = await repo.add_source(cat_id, kind, url, title)
        (added if source_id else skipped).append(f"{texts.KIND_ICONS[kind]} {url}")

    report = []
    if added:
        report.append("✅ Добавлено:\n" + "\n".join(added))
    if skipped:
        report.append("↩️ Уже было:\n" + "\n".join(skipped))
    if bad:
        report.append("❌ Не распознано:\n" + "\n".join(bad))
    if limited:
        report.append(TGWEB_NOTE)

    await state.clear()
    sources = await repo.list_sources(cat_id)
    await message.answer(
        "\n\n".join(report) or "Ничего не распознал, попробуйте ещё раз.",
        reply_markup=sources_menu(cat_id, sources),
        disable_web_page_preview=True,
    )
