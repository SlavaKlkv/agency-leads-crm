from __future__ import annotations

from dataclasses import dataclass
import os


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

    @classmethod
    def from_env(cls) -> "Settings":
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
        )
