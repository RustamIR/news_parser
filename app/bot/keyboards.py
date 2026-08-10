"""Инлайн-клавиатуры и callback-данные."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.texts import KIND_ICONS


class MenuCB(CallbackData, prefix="menu"):
    action: str                     # main | help | new_category


class CatCB(CallbackData, prefix="cat"):
    action: str                     # open | sources | topics | digest | settings
                                    # | run | sub | stats
    cat_id: int


class SrcCB(CallbackData, prefix="src"):
    action: str                     # add | toggle | delete | list
    cat_id: int
    src_id: int = 0


class TopicCB(CallbackData, prefix="tpc"):
    action: str                     # add | toggle | delete | list
    cat_id: int
    topic_id: int = 0


class FeedbackCB(CallbackData, prefix="fb"):
    item_id: int
    verdict: int                    # +1 нужно, -1 не нужно


class SetCB(CallbackData, prefix="set"):
    action: str                     # interval | relevance | autosend
    cat_id: int
    value: int = 0


# --------------------------------------------------------------------------- #
def main_menu(categories: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in categories:
        kb.button(
            text=f"{c['emoji']} {c['title']}",
            callback_data=CatCB(action="open", cat_id=c["id"]),
        )
    kb.button(text="➕ Новая рубрика", callback_data=MenuCB(action="new_category"))
    kb.button(text="❓ Как это работает", callback_data=MenuCB(action="help"))
    kb.adjust(1)
    return kb.as_markup()


def category_menu(category: dict, subscribed: bool,
                  sources_count: int, topics_count: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    cid = category["id"]
    kb.button(text="📰 Дайджест", callback_data=CatCB(action="digest", cat_id=cid))
    kb.button(text="🔄 Проверить сейчас", callback_data=CatCB(action="run", cat_id=cid))
    kb.button(text=f"🔗 Источники ({sources_count})",
              callback_data=CatCB(action="sources", cat_id=cid))
    kb.button(text=f"🎯 Темы ({topics_count})",
              callback_data=CatCB(action="topics", cat_id=cid))
    kb.button(text="📊 Статистика", callback_data=CatCB(action="stats", cat_id=cid))
    kb.button(text="⚙️ Настройки", callback_data=CatCB(action="settings", cat_id=cid))
    kb.button(
        text="📢 Канал публикации" if category["target_chat_id"] else "📢 Привязать канал",
        callback_data=CatCB(action="target", cat_id=cid),
    )
    kb.button(
        text="🔕 Отписаться" if subscribed else "🔔 Подписаться",
        callback_data=CatCB(action="sub", cat_id=cid),
    )
    kb.button(text="⬅️ Назад", callback_data=MenuCB(action="main"))
    kb.adjust(2, 2, 2, 1, 1, 1)
    return kb.as_markup()


def sources_menu(cat_id: int, sources: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in sources:
        icon = KIND_ICONS.get(s["kind"], "•")
        label = s["url"] if len(s["url"]) <= 28 else s["url"][:27] + "…"
        kb.row(
            InlineKeyboardButton(
                text=f"{'🟢' if s['enabled'] else '⚪️'} {icon} {label}",
                callback_data=SrcCB(action="toggle", cat_id=cat_id,
                                    src_id=s["id"]).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=SrcCB(action="delete", cat_id=cat_id,
                                    src_id=s["id"]).pack(),
            ),
        )
    kb.row(InlineKeyboardButton(
        text="➕ Добавить источник",
        callback_data=SrcCB(action="add", cat_id=cat_id).pack(),
    ))
    kb.row(InlineKeyboardButton(
        text="⬅️ К рубрике",
        callback_data=CatCB(action="open", cat_id=cat_id).pack(),
    ))
    return kb.as_markup()


def topics_menu(cat_id: int, topics: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in topics:
        label = t["title"] if len(t["title"]) <= 28 else t["title"][:27] + "…"
        kb.row(
            InlineKeyboardButton(
                text=f"{'🟢' if t['enabled'] else '⚪️'} 🎯 {label}",
                callback_data=TopicCB(action="toggle", cat_id=cat_id,
                                      topic_id=t["id"]).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=TopicCB(action="delete", cat_id=cat_id,
                                      topic_id=t["id"]).pack(),
            ),
        )
    kb.row(InlineKeyboardButton(
        text="➕ Добавить тему",
        callback_data=TopicCB(action="add", cat_id=cat_id).pack(),
    ))
    kb.row(InlineKeyboardButton(
        text="⬅️ К рубрике",
        callback_data=CatCB(action="open", cat_id=cat_id).pack(),
    ))
    return kb.as_markup()


def settings_menu(category: dict) -> InlineKeyboardMarkup:
    cid = category["id"]
    kb = InlineKeyboardBuilder()

    kb.row(InlineKeyboardButton(text="⏱ Интервал опроса", callback_data="noop"))
    kb.row(*[
        InlineKeyboardButton(
            text=("• " if category["poll_interval_min"] == v else "") + label,
            callback_data=SetCB(action="interval", cat_id=cid, value=v).pack(),
        )
        for v, label in ((10, "10 м"), (30, "30 м"), (60, "1 ч"), (180, "3 ч"), (720, "12 ч"))
    ])

    kb.row(InlineKeyboardButton(text="🎚 Порог релевантности", callback_data="noop"))
    kb.row(*[
        InlineKeyboardButton(
            text=("• " if category["min_relevance"] == v else "") + f"{v}%",
            callback_data=SetCB(action="relevance", cat_id=cid, value=v).pack(),
        )
        for v in (40, 55, 70, 85)
    ])

    kb.row(InlineKeyboardButton(
        text=f"📨 Присылать сразу: {'вкл' if category['autosend'] else 'выкл'}",
        callback_data=SetCB(action="autosend", cat_id=cid).pack(),
    ))
    kb.row(InlineKeyboardButton(
        text="⬅️ К рубрике",
        callback_data=CatCB(action="open", cat_id=cid).pack(),
    ))
    return kb.as_markup()


def feedback_kb(item_id: int) -> InlineKeyboardMarkup | None:
    """Кнопки оценки под карточкой новости.

    Оценки уходят в промпт как примеры, поэтому каждое нажатие реально меняет
    то, что бот пропустит в следующий раз.
    """
    if not item_id:
        return None
    kb = InlineKeyboardBuilder()
    kb.button(text="👍 В тему", callback_data=FeedbackCB(item_id=item_id, verdict=1))
    kb.button(text="👎 Мимо", callback_data=FeedbackCB(item_id=item_id, verdict=-1))
    kb.adjust(2)
    return kb.as_markup()


def back_to_category(cat_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ К рубрике", callback_data=CatCB(action="open", cat_id=cat_id))
    return kb.as_markup()


def cancel_kb(cat_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✖️ Отмена", callback_data=CatCB(action="open", cat_id=cat_id))
    return kb.as_markup()
