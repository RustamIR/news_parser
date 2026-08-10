"""Слой доступа к данным. Все SQL-запросы живут здесь."""
from __future__ import annotations

import json
from typing import Any, Iterable

import aiosqlite

from app.db.database import db


def _row(r: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def _rows(rs: Iterable[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rs]


# --------------------------------------------------------------------------- #
# Рубрики
# --------------------------------------------------------------------------- #
async def list_categories(only_enabled: bool = False) -> list[dict]:
    sql = "SELECT * FROM categories"
    if only_enabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id"
    async with db.conn.execute(sql) as cur:
        return _rows(await cur.fetchall())


async def get_category(category_id: int) -> dict | None:
    async with db.conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)) as cur:
        return _row(await cur.fetchone())


async def create_category(key: str, title: str, emoji: str = "🗂", hint: str = "") -> int:
    cur = await db.conn.execute(
        """INSERT INTO categories (key, title, emoji, prompt_hint)
           VALUES (?, ?, ?, ?)""",
        (key, title, emoji, hint),
    )
    await db.conn.commit()
    return cur.lastrowid


async def find_category(query: str) -> dict | None:
    """Поиск рубрики по ключу, названию или его началу — для форм и команд."""
    needle = query.strip().lower().replace("ё", "е")
    if not needle:
        return None
    categories = await list_categories()
    for c in categories:                                   # точное совпадение
        if needle in (c["key"].lower(), c["title"].lower(), c["emoji"]):
            return c
    for c in categories:                                   # начало названия
        if c["title"].lower().startswith(needle) or c["key"].lower().startswith(needle):
            return c
    for c in categories:                                   # аббревиатура: ИБ, ФиР
        initials = "".join(w[0] for w in c["title"].lower().split() if len(w) > 2)
        if needle == initials:
            return c
    for c in categories:                                   # вхождение в любую сторону
        title = c["title"].lower()
        if needle in title or any(w.startswith(needle) for w in title.split()):
            return c
    return None


async def set_category_target(category_id: int, chat_id: int, title: str) -> None:
    await db.conn.execute(
        "UPDATE categories SET target_chat_id = ?, target_title = ? WHERE id = ?",
        (chat_id, title, category_id),
    )
    await db.conn.commit()


async def update_category(category_id: int, **fields) -> None:
    allowed = {"title", "emoji", "prompt_hint", "poll_interval_min",
               "min_relevance", "autosend", "enabled"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    # Имена колонок берутся только из allowed выше, значения — параметрами.
    sets = ", ".join(f"{k} = ?" for k in data)
    await db.conn.execute(
        f"UPDATE categories SET {sets} WHERE id = ?",  # nosec B608
        (*data.values(), category_id),
    )
    await db.conn.commit()


# --------------------------------------------------------------------------- #
# Источники
# --------------------------------------------------------------------------- #
async def list_sources(category_id: int | None = None, only_enabled: bool = False) -> list[dict]:
    sql, args = "SELECT * FROM sources", []
    where = []
    if category_id is not None:
        where.append("category_id = ?")
        args.append(category_id)
    if only_enabled:
        where.append("enabled = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY kind, id"
    async with db.conn.execute(sql, args) as cur:
        return _rows(await cur.fetchall())


async def get_source(source_id: int) -> dict | None:
    async with db.conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)) as cur:
        return _row(await cur.fetchone())


async def add_source(category_id: int, kind: str, url: str, title: str = "") -> int | None:
    """Возвращает id источника или None, если такой уже есть в рубрике."""
    try:
        cur = await db.conn.execute(
            "INSERT INTO sources (category_id, kind, url, title) VALUES (?, ?, ?, ?)",
            (category_id, kind, url, title),
        )
        await db.conn.commit()
        return cur.lastrowid
    except aiosqlite.IntegrityError:
        return None


async def delete_source(source_id: int) -> None:
    await db.conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    await db.conn.commit()


async def toggle_source(source_id: int) -> None:
    await db.conn.execute(
        "UPDATE sources SET enabled = 1 - enabled WHERE id = ?", (source_id,)
    )
    await db.conn.commit()


