"""Анализ через Claude API."""
from __future__ import annotations

import json
import logging

import anthropic

from app.analysis.base import (
    Analysis,
    OUTPUT_SCHEMA,
    build_post,
    build_system,
    parse_payload,
)
from app.config import config

log = logging.getLogger(__name__)


class ClaudeAnalyzer:
    engine = "claude"

    def __init__(self) -> None:
        self._client: anthropic.AsyncAnthropic | None = None
        self._disabled_reason = ""

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def title(self) -> str:
        return f"Claude / {config.anthropic_model}"

    async def start(self) -> bool:
        if not config.llm_ready:
            return False
        self._client = anthropic.AsyncAnthropic(api_key=config.anthropic_key)
        log.info("Анализ через %s", config.anthropic_model)
        return True

    async def stop(self) -> None:
        self._client = None

    def _disable(self, reason: str) -> None:
        """Выключает движок до перезапуска.

        Кончились деньги или ключ отозван — это не пройдёт само. Без такого
        предохранителя бот будет отправлять обречённый запрос на каждый пост:
        медленно, шумно в логах и бессмысленно.
        """
        self._client = None
        self._disabled_reason = reason
        log.error(
            "Анализ через Claude отключён до перезапуска: %s. "
            "Бот продолжает работать в режиме ключевых слов.", reason,
        )

    # ------------------------------------------------------------------ #
    async def analyze(self, category: dict, topics: list[dict], title: str, body: str,
                      examples: list[dict] | None = None) -> Analysis | None:
        if self._client is None:
            return None

        try:
            response = await self._client.messages.create(
                model=config.anthropic_model,
                max_tokens=2000,
                system=[{
                    "type": "text",
                    "text": build_system(category, topics, examples),
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": build_post(title, body)}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                },
            )
        except anthropic.RateLimitError:
            log.warning("Claude: превышен лимит запросов, пост уйдёт в keyword-режим")
            return None
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            self._disable(f"ключ отклонён ({e.status_code})")
            return None
        except anthropic.BadRequestError as e:
            message = str(e)
            if "credit balance" in message or "billing" in message.lower():
                self._disable("на балансе Anthropic нет средств")
            else:
                log.error("Claude API отклонил запрос: %s", message)
            return None
        except anthropic.APIError as e:
            log.error("Claude API: %s", e)
            return None

        if response.stop_reason == "refusal":
            log.warning("Claude отказался анализировать пост (%s)", response.stop_details)
            return None

        text = next((b.text for b in response.content if b.type == "text"), "")
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.error("Claude вернул невалидный JSON: %.200s", text)
            return None
        return parse_payload(data, self.engine)


claude_analyzer = ClaudeAnalyzer()
