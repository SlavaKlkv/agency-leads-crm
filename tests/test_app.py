import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.telegram import TelegramAPIError, raise_for_telegram_error
from app.telegram_user import IncomingTelegramMessage, TelegramUserFlow, TelegramUserService


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
                "tags": "Сайт",
                "status": "Успешно",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert "Анна" in detail.text
        assert "Сайт" in detail.text
        assert 'class="status status-success">Успешно' in detail.text
        assert 'class="tag tag-neutral tag-remove"' in detail.text
        assert client.app.state.database.get_lead(1)["status"] == "Успешно"

        client.app.state.database.add_tag(1, "Просрочен")
        detail = client.get(response.headers["location"])
        overdue_tag = next(
            tag
            for tag in client.app.state.database.get_lead(1)["tags"]
            if tag["name"] == "Просрочен"
        )
        assert overdue_tag["tone"] == "neutral"
        assert detail.text.count('class="tag tag-neutral tag-remove"') == 2

        tags = client.app.state.database.list_tags()
        site_tag = next(tag for tag in tags if tag["name"] == "Сайт")
        overdue_tag = next(tag for tag in tags if tag["name"] == "Просрочен")
        filtered = client.get(f"/?tag={site_tag['id']}")
        assert "Анна" in filtered.text
        assert 'class="source source-manual"' in filtered.text
        assert f'name="tag" value="{site_tag["id"]}" checked' in filtered.text
        assert "Теги · 1" in filtered.text
        assert "Статус" not in filtered.text

        second_lead = client.post(
            "/leads",
            data={
                "name": "Борис",
                "contact": "boris@example.com",
                "request_text": "Нужна реклама",
                "tags": "Сайт",
            },
            follow_redirects=False,
        )
        assert second_lead.status_code == 303

        multi_filtered = client.get(
            f"/?tag={site_tag['id']}&tag={overdue_tag['id']}"
        )
        assert "Анна" in multi_filtered.text
        assert "Борис" not in multi_filtered.text
        assert "Теги · 2" in multi_filtered.text
        assert f'name="tag" value="{site_tag["id"]}" checked' in multi_filtered.text
        assert f'name="tag" value="{overdue_tag["id"]}" checked' in multi_filtered.text

        status_filtered = client.get(f"/?tag={site_tag['id']}&status=Успешно")
        assert "Анна" in status_filtered.text
        assert 'class="filter status-filter status-filter-success active"' in status_filtered.text

        empty_filter = client.get(f"/?tag={site_tag['id']}&status=Отказ")
        assert "Анна" not in empty_filter.text

        deleted = client.post(f"/tags/{site_tag['id']}/delete", follow_redirects=False)
        assert deleted.status_code == 303
        assert deleted.headers["location"] == "/"
        assert "Сайт" not in {tag["name"] for tag in client.app.state.database.list_tags()}
        assert "Сайт" not in {
            tag["name"] for tag in client.app.state.database.get_lead(1)["tags"]
        }


def test_delete_missing_tag_returns_not_found(tmp_path: Path):
    with make_client(tmp_path) as client:
        response = client.post("/tags/999/delete")

        assert response.status_code == 404


def test_list_shows_quick_edit_and_delete_actions(tmp_path: Path):
    with make_client(tmp_path) as client:
        created = client.post(
            "/leads",
            data={
                "name": "Анна",
                "contact": "anna@example.com",
                "request_text": "Нужен лендинг",
                "tags": "Сайт",
                "status": "Новый",
            },
            follow_redirects=False,
        )
        lead_id = int(created.headers["location"].rsplit("/", 1)[-1])

        page = client.get("/")
        assert page.status_code == 200
        assert f'href="/leads/{lead_id}/edit"' in page.text
        assert f'action="/leads/{lead_id}/delete"' in page.text
        assert "data-delete-lead" in page.text
        assert 'id="delete-lead-dialog"' in page.text
        assert 'class="status status-warning">Новый' in page.text


