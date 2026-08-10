"""Роутеры бота. Порядок важен: menu идёт первым, у него команды и главное меню."""
from app.bot.handlers import (
    community, digest, learning, menu, settings, sources, topics,
)

# community идёт последним: его ловушка форм не должна перехватывать ввод
# пошаговых мастеров из остальных роутеров.
routers = (
    menu.router,
    sources.router,
    topics.router,
    digest.router,
    settings.router,
    learning.router,
    community.router,
)

__all__ = ["routers"]
