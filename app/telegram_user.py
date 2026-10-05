from __future__ import annotations

from dataclasses import dataclass

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from .db import Database


TELEGRAM_SERVICE_USER_IDS = frozenset({777000})


class TelegramUserError(RuntimeError):
    """Ошибка подключения обычного Telegram-аккаунта."""


@dataclass(frozen=True)
class IncomingTelegramMessage:
    chat_id: str
    message_id: int
    name: str
    username: str | None
    text: str
    avatar: bytes | None = None


class TelegramUserFlow:
    def __init__(self, database: Database) -> None:
        self.database = database

    def process(self, message: IncomingTelegramMessage) -> tuple[int, bool]:
        username = (message.username or "").strip().lstrip("@")
        contact = f"@{username}" if username else f"Telegram ID: {message.chat_id}"
        return self.database.record_telegram_user_message(
            chat_id=message.chat_id,
            message_id=message.message_id,
            name=message.name.strip() or "Без имени",
            contact=contact,
            text=message.text.strip(),
            avatar=message.avatar,
        )


class TelegramUserService:
    def __init__(
        self,
        database: Database,
        *,
        api_id: int | None,
        api_hash: str,
        session: str = "",
        enabled: bool = True,
    ) -> None:
        self.database = database
        self.api_id = api_id
        self.api_hash = api_hash.strip()
        self.session = session.strip()
        self.enabled = enabled
        self.flow = TelegramUserFlow(database)
        self.client: TelegramClient | None = None
        self.authorized = False
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.api_id and self.api_hash and self.session)

    @property
    def connected(self) -> bool:
        return self.authorized

    async def start(self) -> None:
        if not self.configured:
            return
        try:
            client = self._new_client(self.session)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                self.last_error = "Сессия Telegram больше не авторизована."
                return
            self._activate(client)
            self.authorized = True
            self.last_error = ""
        except Exception as error:
            self.last_error = f"Telegram не подключён: {error}"

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None
        self.authorized = False

    def _new_client(self, session: str = "") -> TelegramClient:
        assert self.api_id is not None
        return TelegramClient(StringSession(session), self.api_id, self.api_hash)

    def _activate(self, client: TelegramClient) -> None:
        client.add_event_handler(self._handle_message, events.NewMessage(incoming=True))
        self.client = client

    async def _handle_message(self, event) -> None:
        if not event.is_private or event.out:
            return
        sender = await event.get_sender()
        if (
            sender is None
            or getattr(sender, "bot", False)
            or getattr(sender, "id", None) in TELEGRAM_SERVICE_USER_IDS
        ):
            return
        name = " ".join(
            part
            for part in (getattr(sender, "first_name", ""), getattr(sender, "last_name", ""))
            if part
        )
        avatar = await self._download_avatar(sender)
        self.flow.process(
            IncomingTelegramMessage(
                chat_id=str(event.chat_id),
                message_id=event.message.id,
                name=name,
                username=getattr(sender, "username", None),
                text=self._message_text(event.message),
                avatar=avatar,
            )
        )

    async def _download_avatar(self, sender) -> bytes | None:
        """Возвращает текущее фото отправителя, не срывая приём лида при ошибке."""
        if self.client is None or getattr(sender, "photo", None) is None:
            return None
        try:
            avatar = await self.client.download_profile_photo(sender, file=bytes)
        except Exception:
            return None
        return avatar if isinstance(avatar, bytes) else None

    @staticmethod
    def _message_text(message) -> str:
        text = (message.raw_text or "").strip()
        if text:
            return text
        if message.photo:
            return "[Фото]"
        if message.voice:
            return "[Голосовое сообщение]"
        if message.video:
            return "[Видео]"
        if message.sticker:
            return "[Стикер]"
        if message.document:
            return "[Файл]"
        return "[Сообщение без текста]"
