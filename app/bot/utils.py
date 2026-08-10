"""Мелкие помощники для хендлеров."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

TG_PREVIEW_RE = re.compile(r"^(?:https?://)?t\.me/s/([A-Za-z0-9_]{3,})/?$")
TG_LINK_RE = re.compile(r"^(?:https?://)?t\.me/([A-Za-z0-9_]{3,})/?$")
USERNAME_RE = re.compile(r"^@?([A-Za-z0-9_]{3,})$")
# Закрытые каналы: пригласительная ссылка и ссылка на пост вида t.me/c/<id>/<msg>
TG_INVITE_RE = re.compile(
    r"^(?:https?://)?t\.me/(?:joinchat/|\+)([A-Za-z0-9_-]{8,})/?$"
)
TG_PRIVATE_POST_RE = re.compile(r"^(?:https?://)?t\.me/c/(\d+)(?:/\d+)*/?$")
TG_ID_RE = re.compile(r"^-100\d{6,}$")
FEED_HINTS = ("rss", "feed", "atom", ".xml")


async def safe_edit(call: CallbackQuery, text: str,
                    markup: InlineKeyboardMarkup | None = None) -> None:
    """Правит сообщение, молча проглатывая «message is not modified»."""
    try:
        await call.message.edit_text(text, reply_markup=markup,
                                     disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


def detect_source(raw: str) -> tuple[str, str] | None:
    """Определяет тип источника по строке.

    '@channel' / 't.me/channel'      -> ('tg',    'channel')  через Telethon
    't.me/s/channel'                 -> ('tgweb', 'channel')  публичная витрина
    't.me/+hash', 't.me/c/123/4'     -> ('tg',    ссылка/-100123)  закрытый канал
    '-1001234567890'                 -> ('tg',    '-1001234567890')
    'https://site.ru/rss.xml'        -> ('rss',   url)
    'https://site.ru/news'           -> ('web',   url)
    """
    value = raw.strip().rstrip("/")
    if not value:
        return None

    if m := TG_PREVIEW_RE.match(value):
        return "tgweb", m.group(1)
    # Закрытые каналы — до общих правил: у них нет юзернейма, только ссылка или id
    if TG_ID_RE.match(value):
        return "tg", value
    if m := TG_PRIVATE_POST_RE.match(value):
        return "tg", f"-100{m.group(1)}"
    if TG_INVITE_RE.match(value):
        # Хэш приглашения превратим в числовой id при добавлении источника.
        return "tg", value if "://" in value else f"https://{value}"
    if m := TG_LINK_RE.match(value):
        return "tg", m.group(1)

    if "://" not in value and "." not in value:
        if m := USERNAME_RE.match(value):
            return "tg", m.group(1)
        return None

    url = value if "://" in value else f"https://{value}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    lowered = url.lower()
    kind = "rss" if any(h in lowered for h in FEED_HINTS) else "web"
    return kind, url


def tg_reading_limited() -> bool:
    """Telethon не поднят — телеграм-каналы читаются только через витрину."""
    from app.parsers.tg import tg_collector

    return not tg_collector.available


async def prepare_source(raw: str) -> tuple[str, str, str, str]:
    """Разбирает строку источника: -> (kind, url, title, ошибка).

    Канал всегда сохраняется как `tg` — даже когда Telethon не поднят. Каким
    транспортом его читать, решает конвейер на каждом проходе, поэтому источник
    сам начнёт читаться полноценно, как только сессия починится.
    """
    from app.parsers.tg import tg_collector

    detected = detect_source(raw)
    if detected is None:
        return "", "", "", "не похоже на канал или ссылку"

    kind, url = detected
    if kind == "tg":
        if not tg_collector.available:
            if url.startswith("http") or url.startswith("-100"):
                return "", "", "", ("закрытый канал читается только через Telethon — "
                                    "сейчас он не поднят")
            return "tg", url, "", ""
        title, error = await tg_collector.resolve_title(url)
        if error:
            return "", "", "", error
        # Пригласительную ссылку заменяем на числовой id: ссылку могут отозвать.
        if url.startswith("http"):
            url = await tg_collector.canonical_id(url)
        return "tg", url, title, error
    return kind, url, "", ""


def split_list(raw: str) -> list[str]:
    """Разбирает «слово1, слово2; слово3» и построчный ввод."""
    if raw.strip() in ("-", "—", ""):
        return []
    parts = re.split(r"[,;\n]", raw)
    return [p.strip() for p in parts if p.strip()]
