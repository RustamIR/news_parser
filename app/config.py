"""Конфигурация приложения: читается из .env один раз при импорте."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().isdigit()}


@dataclass(frozen=True)
class Config:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "").strip('"').strip()
    admin_ids: set[int] = field(default_factory=lambda: _ids(os.getenv("ADMIN_IDS")))

    api_id: int = int(os.getenv("TELEGRAM_API_ID") or 0)
    api_hash: str = os.getenv("TELEGRAM_API_HASH", "").strip('"').strip()
    session: str = os.getenv("TELEGRAM_SESSION", "").strip('"').strip()

    # auto — сначала локальная модель, потом Claude; либо ollama / claude / none
    llm_backend: str = os.getenv("LLM_BACKEND", "auto").strip('"').strip()

    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "").strip('"').strip()
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-5").strip('"').strip()

    ollama_url: str = (os.getenv("OLLAMA_URL") or "http://localhost:11434").strip('"').rstrip("/")
    ollama_model: str = (os.getenv("OLLAMA_MODEL") or "qwen3:8b").strip('"').strip()

    db_path: str = str(BASE_DIR / (os.getenv("DB_PATH") or "news_parser.sqlite3").strip('"'))
    default_poll_interval: int = int(os.getenv("DEFAULT_POLL_INTERVAL") or 30)
    default_min_relevance: int = int(os.getenv("DEFAULT_MIN_RELEVANCE") or 60)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip('"')

    @property
    def telethon_ready(self) -> bool:
        return bool(self.api_id and self.api_hash and self.session)

    @property
    def llm_ready(self) -> bool:
        return bool(self.anthropic_key)


config = Config()
