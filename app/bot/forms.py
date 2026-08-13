"""Форма одним сообщением: источники и фильтры сразу, без пошагового мастера.

В группе сообщества пошаговый диалог неудобен — несколько человек пишут в один
чат, состояние путается. Поэтому основной способ пополнения — форма вида:

    рубрика: ИБ
    источники:
    @dataleak
    https://xakep.ru/feed/
    тема: Утечки данных
    описание: подтверждённые утечки баз российских компаний
    ключевые: утечка, слив базы, персональные данные
    стоп: вебинар, курс, скидка

Порядок полей произвольный, заполнять все не обязательно: можно прислать только
источники или только ключевые слова к уже существующей теме.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from html import escape

from app.bot.utils import prepare_source, split_list, tg_reading_limited
from app.db import repo

TGWEB_NOTE = (
    "ℹ️ Telethon сейчас не поднят, поэтому каналы читаются через публичную "
    "витрину <code>t.me/s/…</code>: последние ~20 постов открытых каналов, без "
    "закрытых и приватных. Чинить источники не придётся — как только сессия "
    "заработает, они сами начнут читаться полноценно."
)

# Синонимы полей: пользователь пишет как удобно.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "category": ("рубрика", "категория", "направление", "раздел"),
    "sources": ("источник", "источники", "ресурс", "ресурсы", "канал", "каналы",
                "сайт", "сайты", "лента", "ленты", "чат", "чаты"),
    "topic": ("тема", "фильтр", "название"),
    "description": ("описание", "интересует", "что интересует", "детали", "контекст"),
    "keywords": ("ключевые", "ключевые слова", "ключи", "слова", "keywords"),
    "stopwords": ("стоп", "стоп-слова", "стопслова", "стоп слова", "минус",
                  "минус-слова", "исключить", "кроме"),
    "mode": ("режим", "mode"),
}
# Значения поля «режим», включающие перезапись вместо дополнения
REPLACE_WORDS = {"замена", "заменить", "перезапись", "перезаписать", "replace"}
_ALIAS_TO_FIELD = {alias: fld for fld, aliases in FIELD_ALIASES.items() for alias in aliases}
_LINE_RE = re.compile(r"^\s*([^:]{2,40}?)\s*:\s*(.*)$")

TEMPLATE = """\
<b>Форма пополнения</b> — скопируйте, замените значения и отправьте сюда же.

<pre>рубрика: ИБ
источники:
@dataleak
t.me/s/xakep_ru
https://xakep.ru/feed/
https://www.anti-malware.ru/news
тема: Утечки данных
описание: подтверждённые утечки баз российских компаний, реакция регулятора
ключевые: утечка, слив базы, персональные данные, взлом*
стоп: вебинар, курс, скидка, розыгрыш</pre>

<b>Что обязательно</b>
· <code>рубрика</code> — ИБ, финансы или геополитика (можно частью названия)
· остальное по желанию: только источники, только фильтры или всё сразу

<b>Источники</b>
· <code>@name</code> — канал через Telethon (нужен api_id)
· <code>t.me/s/name</code> — публичная витрина канала, без api_id
· ссылка с rss/feed/.xml — лента, любая другая ссылка — сайт

<b>Правила слов</b>
· <code>санкции</code> — по основе: поймает «санкций», «санкциям»
· <code>утечк*</code> — по префиксу
· <code>персональные данные</code> — фраза, тоже по основам
· <code>стоп</code> отбрасывает пост, даже если ключевые совпали

<b>Режим</b>
По умолчанию форма <b>дополняет</b>: новые источники и слова добавляются, \
старые остаются. Строка <code>режим: замена</code> включает перезапись — \
в рубрике останется ровно то, что в тексте, а остальное будет удалено.

⚠️ При замене удаление источника уносит и все разобранные с него посты.
Готовые формы с текущими настройками: <code>/form ИБ</code> — они уже идут \
с <code>режим: замена</code>, чтобы правку можно было отправить назад как есть.

