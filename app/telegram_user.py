from __future__ import annotations

from base64 import urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from .db import Database


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
    ) -> None:
        self.database = database
        self.api_id = api_id
        self.api_hash = api_hash.strip()
        self.flow = TelegramUserFlow(database)
        self.client: TelegramClient | None = None
        self.authorized = False
        self.pending_phone = ""
        self.pending_phone_code_hash = ""
        self.awaiting_password = False
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_id and self.api_hash)

    @property
    def connected(self) -> bool:
        return self.authorized

    @property
    def awaiting_code(self) -> bool:
        return bool(self.pending_phone and self.pending_phone_code_hash)

    def _fernet(self) -> Fernet:
        """Строит ключ шифрования из секретного API hash, не храня его в БД."""
        key_material = sha256(
            f"leadroom-telegram-session:{self.api_hash}".encode()
        ).digest()
        return Fernet(urlsafe_b64encode(key_material))

    async def start(self) -> None:
        if not self.configured:
            return
        account = self.database.get_telegram_account()
        if account is None:
            return
        try:
            session = self._fernet().decrypt(
                account["encrypted_session"].encode()
            ).decode()
            client = self._new_client(session)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                self.last_error = "Сессия Telegram больше не авторизована."
                return
            self._activate(client)
            self.authorized = True
            self.last_error = ""
        except (InvalidToken, TelegramUserError):
            self.last_error = "Не удалось расшифровать сессию Telegram."
        except Exception as error:
            self.last_error = f"Telegram не подключён: {error}"

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None
        self.authorized = False

    async def send_code(self, phone: str) -> None:
        self._require_configuration()
        clean_phone = phone.strip()
        if not clean_phone:
            raise TelegramUserError("Укажите номер Telegram-аккаунта.")
        await self.stop()
        client = self._new_client()
        try:
            await client.connect()
            sent_code = await client.send_code_request(clean_phone)
        except Exception as error:
            await client.disconnect()
            raise TelegramUserError(f"Не удалось отправить код: {error}") from error
        self.client = client
        self.authorized = False
        self.pending_phone = clean_phone
        self.pending_phone_code_hash = sent_code.phone_code_hash
        self.awaiting_password = False

    async def verify(self, code: str, password: str) -> None:
        if not self.client or not self.awaiting_code:
            raise TelegramUserError("Сначала запросите код Telegram.")
        try:
            if self.awaiting_password:
                if not password:
                    raise TelegramUserError("Введите пароль двухэтапной защиты.")
                await self.client.sign_in(password=password)
            else:
                if not code.strip():
                    raise TelegramUserError("Введите код из Telegram.")
                await self.client.sign_in(
                    phone=self.pending_phone,
                    code=code.strip(),
                    phone_code_hash=self.pending_phone_code_hash,
                )
        except SessionPasswordNeededError:
            self.awaiting_password = True
            if not password:
                raise TelegramUserError(
                    "Для аккаунта включена двухэтапная защита. Введите пароль."
                ) from None
            await self.client.sign_in(password=password)
        except TelegramUserError:
            raise
        except Exception as error:
            raise TelegramUserError(f"Не удалось войти: {error}") from error

        session = self.client.session.save()
        encrypted_session = self._fernet().encrypt(session.encode()).decode()
        self.database.save_telegram_account(self.pending_phone, encrypted_session)
        self.pending_phone = ""
        self.pending_phone_code_hash = ""
        self.awaiting_password = False
        self.last_error = ""
        self._activate(self.client)
        self.authorized = True

    async def disconnect_account(self) -> None:
        if self.client:
            try:
                await self.client.log_out()
            finally:
                await self.client.disconnect()
                self.client = None
        self.authorized = False
        self.database.delete_telegram_account()
        self.pending_phone = ""
        self.pending_phone_code_hash = ""
        self.awaiting_password = False

    def status(self) -> dict:
        account = self.database.get_telegram_account()
        return {
            "configured": self.configured,
            "connected": self.connected,
            "awaiting_code": self.awaiting_code,
            "awaiting_password": self.awaiting_password,
            "phone": account["phone"] if account else "",
            "last_error": self.last_error,
        }

    def _new_client(self, session: str = "") -> TelegramClient:
        assert self.api_id is not None
        return TelegramClient(StringSession(session), self.api_id, self.api_hash)

    def _activate(self, client: TelegramClient) -> None:
        client.add_event_handler(self._handle_message, events.NewMessage(incoming=True))
        self.client = client

    def _require_configuration(self) -> None:
        if not self.configured:
            raise TelegramUserError(
                "Заполните TELEGRAM_API_ID и TELEGRAM_API_HASH."
            )

    async def _handle_message(self, event) -> None:
        if not event.is_private or event.out:
            return
        sender = await event.get_sender()
        if sender is None or getattr(sender, "bot", False):
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