async def mark_source_checked(source_id: int, last_external_id: str = "",
                              error: str = "") -> None:
    if last_external_id:
        await db.conn.execute(
            """UPDATE sources
               SET last_checked_at = datetime('now'), last_external_id = ?, last_error = ?
               WHERE id = ?""",
            (last_external_id, error, source_id),
        )
    else:
        await db.conn.execute(
            """UPDATE sources SET last_checked_at = datetime('now'), last_error = ?
               WHERE id = ?""",
            (error, source_id),
        )
    await db.conn.commit()


# --------------------------------------------------------------------------- #
# Темы
# --------------------------------------------------------------------------- #
async def list_topics(category_id: int | None = None, only_enabled: bool = False) -> list[dict]:
    sql, args, where = "SELECT * FROM topics", [], []
    if category_id is not None:
        where.append("category_id = ?")
        args.append(category_id)
    if only_enabled:
        where.append("enabled = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    async with db.conn.execute(sql, args) as cur:
        rows = _rows(await cur.fetchall())
    for row in rows:
        row["keywords"] = json.loads(row["keywords"] or "[]")
        row["stopwords"] = json.loads(row["stopwords"] or "[]")
    return rows


async def get_topic(topic_id: int) -> dict | None:
    async with db.conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)) as cur:
        row = _row(await cur.fetchone())
    if row:
        row["keywords"] = json.loads(row["keywords"] or "[]")
        row["stopwords"] = json.loads(row["stopwords"] or "[]")
    return row


async def add_topic(category_id: int, title: str, description: str,
                    keywords: list[str], stopwords: list[str] | None = None) -> int | None:
    try:
        cur = await db.conn.execute(
            """INSERT INTO topics (category_id, title, description, keywords, stopwords)
               VALUES (?, ?, ?, ?, ?)""",
            (category_id, title, description,
             json.dumps(keywords, ensure_ascii=False),
             json.dumps(stopwords or [], ensure_ascii=False)),
        )
        await db.conn.commit()
        return cur.lastrowid
    except aiosqlite.IntegrityError:
        return None


async def update_topic(topic_id: int, description: str | None = None,
                       keywords: list[str] | None = None,
                       stopwords: list[str] | None = None) -> None:
    data: dict[str, Any] = {}
    if description is not None:
        data["description"] = description
    if keywords is not None:
        data["keywords"] = json.dumps(keywords, ensure_ascii=False)
    if stopwords is not None:
        data["stopwords"] = json.dumps(stopwords, ensure_ascii=False)
    if not data:
        return
    # Имена колонок заданы литералами выше, значения — параметрами.
    sets = ", ".join(f"{k} = ?" for k in data)
    await db.conn.execute(
        f"UPDATE topics SET {sets} WHERE id = ?",  # nosec B608
        (*data.values(), topic_id),
    )
    await db.conn.commit()


async def delete_topic(topic_id: int) -> None:
    await db.conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
    await db.conn.commit()


async def toggle_topic(topic_id: int) -> None:
    await db.conn.execute("UPDATE topics SET enabled = 1 - enabled WHERE id = ?", (topic_id,))
    await db.conn.commit()


# --------------------------------------------------------------------------- #
# Новости
# --------------------------------------------------------------------------- #
async def item_exists(hash_: str) -> bool:
    async with db.conn.execute("SELECT 1 FROM items WHERE hash = ?", (hash_,)) as cur:
        return await cur.fetchone() is not None