def test_edit_lead_updates_fields_and_tags(tmp_path: Path):
    with make_client(tmp_path) as client:
        created = client.post(
            "/leads",
            data={
                "name": "Анна",
                "contact": "anna@example.com",
                "request_text": "Нужен лендинг",
                "tags": "Сайт",
                "status": "Новый",
            },
            follow_redirects=False,
        )
        lead_id = int(created.headers["location"].rsplit("/", 1)[-1])

        edit_form = client.get(f"/leads/{lead_id}/edit")
        assert edit_form.status_code == 200
        assert "Нужен лендинг" in edit_form.text
        assert "Сайт" in edit_form.text
        assert '<select name="status">' in edit_form.text
        assert 'value="Новый" selected' in edit_form.text

        updated = client.post(
            f"/leads/{lead_id}",
            data={
                "name": "Анна П.",
                "contact": "+79990000000",
                "request_text": "Нужен интернет-магазин",
                "tags": "Реклама",
                "status": "Отказ",
            },
            follow_redirects=False,
        )
        assert updated.status_code == 303
        assert updated.headers["location"] == f"/leads/{lead_id}"

        lead = client.app.state.database.get_lead(lead_id)
        assert lead["name"] == "Анна П."
        assert lead["contact"] == "+79990000000"
        assert lead["request_text"] == "Нужен интернет-магазин"
        assert lead["status"] == "Отказ"
        assert {tag["name"] for tag in lead["tags"]} == {"Реклама"}


def test_edit_missing_lead_returns_not_found(tmp_path: Path):
    with make_client(tmp_path) as client:
        assert client.get("/leads/999/edit").status_code == 404
        assert (
            client.post(
                "/leads/999",
                data={"name": "X", "contact": "y", "request_text": "z", "tags": ""},
            ).status_code
            == 404
        )


def test_delete_lead_removes_it(tmp_path: Path):
    with make_client(tmp_path) as client:
        created = client.post(
            "/leads",
            data={
                "name": "Анна",
                "contact": "anna@example.com",
                "request_text": "Нужен лендинг",
                "tags": "Сайт",
                "status": "Новый",
            },
            follow_redirects=False,
        )
        lead_id = int(created.headers["location"].rsplit("/", 1)[-1])

        deleted = client.post(f"/leads/{lead_id}/delete", follow_redirects=False)
        assert deleted.status_code == 303
        assert deleted.headers["location"] == "/"
        assert client.app.state.database.get_lead(lead_id) is None
        assert client.app.state.database.list_leads() == []

        assert client.post(f"/leads/{lead_id}/delete").status_code == 404


def test_telegram_dialog_creates_lead_without_automatic_tag(tmp_path: Path):
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
        assert leads[0]["tags"] == []
        assert leads[0]["status"] == "Новый"

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


def test_personal_telegram_messages_create_and_extend_one_lead(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'telegram-user.db'}")
    database.initialize()
    flow = TelegramUserFlow(database)

    lead_id, created = flow.process(
        IncomingTelegramMessage(
            chat_id="321",
            message_id=10,
            name="Анна Петрова",
            username="anna",
            text="Нужен лендинг",
            avatar=b"telegram-avatar",
        )
    )
    assert created is True

    same_lead_id, created = flow.process(
        IncomingTelegramMessage(
            chat_id="321",
            message_id=11,
            name="Анна Петрова",
            username="anna",
            text="Срок — две недели",
        )
    )

    assert created is False
    assert same_lead_id == lead_id
    lead = database.get_lead(lead_id)
    assert lead is not None
    assert lead["source"] == "telegram_user"
    assert lead["contact"] == "@anna"
    assert lead["request_text"] == "Нужен лендинг\n\nСрок — две недели"
    assert lead["tags"] == []
    assert lead["has_avatar"] is True


def test_telegram_avatar_is_rendered_and_served(tmp_path: Path):
    with make_client(tmp_path) as client:
        lead_id, _ = client.app.state.database.record_telegram_user_message(
            chat_id="321",
            message_id=10,
            name="Анна",
            contact="@anna",
            text="Нужен лендинг",
            avatar=b"telegram-avatar",
        )

        lead_list = client.get("/")
        detail = client.get(f"/leads/{lead_id}")
        avatar = client.get(f"/leads/{lead_id}/avatar")

        expected_image = f'src="/leads/{lead_id}/avatar"'
        assert expected_image in lead_list.text
        assert expected_image in detail.text
        assert avatar.status_code == 200
        assert avatar.headers["content-type"] == "image/jpeg"
        assert avatar.content == b"telegram-avatar"


