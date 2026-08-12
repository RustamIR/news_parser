"""Конвейер обработки: сбор → предфильтр → анализ → сохранение → рассылка.

Ключевая идея выборочности: между сбором и дорогим анализом стоит предфильтр по
темам рубрики. Пост, не задевший ни одну тему, помечается `skipped` и дальше не
идёт — модель его не видит.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.analysis.llm import Analysis, analyzer
from app.analysis.prefilter import Prefilter, extractive_summary
from app.db import repo
from app.parsers.base import RawItem
from app.parsers.tg import tg_collector
from app.parsers.web import web_collector

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    """Итоги одного прохода по рубрике."""
    category: str
    fetched: int = 0
    duplicates: int = 0
    skipped: int = 0          # не прошли предфильтр
    rejected: int = 0         # модель сочла нерелевантным
    relevant: int = 0
    resent: int = 0           # досланы из прошлых проходов
    errors: list[str] = None

    def __post_init__(self) -> None:
        self.errors = self.errors or []

    def as_text(self) -> str:
        lines = [
            f"<b>{self.category}</b>",
            f"получено: {self.fetched} · дубли: {self.duplicates}",
            f"мимо тем: {self.skipped} · отсеяно анализом: {self.rejected}",
            f"<b>в дайджест: {self.relevant}</b>",
        ]
        if self.resent:
            lines.append(f"дослано из прошлых проходов: {self.resent}")
        if self.errors:
            lines.append("\n⚠️ " + "\n⚠️ ".join(self.errors[:5]))
        return "\n".join(lines)


async def collect_source(source: dict) -> list[RawItem]:
    """Сбор сырых новостей из одного источника.

    Телеграм-канал читается лучшим доступным способом: через Telethon, если он
    поднят, иначе через публичную витрину t.me/s/. Так источник не ломается,
    когда сессия протухла, и сам начинает читаться полноценно, когда её починят.
    """
    if source["kind"] == "tg" and await tg_collector.ensure_connected():
        items, last_id = await tg_collector.fetch(source)
        await repo.mark_source_checked(source["id"], last_id)
        return items

    if source["kind"] == "tg":
        # У закрытого канала витрины нет — подменять транспорт бессмысленно.
        if source["url"].lstrip("-").isdigit():
            raise RuntimeError(
                "закрытый канал читается только через Telethon, "
                "а соединение сейчас недоступно"
            )
        items = await web_collector.fetch({**source, "kind": "tgweb"})
    else:
        items = await web_collector.fetch(source)
    await repo.mark_source_checked(source["id"])
    return items


async def process_category(category: dict, notify=None) -> RunReport:
    """Один полный проход по рубрике.

    notify — корутина notify(chat_id, item_dict) для мгновенной отправки
    релевантных новостей подписчикам. Если None, новости просто копятся в БД.
    """
    report = RunReport(category=f"{category['emoji']} {category['title']}")
    sources = await repo.list_sources(category["id"], only_enabled=True)
    topics = await repo.list_topics(category["id"], only_enabled=True)

    if not sources:
        report.errors.append("нет активных источников")
        return report
    if not topics:
        report.errors.append("не заданы темы — парсинг остановлен")
        return report

    prefilter = Prefilter(topics)
    min_relevance = category["min_relevance"]
    # Оценки пользователя идут в промпт как примеры — читаем один раз на проход.
    examples = await repo.learning_examples(category["id"])
    # Канал рубрики в сообществе плюс те, кто подписался в личке.
    targets = await repo.destinations(category) if notify else []

    # Досылаем то, что прошло фильтр раньше, но не было отправлено: сбой сети,
    # перезапуск на середине прохода или разбор без включённой рассылки.
    # Иначе такие новости остаются в базе навсегда и до чата не доходят.
    if notify and targets:
        report.resent = await _deliver_pending(notify, targets, category)

    for source in sources:
        try:
            items = await collect_source(source)
        except Exception as e:
            msg = f"{source['url']}: {e}"
            log.warning("Источник недоступен — %s", msg)
            await repo.mark_source_checked(source["id"], error=str(e)[:300])
            report.errors.append(msg[:200])
            continue

        report.fetched += len(items)
        for item in items:
            outcome = await _process_item(
                item, category, prefilter, min_relevance, examples)
            if outcome is None:
                report.duplicates += 1
                continue
            status, item_id, analysis = outcome
            if status == "skipped":
                report.skipped += 1
            elif status == "rejected":
                report.rejected += 1
            else:
                report.relevant += 1
                if notify and targets:
                    await _broadcast(notify, targets, item_id, item, analysis, category)

    return report


async def _process_item(item: RawItem, category: dict,
                        prefilter: Prefilter, min_relevance: int,
                        examples: list[dict] | None = None):
    """Возвращает (status, item_id, analysis) либо None для дубликата."""
    if await repo.item_exists(item.hash):
        return None

    matches = prefilter.match(item.title, item.content)
    if not matches:
        # Храним «пустышку» — чтобы при следующем опросе не разбирать пост заново.
        await repo.save_item(
            item.source_id, item.category_id, item.external_id, item.url,
            item.title[:300], "", item.published_iso, item.hash, "skipped",
        )
        return "skipped", None, None

    candidate_topics = [m.topic for m in matches[:5]]
    analysis = await analyzer.analyze(
        category, candidate_topics, item.title, item.content, examples)
    if analysis is None:                       # модель недоступна — работаем по словам
        best = matches[0]
        analysis = Analysis(
            relevant=best.score >= min_relevance,
            topic=best.topic["title"],
            relevance=best.score,
            summary=extractive_summary(item.content),
            tags=best.matched[:4],
            engine="keywords",
        )

    passed = analysis.relevant and analysis.relevance >= min_relevance
    status = "relevant" if passed else "rejected"
    item_id = await repo.save_item(
        item.source_id, item.category_id, item.external_id, item.url,
        item.title[:300], item.content if passed else "",
        item.published_iso, item.hash, status,
    )
    if item_id is None:                        # параллельный проход успел сохранить
        return None

    topic_id = next(
        (t["id"] for t in candidate_topics if t["title"] == analysis.topic), None
    )
    await repo.save_analysis(
        item_id, topic_id, analysis.topic or (candidate_topics[0]["title"]),
        analysis.relevance, analysis.summary, analysis.tags, analysis.engine,
        impact=analysis.impact,
    )
    return status, item_id, analysis


# За один проход досылаем не больше этого — чтобы после долгого простоя
# в чат не вывалилась вся накопленная очередь разом.
MAX_RESEND_PER_RUN = 10


async def _deliver_pending(notify, targets: list[int], category: dict) -> int:
    """Отправляет новости, прошедшие фильтр, но так и не доставленные."""
    pending = await repo.digest(category["id"], limit=MAX_RESEND_PER_RUN,
                                only_unsent=True)
    sent = 0
    for row in pending:
        payload = {
            "item_id": row["id"],
            "title": row["title"],
            "url": row["url"],
            "summary": row["summary"],
            "impact": row["impact"],
            "relevance": row["relevance"],
            "topic_name": row["topic_name"],
            "tags": row["tags"],
            "engine": row["engine"],
            "category": f"{category['emoji']} {category['title']}",
        }
        delivered = False
        for chat_id in targets:
            try:
                await notify(chat_id, payload)
                delivered = True
            except Exception as e:
                log.warning("Не удалось дослать новость %s в чат %s: %s",
                            row["id"], chat_id, e)
            await asyncio.sleep(0.4)
        if delivered:
            await repo.set_item_status(row["id"], "sent")
            sent += 1
    if sent:
        log.info("%s: дослано %d новостей из прошлых проходов",
                 category["title"], sent)
    return sent


async def _broadcast(notify, chat_ids: list[int], item_id: int, item: RawItem,
                     analysis: Analysis, category: dict) -> None:
    payload = {
        "item_id": item_id,
        "title": item.title,
        "url": item.url,
        "summary": analysis.summary,
        "impact": analysis.impact,
        "relevance": analysis.relevance,
        "topic_name": analysis.topic,
        "tags": analysis.tags,
        "engine": analysis.engine,
        "category": f"{category['emoji']} {category['title']}",
    }
    delivered = False
    for chat_id in chat_ids:
        try:
            await notify(chat_id, payload)
            delivered = True
        except Exception as e:
            log.warning("Не удалось отправить новость в чат %s: %s", chat_id, e)
        # В канал Telegram пускает около 20 сообщений в минуту — не частим.
        await asyncio.sleep(0.4)
    if delivered:
        await repo.set_item_status(item_id, "sent")
