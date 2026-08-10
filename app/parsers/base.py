"""Общая структура сырой новости, которую отдаёт любой парсер."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

_WS = re.compile(r"\s+")


@dataclass
class RawItem:
    source_id: int
    category_id: int
    external_id: str = ""          # id сообщения в TG / guid в RSS
    url: str = ""
    title: str = ""
    content: str = ""
    published_at: datetime | None = None
    meta: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Текст для фильтрации и анализа."""
        return _WS.sub(" ", f"{self.title}\n{self.content}").strip()

    @property
    def hash(self) -> str:
        """Дедупликация: по url/external_id, иначе по содержимому."""
        key = self.url or f"{self.source_id}:{self.external_id}"
        if not self.url and not self.external_id:
            key = _WS.sub(" ", self.content)[:512]
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @property
    def published_iso(self) -> str | None:
        return self.published_at.isoformat(timespec="seconds") if self.published_at else None
