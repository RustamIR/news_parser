"""Парсер телеграм-каналов и чатов через Telethon (user-сессия).

Бот не может читать произвольные каналы — Bot API отдаёт только те чаты, куда
бота добавили админом. Поэтому чтение идёт от имени обычного аккаунта: один раз
проходим авторизацию через `login_telegram.py`, получаем StringSession, и дальше
клиент работает в фоне рядом с ботом.
"""
from __future__ import annotations

import logging
import re

from telethon import TelegramClient, utils
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    UsernameNotOccupiedError,
)
from telethon.sessions import StringSession

from app.config import config
from app.parsers.base import RawItem

log = logging.getLogger(__name__)

# Сколько сообщений тянем за один опрос канала (защита от лавины при первом запуске)
MAX_MESSAGES_PER_POLL = 30


class TelegramCollector:
    """Обёртка над Telethon с ленивым подключением."""

    def __init__(self) -> None:
        self._client: TelegramClient | None = None

    @property
    def configured(self) -> bool:
        """Ключи и сессия прописаны в .env — но ещё не факт, что они рабочие."""
        return config.telethon_ready

    @property
    def available(self) -> bool:
        """Клиент реально подключён и умеет читать каналы.

        Именно это должно решать, каким транспортом читать канал: ключи могут
        быть на месте, а сессия — просроченной или ботовой.
        """
        return self._client is not None

    async def start(self) -> None:
        if not self.configured:
            log.warning(
                "Telethon не настроен (нет API_ID/API_HASH/SESSION) — "
                "телеграм-каналы читаются через публичную витрину t.me/s/"
            )
            return
        self._client = TelegramClient(
            StringSession(config.session), config.api_id, config.api_hash
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            log.error("Сессия Telethon невалидна — перезапустите login_telegram.py")
            await self._client.disconnect()
            self._client = None
            return

        # Сессия бота бесполезна: Bot API не отдаёт историю чужих каналов.
        # Ошибка вылезла бы только при первом опросе, поэтому проверяем сразу.
        me = await self._client.get_me()
        if getattr(me, "bot", False):
            log.error(
                "TELEGRAM_SESSION создана от имени бота (@%s), а не аккаунта — "
                "читать чужие каналы так нельзя. Перезапустите login_telegram.py "
                "и введите номер телефона, а не токен бота. Пока каналы читаются "
                "через публичную витрину t.me/s/",
                me.username or "?",
            )
            await self._client.disconnect()
            self._client = None
            return
        log.info("Telethon подключён как %s", me.first_name or me.username or me.id)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    @staticmethod
    def _post_url(entity, name: str, message_id: int) -> str:
        """Ссылка на пост: у публичного канала по имени, у закрытого — /c/<id>."""
        username = getattr(entity, "username", None)
        if username:
            return f"https://t.me/{username}/{message_id}"
        if not name.lstrip("-").isdigit():
            return f"https://t.me/{name}/{message_id}"
        internal = str(utils.get_peer_id(entity)).removeprefix("-100")
        return f"https://t.me/c/{internal}/{message_id}"

    async def resolve(self, value: str):
        """Приводит запись источника к сущности Telegram.

        Публичный канал адресуется юзернеймом, закрытый — числовым id или
        пригласительной ссылкой. Ссылка работает, только если аккаунт уже
        состоит в канале: вступать за пользователя мы не станем.
        """
        ref: str | int = value.lstrip("@")
        if isinstance(ref, str) and re.fullmatch(r"-?\d{6,}", ref):
            ref = int(ref)
        return await self._client.get_entity(ref)

    async def resolve_title(self, value: str) -> tuple[str, str]:
        """Проверка канала при добавлении: возвращает (название, текст ошибки)."""
        if self._client is None:
            return "", ""                          # не настроен — проверять нечем
        try:
            entity = await self.resolve(value)
        except ChannelPrivateError:
            return "", ("канал закрыт для вашего аккаунта — вступите в него, "
                        "и источник заработает")
        except (UsernameNotOccupiedError, ValueError, TypeError):
            if value.startswith("http"):
                return "", ("по пригласительной ссылке канал не открылся — "
                            "аккаунт должен уже состоять в этом канале")
            return "", "канал не найден или закрыт"
        except FloodWaitError as e:
            return "", f"Telegram просит подождать {e.seconds} с"
        return getattr(entity, "title", "") or getattr(entity, "username", ""), ""

    async def canonical_id(self, value: str) -> str:
        """Числовой id канала — стабильная запись для закрытых каналов.

        Юзернейм у закрытого канала отсутствует, а пригласительная ссылка может
        быть отозвана, поэтому храним то, что не меняется.
        """
        if self._client is None:
            return value
        try:
            entity = await self.resolve(value)
        except Exception:
            return value
        marked = utils.get_peer_id(entity)
        return str(marked)

    async def fetch(self, source: dict) -> tuple[list[RawItem], str]:
        """Возвращает (новые посты, id последнего сообщения)."""
        if self._client is None:
            raise RuntimeError("Telethon-клиент не запущен")

        entity_name = source["url"].lstrip("@")
        min_id = int(source["last_external_id"] or 0)
        # Первый заход: не тянем всю историю, берём только последние посты.
        limit = MAX_MESSAGES_PER_POLL if min_id else 5

        items: list[RawItem] = []
        last_id = min_id
        try:
            entity = await self.resolve(source["url"])
            async for msg in self._client.iter_messages(entity, limit=limit, min_id=min_id):
                last_id = max(last_id, msg.id)
                text = (msg.message or "").strip()
                if not text:
                    continue                       # медиа без подписи — пропускаем
                items.append(
                    RawItem(
                        source_id=source["id"],
                        category_id=source["category_id"],
                        external_id=str(msg.id),
                        url=self._post_url(entity, entity_name, msg.id),
                        title=text.split("\n", 1)[0][:200],
                        content=text,
                        published_at=msg.date,
                        meta={"channel": entity_name},
                    )
                )
        except FloodWaitError as e:
            raise RuntimeError(f"Telegram просит подождать {e.seconds} с") from e
        except (ChannelPrivateError, UsernameNotOccupiedError, ValueError) as e:
            raise RuntimeError(f"канал недоступен: {e}") from e

        items.reverse()                            # от старых к новым
        return items, str(last_id)


tg_collector = TelegramCollector()
