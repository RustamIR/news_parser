"""Парсер веб-ресурсов: RSS/Atom-ленты и обычные HTML-страницы.

Для kind='rss' читаем ленту напрямую. Для kind='web' сначала пробуем найти
ленту в <link rel="alternate">; если её нет — собираем ссылки на статьи
эвристикой и вытягиваем текст с самих страниц.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import aiohttp
import certifi
import feedparser
from bs4 import BeautifulSoup

from app.parsers.base import RawItem

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NewsParserBot/1.0; +https://t.me/)",
    "Accept-Language": "ru,en;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=25)
MAX_ITEMS_PER_POLL = 20
MAX_ARTICLE_CHARS = 6000
MAX_REDIRECTS = 5


async def _assert_public(url: str) -> None:
    """Пускает только на публичные адреса.

    Защита не абсолютная: между проверкой и запросом DNS может ответить иначе
    (DNS rebinding). Для бота, которому источники задаёт доверенный админ,
    этого достаточно — цель в том, чтобы опечатка или чужая ссылка не увели
    его в локальную сеть.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"схема {parsed.scheme!r} не поддерживается")
    host = parsed.hostname
    if not host:
        raise RuntimeError("в ссылке нет хоста")

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, parsed.port or 0)
    except socket.gaierror as e:
        raise RuntimeError(f"хост {host} не резолвится") from e

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise RuntimeError(
                f"адрес {ip} во внутренней сети — такие источники запрещены"
            )


# Мусорные блоки, которые не должны попадать в текст статьи
JUNK_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form", "noscript")


