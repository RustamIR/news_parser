"""Ограничение доступа.

Правила разные для лички и для группы сообщества:

* личка — отвечаем только тем, кто указан в ADMIN_IDS;
* управляющая группа — плюс её администраторам;
* любой другой групповой чат — молчим. Бота могли добавить куда угодно,
  и отвечать «нет доступа» на каждое сообщение в чужом чате нельзя.

Пустой ADMIN_IDS отключает проверку целиком — удобно для первого запуска,
но в сообществе так оставлять нельзя.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import config
from app.db import repo

log = logging.getLogger(__name__)

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
ADMIN_STATUSES = {"creator", "administrator"}


class AccessMiddleware(BaseMiddleware):
    def __init__(self, allowed: set[int]) -> None:
        self.allowed = allowed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:                          # посты каналов, служебные события
            return None

        if not self.allowed or user.id in self.allowed:
            return await handler(event, data)

        # Оценщики — не админы: им открыты только кнопки 👍/👎 под карточками,
        # ничего больше. Конкретную рубрику проверяет уже сам обработчик.
        if isinstance(event, CallbackQuery) and (event.data or "").startswith("fb:"):
            if await repo.is_rater(user.id):
                return await handler(event, data)

        chat = data.get("event_chat")
        if chat is not None and chat.type in GROUP_TYPES:
            if await self._is_control_group_admin(chat.id, user.id, data):
                return await handler(event, data)
            return None                           # чужой чат или не админ — молчим

        log.warning("Отклонён доступ: user_id=%s (@%s)", user.id, user.username)
        if isinstance(event, Message):
            await event.answer("⛔️ Нет доступа к этому боту.")
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔️ Нет доступа", show_alert=True)
        return None

    @staticmethod
    async def _is_control_group_admin(chat_id: int, user_id: int,
                                      data: dict[str, Any]) -> bool:
        if chat_id != await repo.control_chat_id():
            return False
        try:
            member = await data["bot"].get_chat_member(chat_id, user_id)
        except Exception as e:                    # бота выгнали, чат удалён и т.п.
            log.info("Не удалось проверить права в чате %s: %s", chat_id, e)
            return False
        return member.status in ADMIN_STATUSES


def access_middleware() -> AccessMiddleware:
    return AccessMiddleware(config.admin_ids)
