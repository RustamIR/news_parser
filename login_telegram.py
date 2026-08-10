"""Разовая авторизация Telethon.

Запустить один раз: `python login_telegram.py`. Скрипт попросит номер телефона,
код из Telegram (и пароль 2FA, если он включён), после чего запишет
TELEGRAM_SESSION в .env — дальше бот подключается без вопросов.

Строка сессии равносильна доступу к вашему аккаунту: не коммитьте .env и не
передавайте её никому.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import BASE_DIR, config

ENV_PATH = BASE_DIR / ".env"


def write_session(session: str) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    entry = f'TELEGRAM_SESSION="{session}"'
    for i, line in enumerate(lines):
        if re.match(r"^\s*TELEGRAM_SESSION\s*=", line):
            lines[i] = entry
            break
    else:
        lines.append(entry)

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path(ENV_PATH).chmod(0o600)


def ask_phone() -> str:
    """Спрашивает именно номер телефона.

    Штатный вопрос Telethon звучит как «phone (or bot token)», и токен бота там
    принимается — получается сессия бота, которая не умеет читать чужие каналы.
    Поэтому спрашиваем сами и токен не пропускаем.
    """
    while True:
        value = input("Номер телефона в формате +79991234567: ").strip()
        if ":" in value or value.lower().startswith("bot"):
            print("   Это похоже на токен бота. Нужен номер телефона вашего "
                  "аккаунта — от его имени бот будет читать каналы.\n")
            continue
        if value:
            return value


async def main() -> None:
    if not config.api_id or not config.api_hash:
        sys.exit(
            "Сначала заполните TELEGRAM_API_ID и TELEGRAM_API_HASH в .env.\n"
            "Получить их: https://my.telegram.org → API development tools."
        )

    print("Вход от имени вашего аккаунта Telegram (не бота).")
    print("Код придёт в приложение Telegram, а не по SMS.\n")

    client = TelegramClient(StringSession(), config.api_id, config.api_hash)
    async with client:
        await client.start(phone=ask_phone)
        me = await client.get_me()
        if getattr(me, "bot", False):
            sys.exit(
                "\n❌ Получилась сессия бота — читать чужие каналы так нельзя.\n"
                "Запустите скрипт снова и введите номер телефона аккаунта."
            )
        write_session(client.session.save())
        print(f"\n✅ Готово. Авторизован как {me.first_name} (@{me.username}).")
        print("TELEGRAM_SESSION записан в .env — можно запускать `python run.py`.")


if __name__ == "__main__":
    asyncio.run(main())
