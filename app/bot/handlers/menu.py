"""Главное меню, карточка рубрики, ручной запуск, статистика, создание рубрик."""
from __future__ import annotations

import re
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.analysis.llm import analyzer
from app.bot import texts
from app.bot.keyboards import CatCB, MenuCB, back_to_category, category_menu, main_menu
from app.bot.states import AddCategory
from app.bot.utils import safe_edit
from app.config import config
from app.db import repo
from app.parsers.tg import tg_collector
from app.scheduler import ParserScheduler

router = Router(name="menu")


def _greeting() -> str:
    warnings = []
    if not tg_collector.available:
        warnings.append("Telethon не настроен — каналы читаются через публичную "
                        "витрину t.me/s/ (только открытые, без закрытых и чатов)")
    if not analyzer.available:
        warnings.append("Модель для анализа не подключена — фильтрация только "
                        "по ключевым словам")
    text = "👋 <b>Новостной парсер</b>\n\nВыберите рубрику:"
    text += f"\n\n🧠 Анализ: {analyzer.title}"
    if warnings:
        text += "\n\n" + "\n".join(f"⚠️ {w}" for w in warnings)
    return text


async def _category_view(category: dict, chat_id: int) -> tuple[str, object]:
    sources = await repo.list_sources(category["id"])
    topics = await repo.list_topics(category["id"])
    subscribed = await repo.is_subscribed(chat_id, category["id"])

    active_sources = sum(1 for s in sources if s["enabled"])
    active_topics = sum(1 for t in topics if t["enabled"])
    target = (
        f"📢 Публикация: {escape(category['target_title'])}"
        if category["target_chat_id"] else "📢 Канал публикации не привязан"
    )
    text = (
        f"<b>{texts.category_title(category)}</b>\n\n"
        f"{escape(category['prompt_hint'] or '')}\n\n"
        f"🔗 Источников: {active_sources} из {len(sources)}\n"
        f"🎯 Тем: {active_topics} из {len(topics)}\n"
        f"{target}\n"
        f"⏱ Опрос каждые {category['poll_interval_min']} мин · "
        f"порог {category['min_relevance']}%"
    )
    if not topics:
        text += "\n\n⚠️ Пока не задано ни одной темы — парсинг не запустится."
    elif not sources:
        text += "\n\n⚠️ Не добавлено ни одного источника."
    markup = category_menu(category, subscribed, len(sources), len(topics))
    return text, markup


# --------------------------------------------------------------------------- #
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    categories = await repo.list_categories()
    await message.answer(_greeting(), reply_markup=main_menu(categories))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP, disable_web_page_preview=True)


@router.callback_query(MenuCB.filter(F.action == "help"))
async def cb_help(call: CallbackQuery) -> None:
    categories = await repo.list_categories()
    await safe_edit(call, texts.HELP, main_menu(categories))
    await call.answer()


