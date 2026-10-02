from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import secrets

from fastapi import FastAPI, Form, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .db import Database
from .telegram import TelegramAPIError, TelegramClient, TelegramFlow


APP_DIR = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    database = Database(app_settings.database_url)
    telegram_client = TelegramClient(app_settings.telegram_bot_token)
    telegram_flow = TelegramFlow(database)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        yield

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.database = database
    app.state.telegram_client = telegram_client
    app.state.telegram_flow = telegram_flow

    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=APP_DIR / "templates")

    @app.get("/", response_class=HTMLResponse)
    async def leads(request: Request, tag: int | None = None):
        return templates.TemplateResponse(
            request,
            "leads.html",
            {
                "leads": database.list_leads(tag),
                "tags": database.list_tags(),
                "active_tag": tag,
                "app_name": app_settings.app_name,
            },
        )

    @app.get("/leads/new", response_class=HTMLResponse)
    async def new_lead(request: Request):
        return templates.TemplateResponse(
            request,
            "new_lead.html",
            {"app_name": app_settings.app_name},
        )

    @app.post("/leads")
    async def create_lead(
        name: str = Form(min_length=1, max_length=120),
        contact: str = Form(min_length=1, max_length=200),
        request_text: str = Form(min_length=1, max_length=4000),
        tags: str = Form(default="Новый"),
    ):
        tag_names = [item.strip() for item in tags.split(",") if item.strip()]
        lead_id = database.create_lead(
            name=name,
            contact=contact,
            request_text=request_text,
            source="manual",
            tags=tag_names,
        )
        return RedirectResponse(f"/leads/{lead_id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/leads/{lead_id}", response_class=HTMLResponse)
    async def lead_detail(request: Request, lead_id: int):
        lead = database.get_lead(lead_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Лид не найден")
        return templates.TemplateResponse(
            request,
            "lead_detail.html",
            {"lead": lead, "app_name": app_settings.app_name},
        )

    @app.post("/leads/{lead_id}/tags")
    async def add_tag(lead_id: int, tag: str = Form(min_length=1, max_length=80)):
        if not database.get_lead(lead_id):
            raise HTTPException(status_code=404, detail="Лид не найден")
        database.add_tag(lead_id, tag)
        return RedirectResponse(f"/leads/{lead_id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/leads/{lead_id}/tags/{tag_id}/delete")
    async def remove_tag(lead_id: int, tag_id: int):
        database.remove_tag(lead_id, tag_id)
        return RedirectResponse(f"/leads/{lead_id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/api/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ):
        expected = app_settings.telegram_webhook_secret
        if expected and not secrets.compare_digest(
            x_telegram_bot_api_secret_token or "", expected
        ):
            raise HTTPException(status_code=403, detail="Неверный секрет webhook")
        update = await request.json()
        reply = telegram_flow.process(update)
        if reply:
            await telegram_client.send(reply)
        return {"ok": True}

    @app.post("/api/telegram/setup")
    async def setup_telegram():
        if not app_settings.public_base_url or not app_settings.telegram_webhook_secret:
            raise HTTPException(
                status_code=503,
                detail="Нужны PUBLIC_BASE_URL и TELEGRAM_WEBHOOK_SECRET",
            )
        try:
            return await telegram_client.set_webhook(
                app_settings.public_base_url, app_settings.telegram_webhook_secret
            )
        except TelegramAPIError as error:
            raise HTTPException(status_code=502, detail=str(error)) from None

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "bot_configured": bool(app_settings.telegram_bot_token),
            "public_url_configured": bool(app_settings.public_base_url),
        }

    return app


app = create_app()
