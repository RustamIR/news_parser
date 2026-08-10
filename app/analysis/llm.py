"""Выбор движка анализа.

Остальной код работает с одним объектом `analyzer` и не знает, что под ним —
локальная модель или Claude. Если движок не поднялся, анализ просто отключается,
и конвейер откатывается на оценку по ключевым словам.
"""
from __future__ import annotations

import logging

from app.analysis.base import Analysis
from app.analysis.claude import claude_analyzer
from app.analysis.ollama import ollama_analyzer
from app.config import config

log = logging.getLogger(__name__)

__all__ = ["Analysis", "analyzer"]

BACKENDS = {"ollama": ollama_analyzer, "claude": claude_analyzer}


class Analyzer:
    """Фасад над движками анализа."""

    def __init__(self) -> None:
        self._backend = None

    @property
    def available(self) -> bool:
        return self._backend is not None and self._backend.available

    @property
    def title(self) -> str:
        return self._backend.title if self.available else "ключевые слова"

    async def start(self) -> None:
        choice = (config.llm_backend or "auto").lower()

        if choice == "none":
            log.info("Анализ моделью отключён настройкой LLM_BACKEND=none")
            return

        # auto: сначала локальная модель — она бесплатна, потом облако.
        order = ["ollama", "claude"] if choice == "auto" else [choice]
        for name in order:
            backend = BACKENDS.get(name)
            if backend is None:
                log.error("Неизвестный LLM_BACKEND=%s", name)
                return
            if await backend.start():
                self._backend = backend
                return

        log.warning(
            "Ни один движок анализа не поднялся — фильтрация только "
            "по ключевым словам"
        )

    async def stop(self) -> None:
        for backend in BACKENDS.values():
            await backend.stop()
        self._backend = None

    async def analyze(self, category: dict, topics: list[dict], title: str, body: str,
                      examples: list[dict] | None = None) -> Analysis | None:
        """None — движок недоступен или запрос не удался; вызывающий откатится
        на keyword-режим."""
        if self._backend is None:
            return None
        return await self._backend.analyze(category, topics, title, body, examples)


analyzer = Analyzer()
