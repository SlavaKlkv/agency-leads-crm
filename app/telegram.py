from __future__ import annotations

from dataclasses import dataclass
import json

import httpx

from .db import Database, TelegramSession, TelegramUpdate


@dataclass(frozen=True)
class BotReply:
    chat_id: str
    text: str
    reply_markup: dict | None = None


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.token = token

    async def send(self, reply: BotReply) -> None:
        if not self.token:
            return
        payload: dict[str, object] = {"chat_id": reply.chat_id, "text": reply.text}
        if reply.reply_markup:
            payload["reply_markup"] = reply.reply_markup
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json=payload,
            )
            response.raise_for_status()

    async def set_webhook(self, base_url: str, secret: str) -> dict:
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
        payload = {
            "url": f"{base_url}/api/telegram/webhook",
            "secret_token": secret,
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self.token}/setWebhook",
                json=payload,
            )
            response.raise_for_status()
            return response.json()


class TelegramFlow:
    def __init__(self, database: Database) -> None:
        self.database = database

    def process(self, update: dict) -> BotReply | None:
        update_id = update.get("update_id")
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not chat_id or update_id is None:
            return None

        text = str(message.get("text") or "").strip()
        contact_payload = message.get("contact") or {}

        with self.database.session() as session:
            if session.get(TelegramUpdate, update_id):
                return None

            conversation = session.get(TelegramSession, chat_id)

            if text.lower() in {"/start", "/new", "начать заново"} or conversation is None:
                if conversation is None:
                    conversation = TelegramSession(chat_id=chat_id, state="awaiting_name")
                    session.add(conversation)
                else:
                    conversation.state = "awaiting_name"
                    conversation.name = None
                    conversation.contact = None
                reply = BotReply(chat_id, "Здравствуйте! Как вас зовут?")
            elif conversation.state == "awaiting_name":
                if not text:
                    reply = BotReply(chat_id, "Напишите, пожалуйста, ваше имя текстом.")
                else:
                    conversation.state = "awaiting_contact"
                    conversation.name = text[:120]
                    reply = BotReply(
                        chat_id,
                        "Оставьте телефон, email или Telegram для связи.",
                        {
                            "keyboard": [[{"text": "Поделиться телефоном", "request_contact": True}]],
                            "resize_keyboard": True,
                            "one_time_keyboard": True,
                        },
                    )
            elif conversation.state == "awaiting_contact":
                contact = str(contact_payload.get("phone_number") or text).strip()
                if not contact:
                    reply = BotReply(chat_id, "Пришлите контакт текстом или кнопкой ниже.")
                else:
                    conversation.state = "awaiting_request"
                    conversation.contact = contact[:200]
                    reply = BotReply(
                        chat_id,
                        "Коротко опишите задачу, с которой нужна помощь.",
                        {"remove_keyboard": True},
                    )
            else:
                if not text:
                    reply = BotReply(chat_id, "Опишите задачу текстовым сообщением.")
                else:
                    lead_id = self.database.create_lead(
                        name=conversation.name or "Без имени",
                        contact=conversation.contact or "Не указан",
                        request_text=text[:4000],
                        source="telegram_bot",
                        tags=["Telegram", "Новый"],
                        telegram_chat_id=chat_id,
                        session=session,
                    )
                    session.delete(conversation)
                    reply = BotReply(
                        chat_id,
                        f"Спасибо! Заявка #{lead_id} создана. Мы свяжемся с вами.",
                    )

            session.add(TelegramUpdate(update_id=update_id))
            return reply


def format_update(update: dict) -> str:
    return json.dumps(update, ensure_ascii=False, separators=(",", ":"))
