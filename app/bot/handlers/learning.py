"""Обучение на обратной связи: оценки под карточками и отчёт по ним.

Никакого дообучения весов здесь нет — оценки идут в промпт как примеры
(few-shot). Это работает с первого же нажатия, в отличие от файнтюна, которому
нужны сотни размеченных постов.
"""
from __future__ import annotations

from contextlib import suppress
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command, CommandObject

from app.bot.keyboards import FeedbackCB
from app.config import config
from app.db import repo

router = Router(name="learning")

MIN_FOR_FINETUNE = 300


async def _may_rate(user_id: int, item_id: int) -> bool:
    """Админы оценивают всё, остальные — только разрешённые им рубрики."""
    if not config.admin_ids or user_id in config.admin_ids:
        return True
    category_id = await repo.item_category(item_id)
    return await repo.is_rater(user_id, category_id)


@router.callback_query(FeedbackCB.filter())
async def cb_feedback(call: CallbackQuery, callback_data: FeedbackCB) -> None:
    if not await _may_rate(call.from_user.id, callback_data.item_id):
        await call.answer("Оценивать эту рубрику вам не разрешено", show_alert=True)
        return

    saved = await repo.save_feedback(
        callback_data.item_id, call.message.chat.id, callback_data.verdict
    )
    if not saved:
        await call.answer("Эта новость уже удалена из базы", show_alert=True)
        return

    mark = "👍 учту: такое нужно" if callback_data.verdict > 0 else "👎 учту: такое мимо"
    await call.answer(mark)
    # Кнопки убираем — оценка учтена, повторно жать незачем.
    # Сообщение уже отредактировано или слишком старое — не наша забота.
    with suppress(TelegramBadRequest):
        await call.message.edit_reply_markup(reply_markup=None)


RATERS_HELP = (
    "<b>Кто может оценивать новости</b>\n\n"
    "Кнопки 👍/👎 под карточками доступны администраторам бота всегда, "
    "остальным — только по этому списку.\n\n"
    "<code>/raters</code> — весь список\n"
    "<code>/raters финансы</code> — кто оценивает эту рубрику\n"
    "<code>/raters финансы + 123456789</code> — разрешить\n"
    "<code>/raters финансы - 123456789</code> — забрать\n"
    "<code>/raters все + 123456789</code> — доступ ко всем рубрикам\n\n"
    "Вместо ID можно ответить на сообщение человека командой "
    "<code>/raters финансы +</code>.\n"
    "Свой ID показывает /chatid."
)


@router.message(Command("raters"))
async def cmd_raters(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    if not args:
        await _show_raters(message, None)
        return

    # Рубрика: «все» открывает доступ сразу ко всем
    query = args[0]
    category = None
    if query.lower() not in ("все", "всё", "all", "*"):
        category = await repo.find_category(query)
        if category is None:
            names = ", ".join(c["title"] for c in await repo.list_categories())
            await message.answer(
                f"Рубрика «{escape(query)}» не найдена.\n\nЕсть: {names}, все."
            )
            return
    category_id = category["id"] if category else 0

    if len(args) == 1:
        await _show_raters(message, category)
        return

    sign = args[1]
    if sign not in ("+", "-"):
        await message.answer(RATERS_HELP)
        return

    user_id, title = await _target_user(message, args[2:])
    if user_id is None:
        await message.answer(
            "Кого добавляем? Укажите числовой ID или ответьте командой "
            "на сообщение этого человека."
        )
        return

    where = f"{category['emoji']} {category['title']}" if category else "все рубрики"
    if sign == "+":
        await repo.add_rater(user_id, category_id, title)
        await message.answer(
            f"✅ <code>{user_id}</code>{f' ({escape(title)})' if title else ''} "
            f"теперь может оценивать: <b>{escape(where)}</b>."
        )
    else:
        removed = await repo.remove_rater(user_id, category_id)
        await message.answer(
            f"✅ Доступ забран: <code>{user_id}</code> — {escape(where)}."
            if removed else
            f"У <code>{user_id}</code> и не было доступа к «{escape(where)}»."
        )


async def _target_user(message: Message, rest: list[str]) -> tuple[int | None, str]:
    """ID из аргумента или из сообщения, на которое ответили."""
    for arg in rest:
        if arg.lstrip("-").isdigit():
            return int(arg), ""
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        return user.id, user.full_name or (user.username or "")
    return None, ""


async def _show_raters(message: Message, category: dict | None) -> None:
    raters = await repo.list_raters(category["id"] if category else None)
    if not raters:
        scope = f"«{category['title']}»" if category else "ни одной рубрики"
        await message.answer(
            f"Для {scope} отдельных оценщиков нет — кнопки доступны только "
            f"администраторам бота.\n\n{RATERS_HELP}"
        )
        return

    names = {c["id"]: f"{c['emoji']} {c['title']}"
             for c in await repo.list_categories()}
    lines = []
    for r in raters:
        where = names.get(r["category_id"], "все рубрики") if r["category_id"] \
            else "все рубрики"
        who = f"<code>{r['user_id']}</code>"
        if r["title"]:
            who += f" — {escape(r['title'])}"
        lines.append(f"· {who} → {where}")
    head = (f"<b>Оценщики «{escape(category['title'])}»</b>" if category
            else "<b>Оценщики</b>")
    await message.answer(head + "\n" + "\n".join(lines) + "\n\n" + RATERS_HELP)


@router.message(Command("learn"))
async def cmd_learn(message: Message) -> None:
    blocks = []
    total_all = 0
    for category in await repo.list_categories():
        stats = await repo.feedback_stats(category["id"])
        total = stats["total"] or 0
        total_all += total
        line = [f"<b>{category['emoji']} {category['title']}</b>"]
        if not total:
            line.append("оценок пока нет")
            blocks.append("\n".join(line))
            continue

        line.append(f"оценено: {total} · 👍 {stats['liked'] or 0} · "
                    f"👎 {stats['disliked'] or 0}")
        if stats["avg_good"] is not None and stats["avg_bad"] is not None:
            line.append(f"средняя оценка модели: у нужных {stats['avg_good']:.0f}, "
                        f"у лишних {stats['avg_bad']:.0f}")
            if stats["avg_bad"] >= stats["avg_good"]:
                line.append("⚠️ модель не различает нужное и лишнее — "
                            "стоит уточнить описание темы")
        if tags := await repo.disliked_tags(category["id"]):
            line.append("чаще всего в лишнем: " +
                        ", ".join(f"{t} ({n})" for t, n in tags[:5]))
            line.append("<i>кандидаты в стоп-слова</i>")
        blocks.append("\n".join(line))

    head = (
        "<b>Обучение на ваших оценках</b>\n\n"
        "Каждое 👍/👎 под карточкой запоминается и попадает в подсказку модели "
        "при следующем разборе — она видит, что вы уже приняли, а что отвергли.\n"
    )
    tail = (
        f"\n<i>Дообучение самой модели (LoRA) имеет смысл примерно от "
        f"{MIN_FOR_FINETUNE} оценок. Сейчас накоплено {total_all}.</i>"
    )
    await message.answer(head + "\n\n".join(blocks) + tail)
