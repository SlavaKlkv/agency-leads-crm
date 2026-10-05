from __future__ import annotations

import argparse
import asyncio
from getpass import getpass
from pathlib import Path
import sys

from dotenv import set_key
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from .config import Settings
from .telegram_user import TelegramUserError


PROJECT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def save_session(session: str, env_path: Path = PROJECT_ENV_PATH) -> None:
    env_path.touch(exist_ok=True)
    set_key(env_path, "TELEGRAM_SESSION", session, quote_mode="always")


async def generate_session() -> str:
    settings = Settings.from_env()
    if not settings.telegram_api_id or not settings.telegram_api_hash.strip():
        raise TelegramUserError("Заполните TELEGRAM_API_ID и TELEGRAM_API_HASH.")

    phone = input("Номер Telegram: ").strip()
    if not phone:
        raise TelegramUserError("Укажите номер Telegram-аккаунта.")

    client = TelegramClient(
        StringSession(), settings.telegram_api_id, settings.telegram_api_hash.strip()
    )
    await client.connect()
    try:
        sent_code = await client.send_code_request(phone)
        code = input("Код из Telegram: ").strip()
        try:
            await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=sent_code.phone_code_hash,
            )
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass("Пароль 2FA: "))
        return client.session.save()
    except Exception as error:
        raise TelegramUserError(f"Не удалось создать Telegram-сессию: {error}") from error
    finally:
        await client.disconnect()


async def run(action: str) -> int:
    if action != "generate":
        return 2
    try:
        session = await generate_session()
    except TelegramUserError as error:
        print(str(error), file=sys.stderr)
        return 1

    save_session(session)

    print(f"\nTelegram-сессия сохранена в {PROJECT_ENV_PATH}.")
    print("Скопируйте это же значение в TELEGRAM_SESSION на Render:")
    print(session)
    print("\nНе публикуйте эту строку: она даёт доступ к Telegram-аккаунту.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Создание Telegram-сессии для переменной окружения Render."
    )
    parser.add_argument(
        "action",
        choices=("generate",),
        help="создать строку Telegram-сессии",
    )
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(run(arguments.action)))
