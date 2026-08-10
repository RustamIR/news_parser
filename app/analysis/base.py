"""Общее для всех движков анализа: промпт, схема ответа, разбор результата.

Движков два — Claude через API и локальная модель через Ollama. Отличаются они
только транспортом, поэтому промпт и схема живут здесь, а не дублируются.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_INPUT_CHARS = 6000

SYSTEM_TEMPLATE = """\
Ты — редактор новостного дайджеста по рубрике «{category}».
{hint}

Тебе дают один пост из отслеживаемого источника и список тем, которые интересуют \
пользователя. Твоя задача — решить, относится ли пост хотя бы к одной из тем, и \
если да — коротко пересказать суть.

Правила:
- relevance — насколько пост полезен по выбранной теме: 0 (не по теме) … 100 \
(прямое попадание, важная новость).
- Реклама, анонсы вебинаров, розыгрыши, дайджесты «что почитать», перепосты без \
фактуры — это relevance ниже 30, даже если ключевые слова совпали.
- summary — 2–3 предложения на русском: что произошло. Только факты из текста, \
без вводных фраз вроде «В посте говорится» и без домыслов.
- impact — одно предложение: кого это касается и что меняет на практике. Если из \
поста это не следует, оставь пустую строку — не выдумывай последствия.
- topic — точное название одной темы из списка, к которой пост относится ближе \
всего; если ни к одной, оставь пустую строку.
- tags — 2–4 коротких тега на русском (например: «утечка», «Сбербанк», «ЦБ»).

Отвечай строго объектом JSON по заданной схеме, без пояснений вокруг него.

Темы пользователя:
{topics}
{examples}"""

EXAMPLES_HEADER = """
Пользователь уже оценивал похожие посты. Ориентируйся на эти решения — они важнее \
твоей интуиции о том, что интересно:
{lines}
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean", "description": "Относится ли пост к темам"},
        "topic": {"type": "string", "description": "Название темы или пустая строка"},
        "relevance": {"type": "integer", "description": "Оценка релевантности 0-100"},
        "summary": {"type": "string", "description": "Что произошло, 2-3 предложения"},
        "impact": {"type": "string", "description": "Кого касается и что меняет"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["relevant", "topic", "relevance", "summary", "impact", "tags"],
    "additionalProperties": False,
}

# Сколько оценённых пользователем постов подмешивать в промпт
MAX_EXAMPLES = 8
EXAMPLE_CHARS = 160


@dataclass
class Analysis:
    relevant: bool
    topic: str
    relevance: int
    summary: str
    impact: str = ""
    tags: list[str] = field(default_factory=list)
    engine: str = "llm"


def build_system(category: dict, topics: list[dict],
                 examples: list[dict] | None = None) -> str:
    topics_block = "\n".join(
        f"- {t['title']}: {t.get('description') or 'без дополнительного описания'}"
        for t in topics
    ) or "- (темы не заданы, оценивай по общей тематике рубрики)"

    examples_block = ""
    if examples:
        lines = "\n".join(
            f"- {'НУЖНО' if e['verdict'] > 0 else 'НЕ НУЖНО'}: "
            f"{(e['title'] or '').strip()[:EXAMPLE_CHARS]}"
            for e in examples[:MAX_EXAMPLES]
        )
        examples_block = EXAMPLES_HEADER.format(lines=lines)

    return SYSTEM_TEMPLATE.format(
        category=category["title"],
        hint=category.get("prompt_hint") or "",
        topics=topics_block,
        examples=examples_block,
    )


def build_post(title: str, body: str) -> str:
    return f"Заголовок: {title}\n\nТекст:\n{body}"[:MAX_INPUT_CHARS]


def parse_payload(data: dict, engine: str) -> Analysis:
    """Приводит ответ модели к Analysis, не доверяя типам полей."""
    try:
        relevance = int(data.get("relevance") or 0)
    except (TypeError, ValueError):
        relevance = 0
    tags = data.get("tags") or []
    if isinstance(tags, str):                       # мелкие модели иногда шлют строку
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return Analysis(
        relevant=bool(data.get("relevant")),
        topic=str(data.get("topic") or ""),
        relevance=max(0, min(100, relevance)),
        summary=str(data.get("summary") or "").strip(),
        impact=str(data.get("impact") or "").strip(),
        tags=[str(t) for t in tags][:4],
        engine=engine,
    )
