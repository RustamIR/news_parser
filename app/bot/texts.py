"""Форматирование сообщений бота (HTML-разметка Telegram)."""
from __future__ import annotations

from html import escape

KIND_ICONS = {"tg": "✈️", "tgweb": "👀", "rss": "📡", "web": "🌐"}
KIND_NAMES = {
    "tg": "телеграм-канал",
    "tgweb": "телеграм-канал (витрина, без api_id)",
    "rss": "RSS-лента",
    "web": "сайт",
}

HELP = """\
<b>Как это работает</b>

Бот следит за телеграм-каналами и сайтами и присылает <i>не всё подряд</i>, \
а только то, что попадает в заданные вами темы.

Путь новости:
1. <b>Сбор</b> — новые посты из источников рубрики.
2. <b>Предфильтр</b> — пост сверяется с ключевыми словами ваших тем. Не совпал — \
выброшен, дальше не идёт.
3. <b>Анализ</b> — уцелевшие посты читает модель: ставит оценку релевантности \
0–100 и пишет резюме в 2–3 предложения.
4. <b>Отправка</b> — то, что набрало больше порога рубрики, приходит вам.

<b>Рубрики</b> — три готовые: информационная безопасность, финансы, геополитика. \
У каждой свои источники, темы, интервал опроса и порог.

<b>В сообществе</b>
У каждой рубрики свой адрес публикации. Два способа его задать:

· <b>без прав боту</b> — отправьте <code>/target ИБ</code> в той группе или чате, \
куда хотите получать новости. В группе боту хватает обычного участника.
· <b>отдельным каналом</b> — «📢 Привязать канал» в меню рубрики. Здесь бот \
обязан быть администратором с правом «Публикация сообщений»: в каналы Telegram \
посторонним писать не даёт.

Отключить — <code>/target -</code> в том же чате.

Управляющую группу назначает команда /bind, отправленная в самой группе. После \
этого её администраторы пополняют парсер прямо оттуда — формой или через /add.

<b>Форма пополнения</b> — источники и фильтры одним сообщением, шаблон по /form:
<pre>рубрика: ИБ
источники:
@dataleak
https://xakep.ru/feed/
тема: Утечки данных
ключевые: утечка, слив базы
стоп: вебинар, курс</pre>

<b>Команды</b>
/start — главное меню
/form — шаблон формы
/add — принять форму
/digest — свежий дайджест
/run — проверить источники прямо сейчас
/stats — статистика фильтрации
/target — публиковать рубрику в этот чат
/bind — назначить группу управляющей
/chatid — узнать ID чата
/help — эта справка

<b>Ключевые слова темы</b>
· <code>санкции</code> — совпадёт с «санкций», «санкциям» (учитывается основа слова)
· <code>утечк*</code> — явный префикс: «утечка», «утечками»
· <code>ставка ЦБ</code> — фраза целиком
· <b>стоп-слова</b> отбрасывают пост, даже если ключевые слова совпали — \
ими удобно резать рекламу: <code>вебинар, курс, розыгрыш</code>
"""


def category_title(category: dict) -> str:
    return f"{category['emoji']} {category['title']}".strip()


def format_item(item: dict) -> str:
    """Карточка новости для дайджеста и для мгновенной отправки."""
    parts: list[str] = []

    head = item.get("category", "")
    if topic := item.get("topic_name"):
        head = f"{head} · 🎯 {escape(str(topic))}" if head else f"🎯 {escape(str(topic))}"
    if head:
        parts.append(f"<i>{head}</i>")

    title = escape((item.get("title") or "Без заголовка").strip())
    parts.append(f"<b>{title}</b>")

    if summary := (item.get("summary") or "").strip():
        parts.append(escape(summary))

    if impact := (item.get("impact") or "").strip():
        parts.append(f"⚡️ <i>{escape(impact)}</i>")

    if tags := item.get("tags"):
        parts.append("🏷 " + ", ".join(f"#{escape(str(t)).replace(' ', '_')}" for t in tags))

    footer = f"📊 {item.get('relevance', 0)}%"
    if item.get("engine") == "keywords":
        footer += " (по ключевым словам)"
    if url := item.get("url"):
        footer = f'<a href="{escape(url, quote=True)}">Открыть источник</a> · {footer}'
    parts.append(footer)

    return "\n\n".join(parts)


def format_source(source: dict) -> str:
    icon = KIND_ICONS.get(source["kind"], "•")
    state = "" if source["enabled"] else " (выключен)"
    line = f"{icon} <code>{escape(source['url'])}</code>{state}"
    if source.get("last_error"):
        line += f"\n   ⚠️ {escape(source['last_error'][:120])}"
    return line


def format_topic(topic: dict) -> str:
    state = "" if topic["enabled"] else " (выключена)"
    line = f"🎯 <b>{escape(topic['title'])}</b>{state}"
    if topic.get("description"):
        line += f"\n   {escape(topic['description'][:200])}"
    if topic.get("keywords"):
        line += "\n   🔑 " + escape(", ".join(topic["keywords"][:12]))
    if topic.get("stopwords"):
        line += "\n   ⛔ " + escape(", ".join(topic["stopwords"][:12]))
    return line


def format_stats(category: dict, data: dict) -> str:
    total = data["total"] or 1
    return (
        f"<b>{category_title(category)} — статистика</b>\n\n"
        f"Всего обработано постов: <b>{data['total']}</b>\n"
        f"За последние 24 часа: {data['last_24h']}\n\n"
        f"Отсеяно предфильтром: {data['skipped']} "
        f"({data['skipped'] * 100 // total}%)\n"
        f"Отсеяно анализом: {data['rejected']}\n"
        f"Попало в дайджест: <b>{data['relevant']}</b>\n\n"
        f"Интервал опроса: {category['poll_interval_min']} мин · "
        f"порог: {category['min_relevance']}%"
    )
