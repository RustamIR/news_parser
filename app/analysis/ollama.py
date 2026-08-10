"""Локальная модель через Ollama — анализ без API-ключей и без оплаты токенов.

Работает на машине, где запущен бот. Медленнее облака (десятки секунд на пост),
поэтому предфильтр по ключевым словам здесь ещё важнее, чем с Claude.
"""
from __future__ import annotations

import json
import logging

import aiohttp

from app.analysis.base import (
    Analysis,
    OUTPUT_SCHEMA,
    build_post,
    build_system,
    parse_payload,
)
from app.config import config

log = logging.getLogger(__name__)

# Локальная генерация на 8B-модели занимает десятки секунд — таймаут щедрый.
TIMEOUT = aiohttp.ClientTimeout(total=240)

# Локально каждый лишний токен промпта — это время. Решение «по теме или нет»
# принимается по началу поста, поэтому режем короче, чем для облака.
MAX_LOCAL_CHARS = 2500
# Окно под промпт (~800 токенов) с запасом; больше — только медленнее.
NUM_CTX = 4096
# Ответ — короткий JSON; ограничение страхует от разгона на кривом посте.
NUM_PREDICT = 400
# Держим модель в памяти между постами одного прохода, но не вечно:
# между опросами (раз в 30 мин) она выгрузится и освободит ~5 ГБ.
KEEP_ALIVE = "10m"


class OllamaAnalyzer:
    engine = "ollama"

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._think_supported = True

    @property
    def available(self) -> bool:
        return self._session is not None

    @property
    def title(self) -> str:
        return f"Ollama / {config.ollama_model}"

    # ------------------------------------------------------------------ #
    async def start(self) -> bool:
        """Проверяет, что сервер поднят и нужная модель загружена."""
        session = aiohttp.ClientSession(timeout=TIMEOUT)
        try:
            async with session.get(f"{config.ollama_url}/api/tags") as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as e:
            log.warning("Ollama недоступен на %s: %s", config.ollama_url, e)
            await session.close()
            return False

        names = {m.get("name", "") for m in data.get("models", [])}
        if config.ollama_model not in names:
            # В списке модели с тегом, а пользователь мог указать имя без него.
            short = {n.split(":")[0] for n in names}
            if config.ollama_model.split(":")[0] not in short:
                log.error(
                    "Модель %s не загружена в Ollama. Выполните: ollama pull %s",
                    config.ollama_model, config.ollama_model,
                )
                await session.close()
                return False

        self._session = session
        log.info("Анализ через локальную модель %s", config.ollama_model)
        return True

    async def stop(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------ #
    async def analyze(self, category: dict, topics: list[dict], title: str, body: str,
                      examples: list[dict] | None = None) -> Analysis | None:
        if self._session is None:
            return None

        payload = {
            "model": config.ollama_model,
            "messages": [
                {"role": "system",
                 "content": build_system(category, topics, examples)},
                {"role": "user", "content": build_post(title, body)[:MAX_LOCAL_CHARS]},
            ],
            "format": OUTPUT_SCHEMA,          # Ollama умеет принуждать к JSON-схеме
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "options": {
                "temperature": 0,
                "num_ctx": NUM_CTX,
                "num_predict": NUM_PREDICT,
            },
        }
        if self._think_supported:
            # Рассуждающие модели вроде qwen3 иначе тратят минуту на размышления,
            # которые для классификации поста не нужны.
            payload["think"] = False

        text = await self._chat(payload)
        if text is None:
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.error("Ollama вернул невалидный JSON: %.200s", text)
            return None
        return parse_payload(data, self.engine)

    async def _chat(self, payload: dict) -> str | None:
        try:
            async with self._session.post(
                f"{config.ollama_url}/api/chat", json=payload
            ) as resp:
                if resp.status == 400 and payload.get("think") is False:
                    # Модель без режима размышлений — повторяем без этого поля.
                    detail = await resp.text()
                    if "think" in detail:
                        log.info("Модель не поддерживает think — отключаю параметр")
                        self._think_supported = False
                        payload.pop("think", None)
                        return await self._chat(payload)
                resp.raise_for_status()
                data = await resp.json()
        except aiohttp.ClientError as e:
            log.error("Ollama: %s", e)
            return None
        except TimeoutError:
            log.error("Ollama не ответил за %s с — пост уйдёт в keyword-режим",
                      TIMEOUT.total)
            return None

        return (data.get("message") or {}).get("content")


ollama_analyzer = OllamaAnalyzer()