class WebCollector:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            # Свой CA-бандл: системный питон на macOS часто идёт без корневых
            # сертификатов, и без этого любой https-источник падает на проверке.
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            self._session = aiohttp.ClientSession(
                headers=HEADERS,
                timeout=TIMEOUT,
                connector=aiohttp.TCPConnector(ssl=ssl_context, limit_per_host=4),
            )

    async def stop(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _get(self, url: str) -> str:
        """Скачивает страницу, не пуская бота во внутреннюю сеть.

        Источники задаёт человек, а бот ходит по ним с машины, где крутится и
        Ollama, и сама база. Без проверки источником можно было бы сделать
        http://127.0.0.1:11434 или адрес метаданных облака и вытащить ответ
        через дайджест. Проверяем каждый переход: редирект уводит куда угодно.
        """
        await self.start()
        for _ in range(MAX_REDIRECTS):
            await _assert_public(url)
            async with self._session.get(url, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise RuntimeError("редирект без адреса")
                    url = urljoin(url, location)
                    continue
                resp.raise_for_status()
                return await resp.text(errors="ignore")
        raise RuntimeError("слишком много редиректов")

    # ------------------------------------------------------------------ #
    async def fetch(self, source: dict) -> list[RawItem]:
        if source["kind"] == "tgweb":
            return await self._fetch_tg_preview(source)
        if source["kind"] == "rss":
            return await self._fetch_feed(source, source["url"])

        html = await self._get(source["url"])
        feed_url = self._discover_feed(html, source["url"])
        if feed_url:
            log.info("Для %s найдена лента %s", source["url"], feed_url)
            return await self._fetch_feed(source, feed_url)
        return await self._fetch_html(source, html)

    async def _fetch_tg_preview(self, source: dict) -> list[RawItem]:
        """Публичная витрина канала t.me/s/<name> — чтение без api_id и аккаунта.

        Отдаёт последние ~20 постов открытых каналов. Работает только там, где
        владелец не отключил предпросмотр в настройках канала.
        """
        name = source["url"].lstrip("@")
        html = await self._get(f"https://t.me/s/{name}")
        soup = BeautifulSoup(html, "lxml")

        messages = soup.select("div.tgme_widget_message")
        if not messages:
            raise RuntimeError(
                "предпросмотр канала закрыт или канал приватный — "
                "такой источник читается только через Telethon"
            )

        items: list[RawItem] = []
        for msg in messages:
            block = msg.select_one("div.tgme_widget_message_text")
            text = block.get_text("\n", strip=True) if block else ""
            if not text:
                continue                       # медиа без подписи
            post = msg.get("data-post", "")
            date_link = msg.select_one("a.tgme_widget_message_date")
            time_tag = msg.select_one("a.tgme_widget_message_date time")
            items.append(
                RawItem(
                    source_id=source["id"],
                    category_id=source["category_id"],
                    external_id=post.rsplit("/", 1)[-1],
                    url=(date_link.get("href") if date_link else "")
                        or f"https://t.me/{post}",
                    title=text.split("\n", 1)[0][:200],
                    content=text[:MAX_ARTICLE_CHARS],
                    published_at=self._iso_date(time_tag),
                    meta={"channel": name},
                )
            )
        return items

    # ------------------------------------------------------------------ #
    async def _fetch_feed(self, source: dict, feed_url: str) -> list[RawItem]:
        raw = await self._get(feed_url)
        # feedparser синхронный и на больших лентах заметно грузит цикл событий
        feed = await asyncio.to_thread(feedparser.parse, raw)
        if feed.bozo and not feed.entries:
            raise RuntimeError(f"не удалось разобрать ленту: {feed.bozo_exception}")

        items: list[RawItem] = []
        for entry in feed.entries[:MAX_ITEMS_PER_POLL]:
            link = entry.get("link", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            content_parts = entry.get("content") or []
            if content_parts:
                summary = content_parts[0].get("value", summary)
            items.append(
                RawItem(
                    source_id=source["id"],
                    category_id=source["category_id"],
                    external_id=entry.get("id", "") or link,
                    url=link,
                    title=self._clean(entry.get("title", ""))[:300],
                    content=self._clean(summary)[:MAX_ARTICLE_CHARS],
                    published_at=self._entry_date(entry),
                    meta={"feed": feed.feed.get("title", "")},
                )
            )
        return items

    async def _fetch_html(self, source: dict, html: str) -> list[RawItem]:
        links = self._article_links(html, source["url"])[:MAX_ITEMS_PER_POLL]
        items: list[RawItem] = []
        for url, anchor_text in links:
            try:
                page = await self._get(url)
            except Exception as e:                     # одна битая статья не должна ронять опрос
                log.debug("Не удалось скачать %s: %s", url, e)
                continue
            title, body = self._extract_article(page)
            items.append(
                RawItem(
                    source_id=source["id"],
                    category_id=source["category_id"],
                    external_id=url,
                    url=url,
                    title=(title or anchor_text)[:300],
                    content=body[:MAX_ARTICLE_CHARS],
                    published_at=None,
                    meta={"site": urlparse(url).netloc},
                )
            )
        return items

    # ------------------------------------------------------------------ #
    @staticmethod
    def _discover_feed(html: str, base_url: str) -> str | None:
        soup = BeautifulSoup(html, "lxml")
        for link in soup.find_all("link", rel=lambda v: v and "alternate" in v):
            if (link.get("type") or "") in ("application/rss+xml", "application/atom+xml"):
                href = link.get("href")
                if href:
                    return urljoin(base_url, href)
        return None

    @staticmethod
    def _article_links(html: str, base_url: str) -> list[tuple[str, str]]:
        """Эвристика: ссылки того же домена с осмысленным текстом якоря."""
        soup = BeautifulSoup(html, "lxml")
        host = urlparse(base_url).netloc
        seen: set[str] = set()
        result: list[tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            url = urljoin(base_url, a["href"]).split("#")[0]
            parsed = urlparse(url)
            if parsed.netloc != host or parsed.scheme not in ("http", "https"):
                continue
            if url in seen or url.rstrip("/") == base_url.rstrip("/"):
                continue
            text = " ".join(a.get_text(" ", strip=True).split())
            # заголовок новости — это как минимум несколько слов
            if len(text) < 25 or len(text.split()) < 4:
                continue
            seen.add(url)
            result.append((url, text))
        return result

    @classmethod
    def _extract_article(cls, html: str) -> tuple[str, str]:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(list(JUNK_TAGS)):
            tag.decompose()
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        if h1 := soup.find("h1"):
            title = h1.get_text(" ", strip=True) or title

        container = soup.find("article") or soup.find("main") or soup.body or soup
        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        body = "\n".join(p for p in paragraphs if len(p) > 40)
        return title, body

    @staticmethod
    def _clean(html_fragment: str) -> str:
        if not html_fragment:
            return ""
        return BeautifulSoup(html_fragment, "lxml").get_text(" ", strip=True)

    @staticmethod
    def _iso_date(tag) -> datetime | None:
        if tag is None or not tag.get("datetime"):
            return None
        try:
            return datetime.fromisoformat(tag["datetime"])
        except ValueError:
            return None

    @staticmethod
    def _entry_date(entry) -> datetime | None:
        for key in ("published_parsed", "updated_parsed"):
            if struct := entry.get(key):
                return datetime(*struct[:6], tzinfo=timezone.utc)
        return None


web_collector = WebCollector()
