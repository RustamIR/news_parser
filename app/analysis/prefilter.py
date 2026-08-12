"""Дешёвый предфильтр по ключевым словам.

Задача — не пропускать в дорогой LLM-анализ всё подряд. Сначала пост
проверяется по темам рубрики: если он не задевает ни одну тему, он
отбрасывается без единого обращения к модели.

Ключевые слова:
  * `санкции`            — совпадение по основе слова (санкций, санкциям, ...);
  * `утечк*`             — явный префикс: «утечка», «утечками»;
  * `персональные данные`— фраза; слова ищутся подряд, тоже по основам,
                           поэтому найдётся и «персональных данных».
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Порядок важен: длинные окончания проверяются первыми.
_ENDINGS = (
    "ическими", "ического", "ическому", "ическая", "ическое", "ические", "ических",
    "ованиями", "ованиях", "ованием", "ования", "овании",
    "иями", "иях", "иям", "ями", "ами", "ыми", "ими",
    "ого", "его", "ому", "ему", "ется", "ются",
    "ах", "ях", "ов", "ев", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие",
    "ых", "их", "ым", "им", "ую", "юю", "ей", "ой", "ем", "ом", "ам", "ям",
    "ью", "ии", "ия", "ие", "ла", "ло", "ли",
    "а", "я", "о", "е", "ы", "и", "у", "ю", "й", "ь",
)
_MIN_STEM = 4
_WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def stem(word: str) -> str:
    """Простой стеммер: срезает самое длинное известное окончание.

    Второй проход добивает соединительные «и»/«ь», иначе формы одного слова
    расходятся: санкции → санкци, но санкциями → санкц.
    """
    word = word.lower()
    for ending in _ENDINGS:
        if len(word) - len(ending) >= _MIN_STEM and word.endswith(ending):
            word = word[: -len(ending)]
            break
    while len(word) > _MIN_STEM and word[-1] in "иь":
        word = word[:-1]
    return word


def normalize(text: str) -> str:
    return _WS_RE.sub(" ", text.lower().replace("ё", "е")).strip()


class TextIndex:
    """Разобранный текст: основы слов по порядку и множеством — для быстрых проверок."""

    def __init__(self, text: str) -> None:
        self.normalized = normalize(text)
        self.tokens = [stem(w) for w in _WORD_RE.findall(self.normalized)]
        self.stems = set(self.tokens)

    def has(self, keyword: str) -> bool:
        key = normalize(keyword)
        if not key:
            return False
        parts = _WORD_RE.findall(key.replace("*", " * "))
        # `*` после слова означает «совпадение по префиксу основы»
        pattern: list[tuple[str, bool]] = []
        for part in parts:
            if part == "*":
                if pattern:
                    pattern[-1] = (pattern[-1][0], True)
                continue
            pattern.append((stem(part), key.endswith("*") and part == parts[-1]))
        if not pattern:
            return False
        if len(pattern) == 1:
            token, prefix = pattern[0]
            return (any(s.startswith(token) for s in self.stems)
                    if prefix else token in self.stems)
        return self._contains_sequence(pattern)

    def _contains_sequence(self, pattern: list[tuple[str, bool]]) -> bool:
        span = len(pattern)
        for i in range(len(self.tokens) - span + 1):
            window = self.tokens[i:i + span]
            if all(
                (tok.startswith(exp) if prefix else tok == exp)
                for tok, (exp, prefix) in zip(window, pattern, strict=True)
            ):
                return True
        return False


@dataclass
class TopicMatch:
    topic: dict
    score: int
    matched: list[str]


class Prefilter:
    """Считает, каким темам рубрики соответствует новость."""

    def __init__(self, topics: list[dict]) -> None:
        self.topics = topics

    def match(self, title: str, body: str) -> list[TopicMatch]:
        title_index = TextIndex(title)
        full_index = TextIndex(f"{title} {body}")

        results: list[TopicMatch] = []
        for topic in self.topics:
            if self._hits(topic.get("stopwords") or [], full_index):
                continue                                    # тема явно исключена

            keywords = topic.get("keywords") or []
            if not keywords:
                # Тема описана только текстом — решение отдаём модели.
                results.append(TopicMatch(topic, 50, []))
                continue

            matched = self._hits(keywords, full_index)
            if not matched:
                continue
            score = min(100, 40 + 20 * len(matched))
            if self._hits(matched, title_index):
                score = min(100, score + 10)                # попадание в заголовок весомее
            results.append(TopicMatch(topic, score, matched))

        results.sort(key=lambda m: m.score, reverse=True)
        return results

    @staticmethod
    def _hits(keywords: list[str], index: TextIndex) -> list[str]:
        return [kw for kw in keywords if index.has(kw)]


def extractive_summary(text: str, sentences: int = 3, limit: int = 400) -> str:
    """Резюме без модели: первые осмысленные предложения поста."""
    clean = _WS_RE.sub(" ", text).strip()
    parts = re.split(r"(?<=[.!?…])\s+", clean)
    picked: list[str] = []
    for part in parts:
        if len(part) < 25:                                  # заголовки, эмодзи, подписи
            continue
        picked.append(part)
        if len(picked) >= sentences:
            break
    summary = " ".join(picked) or clean
    return summary[:limit].rstrip() + ("…" if len(summary) > limit else "")
