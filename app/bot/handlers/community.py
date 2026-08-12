"""Работа в сообществе: управляющая группа, каналы публикации и форма пополнения."""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Message,
    MessageOriginChannel,
    MessageOriginChat,
)

from app.bot.forms import (
    TEMPLATE,
    apply_form,
    looks_like_form,
    parse_form,
    render_editable,
    render_setup,
)
from app.bot.keyboards import CatCB, back_to_category, cancel_kb
from app.bot.states import BindChannel
from app.bot.utils import TG_ID_RE, TG_PRIVATE_POST_RE, safe_edit
from app.db import repo

log = logging.getLogger(__name__)
router = Router(name="community")

GROUPS = {ChatType.GROUP, ChatType.SUPERGROUP}


# --------------------------------------------------------------------------- #
# Управляющая группа сообщества
# --------------------------------------------------------------------------- #
@router.message(Command("bind"), F.chat.type.in_(GROUPS))
async def cmd_bind(message: Message) -> None:
    """Назначает эту группу управляющей: отсюда принимаются формы и команды."""
    await repo.set_setting("control_chat_id", str(message.chat.id))
    await repo.set_setting("control_chat_title", message.chat.title or "")
    await message.answer(
        f"✅ Группа <b>{escape(message.chat.title or 'без названия')}</b> "
        f"назначена управляющей.\n\n"
        f"Теперь её администраторы могут пополнять парсер прямо здесь: "
        f"пришлите форму (шаблон — /form) или используйте /add.\n\n"
        f"⚠️ Чтобы бот видел формы без команды, отключите ему privacy mode: "
        f"в @BotFather → /setprivacy → Disable."
    )


@router.message(Command("unbind"), F.chat.type.in_(GROUPS))
async def cmd_unbind(message: Message) -> None:
    if str(message.chat.id) != await repo.get_setting("control_chat_id"):
        await message.answer("Эта группа и так не управляющая.")
        return
    await repo.set_setting("control_chat_id", "0")
    await message.answer("Группа отвязана — команды отсюда больше не принимаются.")


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    await message.answer(
        f"ID этого чата: <code>{message.chat.id}</code>\n"
        f"Ваш ID: <code>{message.from_user.id}</code>"
    )


# --------------------------------------------------------------------------- #
# Форма пополнения
# --------------------------------------------------------------------------- #
@router.message(Command("form"))
async def cmd_form(message: Message, command: CommandObject) -> None:
    """Шаблон формы плюс то, что уже настроено.

    Форма чаще всего дополняет существующее, а не создаёт с нуля, поэтому
    видеть текущие темы и источники нужно прямо здесь.
    """
    query = (command.args or "").strip()
    category = await repo.find_category(query) if query else None
    if query and category is None:
        names = ", ".join(c["title"] for c in await repo.list_categories())
        await message.answer(
            f"Рубрика «{escape(query)}» не найдена.\n\nЕсть: {names}."
        )
        return

    if category is None:
        setup = await render_setup()
        await message.answer(
            setup + "\n\n<i>Текущие настройки рубрики в виде готовой формы: "
                    "<code>/form ИБ</code></i>",
            disable_web_page_preview=True,
        )
        await message.answer(TEMPLATE)
        return

    # Каждый блок — самостоятельная форма: скопируйте, поправьте, отправьте назад.
    blocks = await render_editable(category)
    await message.answer(
        f"<b>{category['emoji']} {escape(category['title'])}</b> — текущие "
        f"настройки формами. Нажмите на блок, чтобы скопировать; поправьте и "
        f"отправьте обратно.\n\n"
        f"<i>«режим: замена» значит, что списки станут ровно такими, как в "
        f"тексте: удалённое слово исчезнет. Уберёте эту строку — слова будут "
        f"дополняться, а не заменяться.</i>"
    )
    for block in blocks:
        # Telegram режет сообщения после 4096 символов — по блоку на сообщение.
        await message.answer(f"<pre>{escape(block)}</pre>",
                             disable_web_page_preview=True)


@router.message(Command("add"))
async def cmd_add(message: Message) -> None:
    text = message.text or message.caption or ""
    if not looks_like_form(text):
        await message.answer(
            "После /add нужна сама форма — в том же сообщении, с новой строки.\n\n"
            "Шаблон: /form"
        )
        return
    await message.answer(await apply_form(parse_form(text)), disable_web_page_preview=True)


