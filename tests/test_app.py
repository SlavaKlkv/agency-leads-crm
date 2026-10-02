from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.telegram import TelegramAPIError, raise_for_telegram_error


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        telegram_webhook_secret="test-secret",
    )
    return TestClient(create_app(settings))


def telegram_update(update_id: int, chat_id: int, text: str | None = None, contact: str | None = None):
    message: dict = {"chat": {"id": chat_id}}
    if text is not None:
        message["text"] = text
    if contact is not None:
        message["contact"] = {"phone_number": contact}
    return {"update_id": update_id, "message": message}


def test_manual_lead_tags_and_filter(tmp_path: Path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/leads",
            data={
                "name": "Анна",
                "contact": "anna@example.com",
                "request_text": "Нужен лендинг",
                "tags": "Новый, Сайт",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert "Анна" in detail.text
        assert "Сайт" in detail.text

        tags = client.app.state.database.list_tags()
        site_tag = next(tag for tag in tags if tag["name"] == "Сайт")
        filtered = client.get(f"/?tag={site_tag['id']}")
        assert "Анна" in filtered.text


def test_telegram_dialog_creates_one_tagged_lead(tmp_path: Path):
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
    with make_client(tmp_path) as client:
        updates = [
            telegram_update(1, 777, "/start"),
            telegram_update(2, 777, "Иван"),
            telegram_update(3, 777, contact="+79990000000"),
            telegram_update(4, 777, "Нужна настройка рекламы"),
        ]
        for update in updates:
            assert client.post("/api/telegram/webhook", json=update, headers=headers).status_code == 200

        leads = client.app.state.database.list_leads()
        assert len(leads) == 1
        assert leads[0]["name"] == "Иван"
        assert leads[0]["source"] == "telegram_bot"
        assert {tag["name"] for tag in leads[0]["tags"]} == {"Telegram", "Новый"}

        # Повторная доставка webhook не должна создавать дубль.
        assert client.post("/api/telegram/webhook", json=updates[-1], headers=headers).status_code == 200
        assert len(client.app.state.database.list_leads()) == 1


def test_telegram_webhook_rejects_wrong_secret(tmp_path: Path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/telegram/webhook",
            json=telegram_update(1, 1, "/start"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert response.status_code == 403


def test_telegram_api_error_does_not_expose_bot_token():
    token = "123456789:secret-token"
    request = httpx.Request("POST", f"https://api.telegram.org/bot{token}/setWebhook")
    response = httpx.Response(
        400,
        json={"ok": False, "description": "Bad Request: invalid webhook"},
        request=request,
    )

    with pytest.raises(TelegramAPIError) as captured:
        raise_for_telegram_error(response)

    assert token not in str(captured.value)
    assert str(captured.value) == (
        "Telegram API returned HTTP 400: Bad Request: invalid webhook"
    )