def test_personal_telegram_duplicate_message_is_ignored(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'telegram-duplicate.db'}")
    database.initialize()
    flow = TelegramUserFlow(database)
    message = IncomingTelegramMessage(
        chat_id="654",
        message_id=20,
        name="Иван",
        username=None,
        text="Нужна реклама",
    )

    first = flow.process(message)
    second = flow.process(message)

    assert first[1] is True
    assert second == (first[0], False)
    assert len(database.list_leads()) == 1
    assert database.get_lead(first[0])["request_text"] == "Нужна реклама"


def test_telegram_user_service_does_not_mark_sent_code_as_connected(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'telegram-service.db'}")
    database.initialize()
    service = TelegramUserService(database, api_id=123, api_hash="test-hash")

    service.pending_phone = "+79990000000"
    service.pending_phone_code_hash = "pending-code"

    assert service.awaiting_code is True
    assert service.connected is False


def test_telegram_page_requires_admin_password_on_public_service(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'telegram-admin.db'}",
        public_base_url="https://leadroom.example",
        telegram_admin_password="correct-password",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/telegram").status_code == 401
        assert client.get("/telegram", auth=("admin", "wrong-password")).status_code == 401
        assert client.get("/telegram", auth=("admin", "correct-password")).status_code == 200


def test_public_telegram_page_is_disabled_without_admin_password(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'telegram-admin-missing.db'}",
        public_base_url="https://leadroom.example",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/telegram").status_code == 503


def test_telegram_service_account_message_is_ignored(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'telegram-service-account.db'}")
    database.initialize()
    service = TelegramUserService(database, api_id=123, api_hash="test-hash")
    sender = SimpleNamespace(id=777000, bot=False)
    event = SimpleNamespace(
        is_private=True,
        out=False,
        chat_id=777000,
        message=SimpleNamespace(id=10),
        get_sender=AsyncMock(return_value=sender),
    )

    asyncio.run(service._handle_message(event))

    assert database.list_leads() == []


def test_telegram_handler_downloads_sender_avatar(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'telegram-handler.db'}")
    database.initialize()
    service = TelegramUserService(database, api_id=123, api_hash="test-hash")
    sender = SimpleNamespace(
        id=123456,
        bot=False,
        first_name="Анна",
        last_name="Петрова",
        username="anna",
        photo=object(),
    )
    service.client = SimpleNamespace(
        download_profile_photo=AsyncMock(return_value=b"telegram-avatar")
    )
    event = SimpleNamespace(
        is_private=True,
        out=False,
        chat_id=321,
        message=SimpleNamespace(
            id=10,
            raw_text="Нужен лендинг",
            photo=None,
            voice=None,
            video=None,
            sticker=None,
            document=None,
        ),
        get_sender=AsyncMock(return_value=sender),
    )

    asyncio.run(service._handle_message(event))

    lead = database.list_leads()[0]
    assert lead["name"] == "Анна Петрова"
    assert lead["has_avatar"] is True
    assert database.get_lead_avatar(lead["id"]) == b"telegram-avatar"
    service.client.download_profile_photo.assert_awaited_once_with(sender, file=bytes)


def test_initialize_migrates_status_column(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    url = f"sqlite:///{db_path}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE leads (id INTEGER PRIMARY KEY, name VARCHAR(120), "
                "contact VARCHAR(200), request_text TEXT, source VARCHAR(32), "
                "telegram_chat_id VARCHAR(64), created_at DATETIME)"
            )
        )
        connection.execute(text("CREATE TABLE tags (id INTEGER PRIMARY KEY, name VARCHAR(80))"))
        connection.execute(
            text(
                "CREATE TABLE lead_tags (lead_id INTEGER, tag_id INTEGER, "
                "PRIMARY KEY (lead_id, tag_id))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO leads (id, name, contact, request_text, source) "
                "VALUES (1, 'Анна', 'a@b.c', 'Лендинг', 'manual')"
            )
        )
        connection.execute(text("INSERT INTO tags (id, name) VALUES (1, 'Новый')"))
        connection.execute(text("INSERT INTO lead_tags (lead_id, tag_id) VALUES (1, 1)"))
    engine.dispose()

    database = Database(url)
    database.initialize()

    lead = database.get_lead(1)
    assert lead is not None
    assert lead["status"] == "Новый"
    # Авто-тег «Новый» перенесён в статус и больше не дублируется.
    assert database.list_tags() == []


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
