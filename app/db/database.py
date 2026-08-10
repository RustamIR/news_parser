"""Подключение к SQLite и схема БД."""
from __future__ import annotations

import os
from contextlib import suppress

import aiosqlite

from app.config import config

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS categories (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    key               TEXT UNIQUE NOT NULL,
    title             TEXT NOT NULL,
    emoji             TEXT DEFAULT '',
    prompt_hint       TEXT DEFAULT '',
    poll_interval_min INTEGER NOT NULL DEFAULT 30,
    min_relevance     INTEGER NOT NULL DEFAULT 60,
    autosend          INTEGER NOT NULL DEFAULT 1,
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id     INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,              -- tg | rss | web
    url             TEXT NOT NULL,              -- @channel или http(s)://...
    title           TEXT DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_external_id TEXT DEFAULT '',           -- для tg: id последнего сообщения
    last_checked_at TEXT,
    last_error      TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (category_id, kind, url)
);

CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,                  -- «Утечки данных в РФ»
    description TEXT DEFAULT '',                -- что именно интересует (идёт в промпт)
    keywords    TEXT NOT NULL DEFAULT '[]',     -- JSON-список ключевых слов/фраз
    stopwords   TEXT NOT NULL DEFAULT '[]',     -- JSON-список стоп-слов
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (category_id, title)
);

CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    external_id  TEXT DEFAULT '',
    url          TEXT DEFAULT '',
    title        TEXT DEFAULT '',
    content      TEXT DEFAULT '',
    published_at TEXT,
    hash         TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'new',   -- skipped | rejected | relevant | sent
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_items_cat_status ON items(category_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS analyses (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    topic_id   INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    topic_name TEXT DEFAULT '',
    relevance  INTEGER NOT NULL DEFAULT 0,
    summary    TEXT DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '[]',
    engine     TEXT NOT NULL DEFAULT 'keywords', -- keywords | llm
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    chat_id     INTEGER NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (chat_id, category_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- Оценки пользователя: на них учится промпт и по ним видно, что чинить
CREATE TABLE IF NOT EXISTS feedback (
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    chat_id    INTEGER NOT NULL,
    verdict    INTEGER NOT NULL,          -- +1 нужно, -1 не нужно
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (item_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC);

-- Кому разрешено оценивать новости кнопками. category_id = 0 — все рубрики.
CREATE TABLE IF NOT EXISTS raters (
    user_id     INTEGER NOT NULL,
    category_id INTEGER NOT NULL DEFAULT 0,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, category_id)
);
"""

# Колонки, добавленные после первой версии схемы: накатываются на живую базу.
MIGRATIONS = {
    "categories": (
        ("target_chat_id", "INTEGER NOT NULL DEFAULT 0"),
        ("target_title", "TEXT NOT NULL DEFAULT ''"),
    ),
    "analyses": (
        ("impact", "TEXT NOT NULL DEFAULT ''"),
    ),
}

DEFAULT_CATEGORIES = [
    ("infosec", "Информационная безопасность", "🛡",
     "Уязвимости, эксплойты, утечки данных, APT-группировки, инциденты ИБ, "
     "регуляторика и отраслевые стандарты защиты информации."),
    ("finance", "Финансы и рынки", "📈",
     "Макроэкономика, ставки ЦБ, отчётности компаний, движение рынков, "
     "валюты, сырьё, санкционные и регуляторные события с финансовым эффектом."),
    ("geopolitics", "Геополитика", "🌍",
     "Международные отношения, конфликты, санкции, выборы, договорённости "
     "между государствами, военно-политические события."),
]


class Database:
    def __init__(self, path: str = config.db_path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.path)
            # В базе лежат ID чатов и всё разобранное — читать её посторонним
            # на общей машине незачем.
            with suppress(OSError):
                os.chmod(self.path, 0o600)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA foreign_keys=ON")
            await self._conn.executescript(SCHEMA)
            await self._migrate()
            await self._seed_categories()
            await self._conn.commit()
        return self._conn

    async def _migrate(self) -> None:
        for table, columns in MIGRATIONS.items():
            async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row["name"] for row in await cur.fetchall()}
            for name, ddl in columns:
                if name not in existing:
                    await self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                    )

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() не был вызван")
        return self._conn

    async def _seed_categories(self) -> None:
        for key, title, emoji, hint in DEFAULT_CATEGORIES:
            await self._conn.execute(
                """INSERT INTO categories (key, title, emoji, prompt_hint,
                                           poll_interval_min, min_relevance)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO NOTHING""",
                (key, title, emoji, hint,
                 config.default_poll_interval, config.default_min_relevance),
            )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


db = Database()