@router.callback_query(MenuCB.filter(F.action == "main"))
async def cb_main(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    categories = await repo.list_categories()
    await safe_edit(call, _greeting(), main_menu(categories))
    await call.answer()


@router.callback_query(CatCB.filter(F.action == "open"))
async def cb_open_category(call: CallbackQuery, callback_data: CatCB,
                           state: FSMContext) -> None:
    await state.clear()
    category = await repo.get_category(callback_data.cat_id)
    if not category:
        await call.answer("Рубрика не найдена", show_alert=True)
        return
    text, markup = await _category_view(category, call.message.chat.id)
    await safe_edit(call, text, markup)
    await call.answer()


@router.callback_query(CatCB.filter(F.action == "sub"))
async def cb_toggle_subscription(call: CallbackQuery, callback_data: CatCB) -> None:
    cid = callback_data.cat_id
    chat_id = call.message.chat.id
    if await repo.is_subscribed(chat_id, cid):
        await repo.unsubscribe(chat_id, cid)
        note = "Отписка оформлена"
    else:
        await repo.subscribe(chat_id, cid)
        note = "Новости будут приходить сюда"

    category = await repo.get_category(cid)
    text, markup = await _category_view(category, chat_id)
    await safe_edit(call, text, markup)
    await call.answer(note)


@router.callback_query(CatCB.filter(F.action == "stats"))
async def cb_stats(call: CallbackQuery, callback_data: CatCB) -> None:
    category = await repo.get_category(callback_data.cat_id)
    data = await repo.stats(callback_data.cat_id)
    await safe_edit(call, texts.format_stats(category, data),
                    back_to_category(callback_data.cat_id))
    await call.answer()


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    blocks = []
    for category in await repo.list_categories():
        blocks.append(texts.format_stats(category, await repo.stats(category["id"])))
    await message.answer("\n\n———\n\n".join(blocks))


# --------------------------------------------------------------------------- #
@router.callback_query(CatCB.filter(F.action == "run"))
async def cb_run_now(call: CallbackQuery, callback_data: CatCB,
                     scheduler: ParserScheduler) -> None:
    await call.answer("Запускаю проверку…")
    await safe_edit(call, "🔄 Опрашиваю источники, это может занять минуту…", None)

    report = await scheduler.run_category(callback_data.cat_id)
    category = await repo.get_category(callback_data.cat_id)
    if report is None:
        await safe_edit(call, "Проверка уже идёт в фоне, подождите немного.",
                        back_to_category(callback_data.cat_id))
        return

    text, markup = await _category_view(category, call.message.chat.id)
    await safe_edit(call, f"{report.as_text()}\n\n———\n\n{text}", markup)


@router.message(Command("run"))
async def cmd_run(message: Message, scheduler: ParserScheduler) -> None:
    status = await message.answer("🔄 Опрашиваю все рубрики…")
    reports = []
    for category in await repo.list_categories(only_enabled=True):
        report = await scheduler.run_category(category["id"])
        if report:
            reports.append(report.as_text())
    await status.edit_text("\n\n———\n\n".join(reports) or "Нет активных рубрик.")


# --------------------------------------------------------------------------- #
@router.callback_query(MenuCB.filter(F.action == "new_category"))
async def cb_new_category(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddCategory.waiting_title)
    await safe_edit(
        call,
        "Название новой рубрики (можно с эмодзи в начале).\n\n"
        "Например: <code>🚀 Космос и запуски</code>",
        None,
    )
    await call.answer()


@router.message(AddCategory.waiting_title, F.text)
async def on_category_title(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    emoji, title = "🗂", raw
    # Отделяем ведущий эмодзи от названия, если он есть.
    if parts := raw.split(" ", 1):
        if len(parts) == 2 and not re.match(r"^[\w\d]", parts[0], re.UNICODE):
            emoji, title = parts[0], parts[1].strip()
    key = re.sub(r"[^a-z0-9]+", "_", title.lower())[:24] or f"cat_{message.message_id}"

    await state.update_data(title=title, emoji=emoji, key=key)
    await state.set_state(AddCategory.waiting_hint)
    await message.answer(
        "Опишите одним абзацем, что относится к этой рубрике — этот текст "
        "получает модель при оценке новостей.\n\nОтправьте <code>-</code>, чтобы пропустить."
    )


@router.message(AddCategory.waiting_hint, F.text)
async def on_category_hint(message: Message, state: FSMContext,
                           scheduler: ParserScheduler) -> None:
    data = await state.get_data()
    hint = "" if message.text.strip() in ("-", "—") else message.text.strip()

    cat_id = await repo.create_category(data["key"], data["title"], data["emoji"], hint)
    await repo.update_category(
        cat_id,
        poll_interval_min=config.default_poll_interval,
        min_relevance=config.default_min_relevance,
    )
    await state.clear()
    await scheduler.reload()

    category = await repo.get_category(cat_id)
    text, markup = await _category_view(category, message.chat.id)
    await message.answer(f"✅ Рубрика создана.\n\n{text}", reply_markup=markup)


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()