@router.message(F.text.func(looks_like_form), F.chat.type.in_(GROUPS))
async def on_group_form(message: Message) -> None:
    """Форма, отправленная в управляющую группу без команды."""
    if str(message.chat.id) != await repo.get_setting("control_chat_id"):
        return
    await message.reply(await apply_form(parse_form(message.text)),
                        disable_web_page_preview=True)


@router.message(F.text.func(looks_like_form), F.chat.type == ChatType.PRIVATE)
async def on_private_form(message: Message, state: FSMContext) -> None:
    """То же самое в личке — но не мешаем пошаговому мастеру."""
    if await state.get_state() is not None:
        return
    await message.answer(await apply_form(parse_form(message.text)),
                         disable_web_page_preview=True)


# --------------------------------------------------------------------------- #
# Куда публиковать
# --------------------------------------------------------------------------- #
@router.message(Command("target"))
async def cmd_target(message: Message, command: CommandObject, bot: Bot) -> None:
    """Привязывает рубрику к текущему чату — без пересылок и без админки.

    В группе боту достаточно быть участником, так что этот путь позволяет
    вообще не выдавать ему прав администратора.
    """
    query = (command.args or "").strip()
    categories = await repo.list_categories()
    hint = "\n".join(f"· <code>/target {c['key']}</code> — {c['emoji']} {c['title']}"
                     for c in categories)

    if not query:
        await message.answer(
            f"Укажите рубрику, новости которой должны приходить в этот чат:\n\n{hint}"
        )
        return

    if query in ("-", "—", "off", "стоп"):
        stopped = [c["title"] for c in categories
                   if c["target_chat_id"] == message.chat.id]
        for c in categories:
            if c["target_chat_id"] == message.chat.id:
                await repo.set_category_target(c["id"], 0, "")
        await message.answer(
            f"Отвязано: {', '.join(stopped)}" if stopped
            else "В этот чат ничего и не публиковалось."
        )
        return

    category = await repo.find_category(query)
    if category is None:
        await message.answer(f"Рубрика «{escape(query)}» не найдена.\n\n{hint}")
        return

    ok, problem = await _check_can_post(message.chat, bot)
    if not ok:
        await message.answer(f"❌ {problem}")
        return

    title = message.chat.title or message.chat.full_name or str(message.chat.id)
    await repo.set_category_target(category["id"], message.chat.id, title)
    await message.answer(
        f"✅ Новости рубрики <b>{category['emoji']} {escape(category['title'])}</b> "
        f"будут приходить сюда.\n\n"
        f"Отключить — <code>/target -</code> в этом же чате."
    )


# --------------------------------------------------------------------------- #
@router.callback_query(CatCB.filter(F.action == "target"))
async def cb_bind_channel(call: CallbackQuery, callback_data: CatCB,
                          state: FSMContext) -> None:
    category = await repo.get_category(callback_data.cat_id)
    current = (
        f"Сейчас: <b>{escape(category['target_title'])}</b>\n\n"
        if category["target_chat_id"] else "Сейчас канал не привязан.\n\n"
    )
    await state.set_state(BindChannel.waiting_channel)
    await state.update_data(cat_id=callback_data.cat_id)
    body = (
        f"<b>Куда публиковать — {escape(category['title'])}</b>\n\n{current}"
        f"<b>Без выдачи прав боту</b>\n"
        f"Отправьте <code>/target {category['key']}</code> в той группе или в том "
        f"чате, куда хотите получать новости. В группе боту хватает обычного "
        f"участника, админка не нужна. Работает и в личке.\n\n"
        f"<b>Отдельным каналом</b>\n"
        f"Добавьте бота в канал администратором с правом «Публикация сообщений», "
        f"затем пришлите сюда любое из:\n"
        f"· <b>@юзернейм</b> — для публичного канала\n"
        f"· <b>пересылку любого поста</b> из канала\n"
        f"· <b>числовой ID</b> вида <code>-1001234567890</code> или ссылку на пост "
        f"<code>t.me/c/…</code> — для закрытого канала, скрытого от других\n\n"
        f"<i>У закрытого канала юзернейма нет, а пересылка из него может быть "
        f"запрещена защитой контента — тогда работает только ID.</i>\n\n"
        f"Админство обязательно: в каналы Telegram посторонним писать не даёт.\n\n"
        f"Чтобы отвязать, отправьте <code>-</code>."
    )
    await call.answer()
    await safe_edit(call, body, cancel_kb(callback_data.cat_id))


