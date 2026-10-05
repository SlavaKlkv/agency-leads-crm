from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
import secrets
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .db import DEFAULT_STATUS, LEAD_SOURCES, LEAD_STATUSES, WORKFLOW_STATUSES, Database
from .telegram import TelegramAPIError, TelegramClient, TelegramFlow
from .telegram_user import TelegramUserService


APP_DIR = Path(__file__).parent
DEFAULT_LEADS_PER_PAGE = 10
LEADS_PER_PAGE_OPTIONS = (5, 10)
LEAD_SORT_OPTIONS = (
    ("created", "По созданию"),
    ("updated", "По обновлению"),
    ("deadline", "По сроку"),
    ("name", "По имени"),
    ("status", "По этапу"),
)
LEAD_SORT_DIRECTIONS = {"asc", "desc"}
DEFAULT_LEAD_SORT = "created"
DEFAULT_LEAD_SORT_DIRECTION = "desc"


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    database = Database(app_settings.database_url)
    telegram_client = TelegramClient(app_settings.telegram_bot_token)
    telegram_flow = TelegramFlow(database)
    telegram_user = TelegramUserService(
        database,
        api_id=app_settings.telegram_api_id,
        api_hash=app_settings.telegram_api_hash,
        session=app_settings.telegram_session,
        enabled=app_settings.telegram_user_enabled,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        await telegram_user.start()
        try:
            yield
        finally:
            await telegram_user.stop()

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.database = database
    app.state.telegram_client = telegram_client
    app.state.telegram_flow = telegram_flow
    app.state.telegram_user = telegram_user

    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=APP_DIR / "templates")

    @app.get("/", response_class=HTMLResponse)
    async def leads(
        request: Request,
        tag: list[int] = Query(default=[]),
        lead_status: str | None = Query(default=None, alias="status"),
        source: list[str] = Query(default=[]),
        search: str = Query(default="", max_length=200),
        created_from: str = Query(default=""),
        created_to: str = Query(default=""),
        sort: str = Query(default=DEFAULT_LEAD_SORT),
        direction: str = Query(default=DEFAULT_LEAD_SORT_DIRECTION),
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=DEFAULT_LEADS_PER_PAGE),
    ):
        active_tags = list(dict.fromkeys(tag))
        active_status = (
            lead_status
            if any(item["value"] == lead_status for item in LEAD_STATUSES)
            else None
        )
        active_sources = list(
            dict.fromkeys(item for item in source if item in {source["value"] for source in LEAD_SOURCES})
        )
        active_per_page = (
            per_page if per_page in LEADS_PER_PAGE_OPTIONS else DEFAULT_LEADS_PER_PAGE
        )
        if sort in {"newest", "oldest"}:
            active_sort = DEFAULT_LEAD_SORT
            active_direction = "desc" if sort == "newest" else "asc"
        else:
            active_sort = sort if sort in dict(LEAD_SORT_OPTIONS) else DEFAULT_LEAD_SORT
            active_direction = (
                direction if direction in LEAD_SORT_DIRECTIONS else DEFAULT_LEAD_SORT_DIRECTION
            )
        active_search = search.strip()
        try:
            active_created_from = date.fromisoformat(created_from) if created_from else None
        except ValueError:
            active_created_from = None
        try:
            active_created_to = date.fromisoformat(created_to) if created_to else None
        except ValueError:
            active_created_to = None
        resettable_filter_count = (
            len(active_tags)
            + len(active_sources)
            + bool(active_search)
            + bool(active_created_from)
            + bool(active_created_to)
        )
        filter_count = (
            resettable_filter_count
            + bool(active_status)
            + (active_sort != DEFAULT_LEAD_SORT or active_direction != DEFAULT_LEAD_SORT_DIRECTION)
        )
        total_leads = database.count_leads(
            active_tags, active_status, active_sources, active_search, active_created_from, active_created_to
        )
        total_pages = max(1, (total_leads + active_per_page - 1) // active_per_page)
        current_page = min(page, total_pages)

        pagination_pages: list[int | None] = [1]
        middle_first = 2
        middle_last = total_pages - 1
        window_size = 3
        if middle_first <= middle_last:
            window_start = min(
                max(current_page - 1, middle_first),
                max(middle_first, middle_last - window_size + 1),
            )
            window_end = min(window_start + window_size - 1, middle_last)
            if window_start > middle_first:
                pagination_pages.append(None)
            pagination_pages.extend(range(window_start, window_end + 1))
            if window_end < middle_last:
                pagination_pages.append(None)
        if total_pages > 1:
            pagination_pages.append(total_pages)

        def page_url(target_page: int) -> str:
            query: list[tuple[str, str | int]] = [("tag", tag_id) for tag_id in active_tags]
            if active_status is not None:
                query.append(("status", active_status))
            query.extend(("source", source) for source in active_sources)
            if active_search:
                query.append(("search", active_search))
            if active_created_from:
                query.append(("created_from", active_created_from.isoformat()))
            if active_created_to:
                query.append(("created_to", active_created_to.isoformat()))
            if active_sort != DEFAULT_LEAD_SORT:
                query.append(("sort", active_sort))
            if active_direction != DEFAULT_LEAD_SORT_DIRECTION:
                query.append(("direction", active_direction))
            if active_per_page != DEFAULT_LEADS_PER_PAGE:
                query.append(("per_page", active_per_page))
            query.append(("page", target_page))
            return f"/?{urlencode(query)}"

        return templates.TemplateResponse(
            request,
            "leads.html",
            {
                "leads": database.list_leads(
                    active_tags,
                    active_status,
                    active_sources,
                    active_search,
                    active_created_from,
                    active_created_to,
                    sort=active_sort,
                    direction=active_direction,
                    limit=active_per_page,
                    offset=(current_page - 1) * active_per_page,
                ),
                "total_leads": total_leads,
                "current_page": current_page,
                "total_pages": total_pages,
                "pagination_pages": pagination_pages,
                "page_start": (current_page - 1) * active_per_page + 1 if total_leads else 0,
                "page_end": min(current_page * active_per_page, total_leads),
                "page_url": page_url,
                "active_per_page": active_per_page,
                "per_page_options": LEADS_PER_PAGE_OPTIONS,
                "tags": database.list_tags(),
                "sources": database.list_sources(),
                "statuses": database.list_statuses(),
                "active_tags": active_tags,
                "active_status": active_status,
                "active_sources": active_sources,
                "active_search": active_search,
                "active_created_from": active_created_from.isoformat() if active_created_from else "",
                "active_created_to": active_created_to.isoformat() if active_created_to else "",
                "show_reset_filters": filter_count >= 2,
                "active_sort": active_sort,
                "active_direction": active_direction,
                "inverse_sort_direction": "asc" if active_direction == "desc" else "desc",
                "sort_options": LEAD_SORT_OPTIONS,
                "app_name": app_settings.app_name,
            },
        )

    @app.get("/leads/new", response_class=HTMLResponse)
    async def new_lead(request: Request):
        return templates.TemplateResponse(
            request,
            "new_lead.html",
            {
                "app_name": app_settings.app_name,
                "statuses": WORKFLOW_STATUSES,
                "default_status": DEFAULT_STATUS,
            },
        )

    @app.post("/leads")
    async def create_lead(
        name: str = Form(min_length=1, max_length=120),
        contact: str = Form(min_length=1, max_length=200),
        request_text: str = Form(min_length=1, max_length=4000),
        tags: str = Form(default=""),
        lead_status: str = Form(default=DEFAULT_STATUS, alias="status"),
        deadline: date | None = Form(default=None),
    ):
        tag_names = [item.strip() for item in tags.split(",") if item.strip()]
        lead_id = database.create_lead(
            name=name,
            contact=contact,
            request_text=request_text,
            source="manual",
            tags=tag_names,
            status=lead_status,
            deadline=deadline,
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

    @app.get("/leads/{lead_id}/avatar")
    async def lead_avatar(lead_id: int):
        avatar = database.get_lead_avatar(lead_id)
        if avatar is None:
            raise HTTPException(status_code=404, detail="Аватар не найден")
        return Response(
            content=avatar,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, no-cache"},
        )

    @app.get("/leads/{lead_id}/edit", response_class=HTMLResponse)
    async def edit_lead(request: Request, lead_id: int):
        lead = database.get_lead(lead_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Лид не найден")
        tags_text = ", ".join(tag["name"] for tag in lead["tags"])
        return templates.TemplateResponse(
            request,
            "edit_lead.html",
            {
                "lead": lead,
                "tags_text": tags_text,
                "app_name": app_settings.app_name,
                "statuses": WORKFLOW_STATUSES,
                "default_status": DEFAULT_STATUS,
            },
        )

    @app.post("/leads/{lead_id}")
    async def update_lead(
        lead_id: int,
        name: str = Form(min_length=1, max_length=120),
        contact: str = Form(min_length=1, max_length=200),
        request_text: str = Form(min_length=1, max_length=4000),
        tags: str = Form(default=""),
        lead_status: str = Form(default=DEFAULT_STATUS, alias="status"),
        deadline: date | None = Form(default=None),
    ):
        tag_names = [item.strip() for item in tags.split(",") if item.strip()]
        updated = database.update_lead(
            lead_id,
            name=name,
            contact=contact,
            request_text=request_text,
            tags=tag_names,
            status=lead_status,
            deadline=deadline,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Лид не найден")
        return RedirectResponse(f"/leads/{lead_id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/leads/{lead_id}/delete")
    async def delete_lead(lead_id: int, return_to: str = Form(default="/")):
        if not database.delete_lead(lead_id):
            raise HTTPException(status_code=404, detail="Лид не найден")
        safe_return_to = return_to if return_to.startswith("/") and not return_to.startswith("//") else "/"
        return RedirectResponse(safe_return_to, status_code=status.HTTP_303_SEE_OTHER)

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

    @app.post("/tags/{tag_id}/delete")
    async def delete_tag(tag_id: int):
        if not database.delete_tag(tag_id):
            raise HTTPException(status_code=404, detail="Тег не найден")
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

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
            "telegram_user_enabled": app_settings.telegram_user_enabled,
            "telegram_user_configured": telegram_user.configured,
            "telegram_user_connected": telegram_user.connected,
        }

    return app


app = create_app()
