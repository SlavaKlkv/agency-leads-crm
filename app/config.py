from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@dataclass(frozen=True)
class Settings:
    app_name: str = "Leadroom"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:55432/leadroom"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    public_base_url: str = ""
    telegram_api_id: int | None = None
    telegram_api_hash: str = ""
    telegram_session: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        telegram_api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        return cls(
            app_name=os.getenv("APP_NAME", "Leadroom"),
            database_url=normalize_database_url(
                os.getenv(
                    "DATABASE_URL",
                    "postgresql+psycopg://postgres:postgres@localhost:55432/leadroom",
                )
            ),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", ""),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
            telegram_api_id=int(telegram_api_id) if telegram_api_id else None,
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH", ""),
            telegram_session=os.getenv("TELEGRAM_SESSION", ""),
        )
