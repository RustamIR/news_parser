"""Периодический опрос источников — по одной задаче на рубрику."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import repo
from app.pipeline import RunReport, process_category

log = logging.getLogger(__name__)


class ParserScheduler:
    def __init__(self, notify=None) -> None:
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.notify = notify
        self._locks: dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        await self.reload()
        self.scheduler.start()
        log.info("Планировщик запущен: %d задач", len(self.scheduler.get_jobs()))

    async def reload(self) -> None:
        """Пересобирает расписание из БД (после смены интервала или рубрик)."""
        self.scheduler.remove_all_jobs()
        for category in await repo.list_categories(only_enabled=True):
            self.scheduler.add_job(
                self.run_category,
                trigger="interval",
                minutes=max(1, category["poll_interval_min"]),
                args=[category["id"]],
                id=f"category:{category['id']}",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                # Первый прогон не сразу на старте — даём боту подняться.
                # Время обязательно с таймзоной: наивное APScheduler трактует
                # как UTC, и первый запуск уезжает на разницу часовых поясов.
                next_run_time=datetime.now(timezone.utc) + timedelta(seconds=20),
            )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    # ------------------------------------------------------------------ #
    async def run_category(self, category_id: int) -> RunReport | None:
        """Один проход. Безопасен для ручного вызова из бота параллельно с расписанием."""
        lock = self._locks.setdefault(category_id, asyncio.Lock())
        if lock.locked():
            log.info("Рубрика %s уже обрабатывается — пропускаем", category_id)
            return None

        async with lock:
            category = await repo.get_category(category_id)
            if not category:
                return None
            # Мгновенная рассылка — только если она включена в настройках рубрики.
            notify = self.notify if category["autosend"] else None
            try:
                report = await process_category(category, notify=notify)
            except Exception:
                log.exception("Ошибка обработки рубрики %s", category["title"])
                return None
            log.info(
                "%s: получено %d, в дайджест %d",
                category["title"], report.fetched, report.relevant,
            )
            return report