@router.message(BindChannel.waiting_channel)
async def on_channel_input(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    cat_id = data["cat_id"]

    if (message.text or "").strip() in ("-", "—"):
        await repo.set_category_target(cat_id, 0, "")
        await state.clear()
        await message.answer("Канал отвязан — новости идут только подписчикам.",
                             reply_markup=back_to_category(cat_id))
        return

    chat, error = await _resolve_channel(message, bot)
    if error:
        await message.answer(f"❌ {error}", reply_markup=cancel_kb(cat_id))
        return

    ok, problem = await _check_can_post(chat, bot)
    if not ok:
        await message.answer(f"❌ {problem}", reply_markup=cancel_kb(cat_id))
        return

    title = chat.title or chat.full_name or str(chat.id)
    await repo.set_category_target(cat_id, chat.id, title)
    await state.clear()
    category = await repo.get_category(cat_id)
    await message.answer(
        f"✅ Новости рубрики <b>{escape(category['title'])}</b> будут "
        f"публиковаться в <b>{escape(title)}</b>.",
        reply_markup=back_to_category(cat_id),
    )


async def _resolve_channel(message: Message, bot: Bot):
    """Достаёт чат из пересланного поста или из @юзернейма.

    Пересылка работает и из канала, и из группы: у канала источник приходит
    как MessageOriginChannel, у группы — как MessageOriginChat.
    """
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        return origin.chat, ""
    if isinstance(origin, MessageOriginChat):
        return origin.sender_chat, ""
    if forwarded := message.forward_from_chat:          # старый формат Bot API
        return forwarded, ""

    raw = (message.text or "").strip()
    if not raw:
        return None, "Пришлите @юзернейм, числовой ID или перешлите пост из канала."

    # Закрытый канал: у него нет юзернейма, зато есть постоянный числовой ID.
    # Пересылка из таких каналов часто запрещена защитой контента, поэтому ID —
    # единственный способ их привязать.
    chat_id = ""
    if m := TG_PRIVATE_POST_RE.match(raw):
        chat_id = f"-100{m.group(1)}"
    elif TG_ID_RE.match(raw):
        chat_id = raw
    if chat_id:
        try:
            return await bot.get_chat(int(chat_id)), ""
        except Exception as e:
            log.info("Не удалось получить чат %s: %s", chat_id, e)
            return None, (
                f"Канал <code>{escape(chat_id)}</code> боту не виден. "
                "Проверьте, что он добавлен туда администратором."
            )

    username = raw.replace("https://", "").replace("t.me/", "").lstrip("@").strip("/")
    if not username:
        return None, "Не разобрал юзернейм канала."
    try:
        return await bot.get_chat(f"@{username}"), ""
    except Exception as e:
        log.info("Не удалось получить чат @%s: %s", username, e)
        return None, (
            f"Канал @{escape(username)} не найден или бот его не видит.\n"
            "У закрытого канала юзернейма нет — пришлите числовой ID "
            "(<code>-100…</code>) или ссылку на любой его пост."
        )


async def _check_can_post(chat, bot: Bot) -> tuple[bool, str]:
    """Может ли бот писать в этот чат.

    Требования Telegram разные: в канал пускают только администратора, а в
    группу — любого участника. Поэтому группа позволяет обойтись без выдачи
    боту админских прав вообще.
    """
    if chat.type == ChatType.PRIVATE:
        return True, ""

    try:
        member = await bot.get_chat_member(chat.id, (await bot.me()).id)
    except Exception as e:
        log.info("Не удалось проверить права в %s: %s", chat.id, e)
        return False, ("Бот не состоит в этом чате — добавьте его туда "
                       "и повторите.")

    if chat.type == ChatType.CHANNEL:
        if member.status != "administrator":
            return False, (
                "В канал Telegram пускает только администраторов — это его "
                "правило, обойти нельзя.\n\n"
                "Если давать боту админку не хочется, привяжите вместо канала "
                "<b>группу</b>: там достаточно обычного участника. Отправьте "
                "в нужной группе <code>/target ИБ</code>."
            )
        if getattr(member, "can_post_messages", None) is False:
            return False, "У бота нет права «Публикация сообщений» в этом канале."
        return True, ""

    if member.status in ("left", "kicked"):
        return False, "Бот не в этой группе — добавьте его участником."
    return True, ""
