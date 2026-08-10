"""Управление темами — тем самым тем, по которым идёт выборочный парсинг."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards import TopicCB, cancel_kb, topics_menu
from app.bot.states import AddTopic
from app.bot.utils import safe_edit, split_list
from app.db import repo

router = Router(name="topics")


async def _render(call: CallbackQuery, cat_id: int) -> None:
    category = await repo.get_category(cat_id)
    topics = await repo.list_topics(cat_id)
    if topics:
        body = "\n\n".join(texts.format_topic(t) for t in topics)
    else:
        body = ("Тем нет — а без них парсер не запустится: именно они решают, "
                "какие посты вообще смотреть.")
    text = (
        f"<b>{texts.category_title(category)} — темы</b>\n\n{body}\n\n"
        "<i>Нажатие на тему включает или выключает её, 🗑 — удаляет.</i>"
    )
    await safe_edit(call, text, topics_menu(cat_id, topics))


@router.callback_query(TopicCB.filter(F.action.in_({"list", "toggle", "delete"})))
async def cb_topics(call: CallbackQuery, callback_data: TopicCB) -> None:
    if callback_data.action == "toggle":
        await repo.toggle_topic(callback_data.topic_id)
    elif callback_data.action == "delete":
        await repo.delete_topic(callback_data.topic_id)
        await call.answer("Тема удалена")
    await _render(call, callback_data.cat_id)
    await call.answer()


# --------------------------------------------------------------------------- #
@router.callback_query(TopicCB.filter(F.action == "add"))
async def cb_add_topic(call: CallbackQuery, callback_data: TopicCB,
                       state: FSMContext) -> None:
    await state.set_state(AddTopic.waiting_title)
    await state.update_data(cat_id=callback_data.cat_id)
    await safe_edit(
        call,
        "<b>Шаг 1 из 4.</b> Название темы одной строкой.\n\n"
        "Например: <code>Утечки персональных данных в РФ</code>",
        cancel_kb(callback_data.cat_id),
    )
    await call.answer()


@router.message(AddTopic.waiting_title, F.text)
async def on_topic_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip()[:120])
    await state.set_state(AddTopic.waiting_description)
    await message.answer(
        "<b>Шаг 2 из 4.</b> Опишите, что именно вам интересно по этой теме — "
        "текст читает модель при оценке новости.\n\n"
        "Например: <i>«Интересуют подтверждённые утечки баз российских компаний: "
        "кто пострадал, объём данных, реакция регулятора. Слухи и перепечатки — мимо.»</i>\n\n"
        "Отправьте <code>-</code>, чтобы пропустить."
    )


@router.message(AddTopic.waiting_description, F.text)
async def on_topic_description(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    await state.update_data(description="" if text in ("-", "—") else text[:800])
    await state.set_state(AddTopic.waiting_keywords)
    await message.answer(
        "<b>Шаг 3 из 4.</b> Ключевые слова через запятую — по ним идёт "
        "дешёвый предфильтр до обращения к модели.\n\n"
        "<code>утечка, слив базы, персональные данные, Роскомнадзор, взлом*</code>\n\n"
        "· учитывается основа слова: <code>утечка</code> поймает «утечки», «утечкам»\n"
        "· <code>звёздочка*</code> — совпадение по префиксу\n"
        "· фраза с пробелом ищется целиком\n\n"
        "Отправьте <code>-</code>, если хотите, чтобы все посты рубрики "
        "оценивала модель (дороже, но ничего не пропустите)."
    )


@router.message(AddTopic.waiting_keywords, F.text)
async def on_topic_keywords(message: Message, state: FSMContext) -> None:
    await state.update_data(keywords=split_list(message.text))
    await state.set_state(AddTopic.waiting_stopwords)
    await message.answer(
        "<b>Шаг 4 из 4.</b> Стоп-слова через запятую — пост с ними отбрасывается "
        "сразу, даже если ключевые слова совпали.\n\n"
        "<code>вебинар, курс, скидка, розыгрыш, реклама</code>\n\n"
        "Отправьте <code>-</code>, чтобы пропустить."
    )


@router.message(AddTopic.waiting_stopwords, F.text)
async def on_topic_stopwords(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cat_id = data["cat_id"]
    stopwords = split_list(message.text)

    topic_id = await repo.add_topic(
        cat_id, data["title"], data.get("description", ""),
        data.get("keywords", []), stopwords,
    )
    await state.clear()

    topics = await repo.list_topics(cat_id)
    if topic_id is None:
        answer = "⚠️ Тема с таким названием в этой рубрике уже есть."
    else:
        topic = await repo.get_topic(topic_id)
        answer = "✅ Тема добавлена:\n\n" + texts.format_topic(topic)
        if not topic["keywords"]:
            answer += ("\n\n<i>Ключевых слов нет — все посты рубрики будут "
                       "уходить на анализ модели.</i>")
    await message.answer(answer, reply_markup=topics_menu(cat_id, topics))