async def save_item(source_id: int, category_id: int, external_id: str, url: str,
                    title: str, content: str, published_at: str | None,
                    hash_: str, status: str) -> int | None:
    try:
        cur = await db.conn.execute(
            """INSERT INTO items (source_id, category_id, external_id, url, title,
                                  content, published_at, hash, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_id, category_id, external_id, url, title, content,
             published_at, hash_, status),
        )
        await db.conn.commit()
        return cur.lastrowid
    except aiosqlite.IntegrityError:      # гонка: элемент уже сохранён
        return None


async def set_item_status(item_id: int, status: str) -> None:
    await db.conn.execute("UPDATE items SET status = ? WHERE id = ?", (status, item_id))
    await db.conn.commit()


async def save_analysis(item_id: int, topic_id: int | None, topic_name: str,
                        relevance: int, summary: str, tags: list[str], engine: str,
                        impact: str = "") -> None:
    await db.conn.execute(
        """INSERT INTO analyses (item_id, topic_id, topic_name, relevance,
                                 summary, impact, tags, engine)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(item_id) DO UPDATE SET
               topic_id = excluded.topic_id, topic_name = excluded.topic_name,
               relevance = excluded.relevance, summary = excluded.summary,
               impact = excluded.impact,
               tags = excluded.tags, engine = excluded.engine""",
        (item_id, topic_id, topic_name, relevance, summary, impact,
         json.dumps(tags, ensure_ascii=False), engine),
    )
    await db.conn.commit()


# --------------------------------------------------------------------------- #
# Обратная связь: на ней учится промпт
# --------------------------------------------------------------------------- #
async def save_feedback(item_id: int, chat_id: int, verdict: int) -> bool:
    """False — карточки уже нет: кнопку нажали на удалённой или поддельной."""
    try:
        await db.conn.execute(
            """INSERT INTO feedback (item_id, chat_id, verdict) VALUES (?, ?, ?)
               ON CONFLICT(item_id, chat_id) DO UPDATE SET
                   verdict = excluded.verdict, created_at = datetime('now')""",
            (item_id, chat_id, verdict),
        )
    except aiosqlite.IntegrityError:
        return False
    await db.conn.commit()
    return True


async def learning_examples(category_id: int, limit: int = 8) -> list[dict]:
    """Свежие оценённые посты рубрики — идут в промпт как примеры.

    Берём поровну одобренных и отклонённых: односторонняя выборка смещает
    модель, а не учит её различать.
    """
    rows: list[dict] = []
    for verdict in (1, -1):
        async with db.conn.execute(
            """SELECT i.title, f.verdict
               FROM feedback f JOIN items i ON i.id = f.item_id
               WHERE i.category_id = ? AND f.verdict = ?
               ORDER BY f.created_at DESC LIMIT ?""",
            (category_id, verdict, max(1, limit // 2)),
        ) as cur:
            rows.extend(_rows(await cur.fetchall()))
    return rows


async def add_rater(user_id: int, category_id: int, title: str = "") -> None:
    await db.conn.execute(
        """INSERT INTO raters (user_id, category_id, title) VALUES (?, ?, ?)
           ON CONFLICT(user_id, category_id) DO UPDATE SET title = excluded.title""",
        (user_id, category_id, title),
    )
    await db.conn.commit()


async def remove_rater(user_id: int, category_id: int) -> bool:
    cur = await db.conn.execute(
        "DELETE FROM raters WHERE user_id = ? AND category_id = ?",
        (user_id, category_id),
    )
    await db.conn.commit()
    return cur.rowcount > 0


async def list_raters(category_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM raters"
    args: list[Any] = []
    if category_id is not None:
        # 0 — доступ ко всем рубрикам, он действует и здесь
        sql += " WHERE category_id IN (0, ?)"
        args.append(category_id)
    sql += " ORDER BY category_id, user_id"
    async with db.conn.execute(sql, args) as cur:
        return _rows(await cur.fetchall())


async def is_rater(user_id: int, category_id: int | None = None) -> bool:
    sql = "SELECT 1 FROM raters WHERE user_id = ?"
    args: list[Any] = [user_id]
    if category_id is not None:
        sql += " AND category_id IN (0, ?)"
        args.append(category_id)
    async with db.conn.execute(sql, args) as cur:
        return await cur.fetchone() is not None


async def item_category(item_id: int) -> int | None:
    async with db.conn.execute(
        "SELECT category_id FROM items WHERE id = ?", (item_id,)
    ) as cur:
        row = await cur.fetchone()
    return row["category_id"] if row else None


async def feedback_stats(category_id: int) -> dict:
    async with db.conn.execute(
        """SELECT
             COUNT(*)                    AS total,
             SUM(f.verdict > 0)          AS liked,
             SUM(f.verdict < 0)          AS disliked,
             AVG(CASE WHEN f.verdict < 0 THEN a.relevance END) AS avg_bad,
             AVG(CASE WHEN f.verdict > 0 THEN a.relevance END) AS avg_good
           FROM feedback f
           JOIN items i ON i.id = f.item_id
           LEFT JOIN analyses a ON a.item_id = i.id
           WHERE i.category_id = ?""",
        (category_id,),
    ) as cur:
        row = await cur.fetchone()
    return {k: row[k] for k in row.keys()}


async def disliked_tags(category_id: int, limit: int = 10) -> list[tuple[str, int]]:
    """Теги, чаще всего встречающиеся у отклонённых постов — кандидаты в стоп-слова."""
    async with db.conn.execute(
        """SELECT a.tags FROM feedback f
           JOIN items i ON i.id = f.item_id
           JOIN analyses a ON a.item_id = i.id
           WHERE i.category_id = ? AND f.verdict < 0""",
        (category_id,),
    ) as cur:
        rows = await cur.fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        for tag in json.loads(row["tags"] or "[]"):
            key = str(tag).strip().lower()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]


async def digest(category_id: int, limit: int = 10, since_hours: int | None = None,
                 only_unsent: bool = False) -> list[dict]:
    sql = """
        SELECT i.*, a.summary, a.impact, a.relevance, a.topic_name, a.tags, a.engine,
               s.title AS source_title, s.url AS source_url, s.kind AS source_kind
        FROM items i
        JOIN analyses a ON a.item_id = i.id
        JOIN sources  s ON s.id = i.source_id
        WHERE i.category_id = ? AND i.status IN ('relevant', 'sent')
    """
    args: list[Any] = [category_id]
    if only_unsent:
        sql += " AND i.status = 'relevant'"
    if since_hours:
        sql += " AND i.created_at >= datetime('now', ?)"
        args.append(f"-{since_hours} hours")
    sql += " ORDER BY a.relevance DESC, i.created_at DESC LIMIT ?"
    args.append(limit)
    async with db.conn.execute(sql, args) as cur:
        rows = _rows(await cur.fetchall())
    for row in rows:
        row["tags"] = json.loads(row["tags"] or "[]")
    return rows


async def stats(category_id: int) -> dict:
    async with db.conn.execute(
        """SELECT
             COUNT(*)                                        AS total,
             SUM(status = 'skipped')                         AS skipped,
             SUM(status = 'rejected')                        AS rejected,
             SUM(status IN ('relevant', 'sent'))             AS relevant,
             SUM(created_at >= datetime('now', '-24 hours')) AS last_24h
           FROM items WHERE category_id = ?""",
        (category_id,),
    ) as cur:
        row = await cur.fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


# --------------------------------------------------------------------------- #
# Подписки на автоотправку
# --------------------------------------------------------------------------- #
async def subscribe(chat_id: int, category_id: int) -> None:
    await db.conn.execute(
        "INSERT OR IGNORE INTO subscriptions (chat_id, category_id) VALUES (?, ?)",
        (chat_id, category_id),
    )
    await db.conn.commit()


async def unsubscribe(chat_id: int, category_id: int) -> None:
    await db.conn.execute(
        "DELETE FROM subscriptions WHERE chat_id = ? AND category_id = ?",
        (chat_id, category_id),
    )
    await db.conn.commit()


async def is_subscribed(chat_id: int, category_id: int) -> bool:
    async with db.conn.execute(
        "SELECT 1 FROM subscriptions WHERE chat_id = ? AND category_id = ?",
        (chat_id, category_id),
    ) as cur:
        return await cur.fetchone() is not None


async def subscribers(category_id: int) -> list[int]:
    async with db.conn.execute(
        "SELECT chat_id FROM subscriptions WHERE category_id = ?", (category_id,)
    ) as cur:
        return [r["chat_id"] for r in await cur.fetchall()]


async def destinations(category: dict) -> list[int]:
    """Куда публиковать: канал рубрики плюс личные подписки, без дублей."""
    targets: list[int] = []
    if category["target_chat_id"]:
        targets.append(category["target_chat_id"])
    for chat_id in await subscribers(category["id"]):
        if chat_id not in targets:
            targets.append(chat_id)
    return targets


# --------------------------------------------------------------------------- #
# Общие настройки (управляющий чат сообщества и прочее)
# --------------------------------------------------------------------------- #
async def get_setting(key: str, default: str = "") -> str:
    async with db.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    await db.conn.execute(
        """INSERT INTO settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (key, value),
    )
    await db.conn.commit()


async def control_chat_id() -> int:
    return int(await get_setting("control_chat_id", "0") or 0)