Если <code>тема</code> не указана, а ключевые слова есть — они добавятся \
к единственной теме рубрики. Когда тем несколько, название обязательно.
"""


def _source_line(source: dict) -> str:
    """Источник в том виде, в каком его принимает форма."""
    if source["kind"] == "tg":
        return f"@{source['url']}"
    if source["kind"] == "tgweb":
        return f"t.me/s/{source['url']}"
    return source["url"]


async def render_editable(category: dict) -> list[str]:
    """Рубрика в виде форм, готовых к копированию и правке.

    Один блок на тему: поле «тема» в форме одно, а тем в рубрике может быть
    несколько. В каждом блоке стоит «режим: замена» — чтобы удалённое из текста
    слово действительно исчезло из темы, а не осталось после дополнения.
    """
    sources = await repo.list_sources(category["id"])
    topics = await repo.list_topics(category["id"])
    blocks: list[str] = []

    head = [f"рубрика: {category['key']}"]
    if sources:
        head.append("режим: замена")
        head.append("источники:")
        head += [_source_line(s) for s in sources]
    blocks.append("\n".join(head))

    for topic in topics:
        lines = [f"рубрика: {category['key']}", "режим: замена",
                 f"тема: {topic['title']}"]
        if topic["description"]:
            lines.append(f"описание: {topic['description']}")
        lines.append("ключевые: " + (", ".join(topic["keywords"]) or "-"))
        lines.append("стоп: " + (", ".join(topic["stopwords"]) or "-"))
        blocks.append("\n".join(lines))

    return blocks


async def render_setup(category: dict | None = None, detailed: bool = False) -> str:
    """Текущая настройка рубрик — чтобы форму писать, видя, что уже есть.

    Без аргумента — сводка по всем рубрикам; с рубрикой — подробности с
    ключевыми и стоп-словами, которые как раз и правят формой.
    """
    from app.bot.texts import KIND_ICONS

    categories = [category] if category else await repo.list_categories()
    blocks: list[str] = []

    for cat in categories:
        sources = await repo.list_sources(cat["id"])
        topics = await repo.list_topics(cat["id"])
        lines = [f"<b>{cat['emoji']} {cat['title']}</b> "
                 f"<code>{cat['key']}</code>"]

        if sources:
            shown = sources if detailed else sources[:6]
            names = " ".join(
                f"{KIND_ICONS.get(s['kind'], '•')}{'' if s['enabled'] else '⚪️'}"
                f"{s['url'].split('//')[-1][:28]}"
                for s in shown
            )
            more = "" if len(shown) == len(sources) else f" +{len(sources) - len(shown)}"
            lines.append(f"🔗 {names}{more}")
        else:
            lines.append("🔗 <i>источников нет</i>")

        if topics:
            for t in topics:
                mark = "" if t["enabled"] else " ⚪️"
                lines.append(f"🎯 <b>{t['title']}</b>{mark}")
                if detailed:
                    if t["keywords"]:
                        lines.append("   🔑 " + ", ".join(t["keywords"]))
                    if t["stopwords"]:
                        lines.append("   ⛔ " + ", ".join(t["stopwords"]))
                elif t["keywords"]:
                    lines.append(f"   🔑 {len(t['keywords'])} слов, "
                                 f"⛔ {len(t['stopwords'])}")
        else:
            lines.append("🎯 <i>тем нет — рубрика пропускается</i>")

        target = cat["target_title"] if cat["target_chat_id"] else "не привязан"
        lines.append(f"⏱ {cat['poll_interval_min']} мин · "
                     f"порог {cat['min_relevance']}% · 📢 {target}")
        blocks.append("\n".join(lines))

    return "<b>Сейчас настроено</b>\n\n" + "\n\n".join(blocks)


@dataclass
class FormData:
    category: str = ""
    sources: list[str] = field(default_factory=list)
    topic: str = ""
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    stopwords: list[str] = field(default_factory=list)
    mode: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.sources or self.keywords or self.stopwords or self.topic)

    @property
    def replace(self) -> bool:
        """Заменять списки слов целиком, а не дополнять.

        Нужно для правки скопированной формы: без этого удалённое из текста
        слово осталось бы в теме.
        """
        return self.mode.strip().lower() in REPLACE_WORDS


def looks_like_form(text: str | None) -> bool:
    """Похоже ли сообщение на форму — чтобы ловить их в чате без команды.

    Принимает None: magic-фильтр зовёт эту функцию и для сообщений без текста —
    стикеров, фото, служебных событий о входе в чат.
    """
    if not text:
        return False
    for line in text.splitlines():
        if m := _LINE_RE.match(line):
            if _normalize_key(m.group(1)) in _ALIAS_TO_FIELD:
                return True
    return False


def _normalize_key(raw: str) -> str:
    return raw.strip().lower().replace("ё", "е").lstrip("-—*• ")


def parse_form(text: str) -> FormData:
    """Разбирает текст формы. Значение может быть в той же строке или ниже списком."""
    buffers: dict[str, list[str]] = {}
    current: str | None = None

    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("/"):
            continue
        matched = None
        if m := _LINE_RE.match(line):
            matched = _ALIAS_TO_FIELD.get(_normalize_key(m.group(1)))
        if matched:
            current = matched
            buffers.setdefault(current, [])
            if rest := m.group(2).strip():
                buffers[current].append(rest)
        elif current:
            buffers[current].append(line.strip())

    def joined(name: str) -> str:
        return " ".join(buffers.get(name, [])).strip()

    def listed(name: str) -> list[str]:
        return split_list("\n".join(buffers.get(name, [])))

    return FormData(
        category=joined("category"),
        sources=listed("sources"),
        topic=joined("topic")[:120],
        description=joined("description")[:800],
        keywords=listed("keywords"),
        stopwords=listed("stopwords"),
        mode=joined("mode"),
    )


async def apply_form(form: FormData) -> str:
    """Применяет форму к базе и возвращает готовый отчёт для пользователя."""
    if not form.category:
        return ("❌ Не указана рубрика.\n\nДобавьте строку "
                "<code>рубрика: ИБ</code> (или финансы / геополитика).")

    category = await repo.find_category(form.category)
    if category is None:
        names = ", ".join(c["title"] for c in await repo.list_categories())
        return f"❌ Рубрика «{escape(form.category)}» не найдена.\n\nЕсть: {names}."

    if form.is_empty:
        return ("❌ В форме нет ни источников, ни фильтров — "
                "добавить нечего. Шаблон: /form")

    report = [f"<b>{category['emoji']} {category['title']}</b>"]
    report += await _apply_sources(category, form)
    report += await _apply_filters(category, form)
    return "\n\n".join(report)


async def _apply_sources(category: dict, form: FormData) -> list[str]:
    if not form.sources:
        return []

    from app.bot.texts import KIND_ICONS

    added, skipped, bad, limited = [], [], [], False
    keep: set[tuple[str, str]] = set()
    for raw in form.sources:
        kind, url, title, error = await prepare_source(raw)
        if error:
            # Текст пришёл от пользователя, а ответ уходит с parse_mode=HTML —
            # без экранирования угловые скобки ломают отправку сообщения.
            bad.append(f"{escape(raw)} — {escape(error)}")
            continue
        keep.add((kind, url))
        limited = limited or (kind == "tg" and tg_reading_limited())
        source_id = await repo.add_source(category["id"], kind, url, title)
        (added if source_id else skipped).append(
            f"{KIND_ICONS[kind]} {escape(url)}")

    removed: list[str] = []
    if form.replace:
        # Правка скопированной формы: чего нет в списке — того нет и в рубрике.
        # Разбирать нечего только при пустом списке, но тогда сюда не доходим.
        for s in await repo.list_sources(category["id"]):
            if (s["kind"], s["url"]) in keep:
                continue
            lost = await repo.count_items(s["id"])
            await repo.delete_source(s["id"])
            tail = f" (и {lost} разобранных постов)" if lost else ""
            removed.append(f"{KIND_ICONS.get(s['kind'], '•')} {escape(s['url'])}{tail}")

    block = []
    if added:
        block.append("✅ Источники добавлены:\n" + "\n".join(added))
    if removed:
        block.append("🗑 Источники удалены:\n" + "\n".join(removed))
    if skipped:
        block.append("↩️ Уже были:\n" + "\n".join(skipped))
    if bad:
        block.append("❌ Не приняты:\n" + "\n".join(bad))
    if limited:
        block.append(TGWEB_NOTE)
    return block


async def _apply_filters(category: dict, form: FormData) -> list[str]:
    if not (form.topic or form.keywords or form.stopwords or form.description):
        return []

    topics = await repo.list_topics(category["id"])

    if form.topic:
        existing = next(
            (t for t in topics if t["title"].lower() == form.topic.lower()), None
        )
    elif len(topics) == 1:
        existing = topics[0]                    # дополняем единственную тему
    elif not topics:
        return ["❌ Ключевые слова некуда положить: в рубрике нет тем.\n"
                "Добавьте строку <code>тема: Название</code>."]
    else:
        names = ", ".join(f"«{t['title']}»" for t in topics)
        return [f"❌ В рубрике несколько тем ({names}) — укажите, "
                f"к какой относятся слова: <code>тема: Название</code>."]

    if existing is None:
        topic_id = await repo.add_topic(
            category["id"], form.topic, form.description,
            form.keywords, form.stopwords,
        )
        if topic_id is None:
            return ["❌ Тема с таким названием уже есть — не смог добавить."]
        lines = [f"🎯 Тема создана: <b>{escape(form.topic)}</b>"]
        if form.keywords:
            lines.append("🔑 " + escape(", ".join(form.keywords)))
        if form.stopwords:
            lines.append("⛔ " + escape(", ".join(form.stopwords)))
        if not form.keywords:
            lines.append("<i>Ключевых слов нет — все посты рубрики пойдут "
                         "на анализ модели.</i>")
        return ["\n".join(lines)]

    if form.replace:
        # Правка скопированной формы: списки становятся ровно такими, как прислали.
        await repo.update_topic(
            existing["id"],
            description=form.description or None,
            keywords=form.keywords,
            stopwords=form.stopwords,
        )
        lines = [f"🎯 Тема перезаписана: <b>{existing['title']}</b>"]
        if gone := [k for k in existing["keywords"] if k not in form.keywords]:
            lines.append("🔑 убрано: " + ", ".join(gone))
        if gone := [s for s in existing["stopwords"] if s not in form.stopwords]:
            lines.append("⛔ убрано: " + ", ".join(gone))
        if new := [k for k in form.keywords if k not in existing["keywords"]]:
            lines.append("🔑 добавлено: " + ", ".join(new))
        if new := [s for s in form.stopwords if s not in existing["stopwords"]]:
            lines.append("⛔ добавлено: " + ", ".join(new))
        return ["\n".join(lines)]

    # По умолчанию тему дополняем, а не затираем: форма, написанная руками,
    # обычно добавляет пару слов и не должна снести остальные.
    keywords = existing["keywords"] + [k for k in form.keywords
                                       if k not in existing["keywords"]]
    stopwords = existing["stopwords"] + [s for s in form.stopwords
                                         if s not in existing["stopwords"]]
    await repo.update_topic(
        existing["id"],
        description=form.description or None,
        keywords=keywords if form.keywords else None,
        stopwords=stopwords if form.stopwords else None,
    )
    lines = [f"🎯 Тема дополнена: <b>{existing['title']}</b>"]
    if new := [k for k in form.keywords if k not in existing["keywords"]]:
        lines.append("🔑 добавлено: " + ", ".join(new))
    if new := [s for s in form.stopwords if s not in existing["stopwords"]]:
        lines.append("⛔ добавлено: " + ", ".join(new))
    if form.description:
        lines.append("📝 описание обновлено")
    return ["\n".join(lines)]
