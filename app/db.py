from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Iterator

from sqlalchemy import Column, DateTime, ForeignKey, LargeBinary, String, Table, Text, case, create_engine, func, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, selectinload


def tag_tone(_name: str) -> str:
    return "neutral"


DEFAULT_STATUS = "Новый"
ACTIVE_STATUS = "Активные"

WORKFLOW_STATUSES = [
    {"value": "Новый", "label": "Новые", "tone": "warning"},
    {"value": "В работе", "label": "В работе", "tone": "neutral"},
    {"value": "Успешно", "label": "Успешные", "tone": "success"},
    {"value": "Отказ", "label": "Отказы", "tone": "danger"},
]

LEAD_STATUSES = [
    *WORKFLOW_STATUSES,
    {"value": "Просрочен", "label": "Просроченные", "tone": "overdue"},
]

LEAD_SOURCES = [
    {"value": "manual", "label": "Вручную", "sources": ("manual",)},
    {
        "value": "telegram",
        "label": "Telegram",
        "sources": ("telegram_bot", "telegram_user"),
    },
]

_STATUS_TONES = {item["value"]: item["tone"] for item in LEAD_STATUSES}
_WORKFLOW_STATUS_VALUES = {item["value"] for item in WORKFLOW_STATUSES}
_OVERDUE_ELIGIBLE_STATUSES = {"Новый", "В работе"}


def status_tone(name: str) -> str:
    return _STATUS_TONES.get((name or "").strip(), "neutral")


class Base(DeclarativeBase):
    pass


lead_tags = Table(
    "lead_tags",
    Base.metadata,
    Column("lead_id", ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    contact: Mapped[str] = mapped_column(String(200))
    request_text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=DEFAULT_STATUS, server_default=DEFAULT_STATUS)
    deadline: Mapped[date | None] = mapped_column(nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    tags: Mapped[list["Tag"]] = relationship(secondary=lead_tags, back_populates="leads")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    leads: Mapped[list[Lead]] = relationship(secondary=lead_tags, back_populates="tags")


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"

    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TelegramUpdate(Base):
    __tablename__ = "telegram_updates"

    update_id: Mapped[int] = mapped_column(primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TelegramUserMessage(Base):
    __tablename__ = "telegram_user_messages"

    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[int] = mapped_column(primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        self._migrate_status_column()
        self._migrate_deadline_column()
        self._migrate_avatar_column()
        self._migrate_updated_at_column()

    def _migrate_updated_at_column(self) -> None:
        """Добавляет дату последнего изменения в уже созданную таблицу лидов."""
        inspector = inspect(self.engine)
        if "leads" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("leads")}
        if "updated_at" in columns:
            return
        column_type = "TIMESTAMP" if self.engine.dialect.name == "postgresql" else "DATETIME"
        with self.engine.begin() as connection:
            _ = connection.execute(text(f"ALTER TABLE leads ADD COLUMN updated_at {column_type}"))
            _ = connection.execute(
                text(
                    "UPDATE leads SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP) "
                    "WHERE updated_at IS NULL"
                )
            )

    def _migrate_deadline_column(self) -> None:
        """Добавляет срок и заполняет его для существующих просроченных лидов."""
        inspector = inspect(self.engine)
        if "leads" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("leads")}
        with self.engine.begin() as connection:
            if "deadline" not in columns:
                _ = connection.execute(text("ALTER TABLE leads ADD COLUMN deadline DATE"))
            _ = connection.execute(
                text(
                    "UPDATE leads SET deadline = :deadline "
                    "WHERE status = :status AND deadline IS NULL"
                ),
                {
                    "deadline": date.today() - timedelta(days=1),
                    "status": "Просрочен",
                },
            )
            _ = connection.execute(
                text(
                    "UPDATE leads SET status = :replacement "
                    "WHERE status = :overdue"
                ),
                {"replacement": "В работе", "overdue": "Просрочен"},
            )

    def _migrate_avatar_column(self) -> None:
        """Добавляет место для фото Telegram в уже созданную таблицу лидов."""
        inspector = inspect(self.engine)
        if "leads" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("leads")}
        if "avatar" in columns:
            return
        column_type = "BYTEA" if self.engine.dialect.name == "postgresql" else "BLOB"
        with self.engine.begin() as connection:
            _ = connection.execute(
                text(f"ALTER TABLE leads ADD COLUMN avatar {column_type}")
            )

    def _migrate_status_column(self) -> None:
        """Добавляет колонку status в уже существующую таблицу leads.

        Раньше статуса не было, а роль «Новый» играл одноимённый тег.
        Миграция выполняется один раз: после добавления колонки она больше
        не срабатывает.
        """
        inspector = inspect(self.engine)
        if "leads" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("leads")}
        if "status" in columns:
            return
        with self.engine.begin() as connection:
            _ = connection.execute(
                text(f"ALTER TABLE leads ADD COLUMN status VARCHAR(32) DEFAULT '{DEFAULT_STATUS}'")
            )
            _ = connection.execute(
                text("UPDATE leads SET status = :status WHERE status IS NULL"),
                {"status": DEFAULT_STATUS},
            )
            _ = connection.execute(
                text("DELETE FROM lead_tags WHERE tag_id IN (SELECT id FROM tags WHERE name = :name)"),
                {"name": DEFAULT_STATUS},
            )
            _ = connection.execute(
                text("DELETE FROM tags WHERE name = :name"),
                {"name": DEFAULT_STATUS},
            )

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def create_lead(
        self,
        *,
        name: str,
        contact: str,
        request_text: str,
        source: str,
        tags: list[str] | None = None,
        status: str = DEFAULT_STATUS,
        deadline: date | None = None,
        telegram_chat_id: str | None = None,
        avatar: bytes | None = None,
        session: Session | None = None,
    ) -> int:
        if session is None:
            with self.session() as own_session:
                return self.create_lead(
                    name=name,
                    contact=contact,
                    request_text=request_text,
                    source=source,
                    tags=tags,
                    status=status,
                    deadline=deadline,
                    telegram_chat_id=telegram_chat_id,
                    avatar=avatar,
                    session=own_session,
                )

        lead = Lead(
            name=name.strip(),
            contact=contact.strip(),
            request_text=request_text.strip(),
            source=source,
            status=self._normalize_status(status),
            deadline=deadline,
            telegram_chat_id=telegram_chat_id,
            avatar=avatar,
            updated_at=datetime.now(),
        )
        session.add(lead)
        for tag_name in tags or []:
            tag = self._get_or_create_tag(session, tag_name)
            if tag and tag not in lead.tags:
                lead.tags.append(tag)
        session.flush()
        return lead.id

    def record_telegram_user_message(
        self,
        *,
        chat_id: str,
        message_id: int,
        name: str,
        contact: str,
        text: str,
        avatar: bytes | None = None,
        continuation: bool = False,
    ) -> tuple[int, bool]:
        with self.session() as session:
            message_key = {"chat_id": chat_id, "message_id": message_id}
            if session.get(TelegramUserMessage, message_key):
                lead = session.scalar(
                    select(Lead)
                    .where(
                        Lead.source == "telegram_user",
                        Lead.telegram_chat_id == chat_id,
                    )
                    .order_by(Lead.id.desc())
                )
                return (lead.id if lead else 0), False

            lead = None
            if continuation:
                lead = session.scalar(
                    select(Lead)
                    .where(
                        Lead.source == "telegram_user",
                        Lead.telegram_chat_id == chat_id,
                    )
                    .order_by(Lead.id.desc())
                )
            created = lead is None
            if lead is None:
                lead_id = self.create_lead(
                    name=name,
                    contact=contact,
                    request_text=text,
                    source="telegram_user",
                    telegram_chat_id=chat_id,
                    avatar=avatar,
                    session=session,
                )
            else:
                lead.name = name.strip() or lead.name
                lead.contact = contact.strip() or lead.contact
                if avatar is not None:
                    lead.avatar = avatar
                lead.request_text = f"{lead.request_text}\n\n{text.strip()}"
                session.flush()
                lead_id = lead.id

            session.add(TelegramUserMessage(**message_key))
            return lead_id, created

    def update_lead(
        self,
        lead_id: int,
        *,
        name: str,
        contact: str,
        request_text: str,
        tags: list[str] | None = None,
        status: str | None = None,
        deadline: date | None = None,
    ) -> bool:
        with self.session() as session:
            lead = session.scalar(
                select(Lead).options(selectinload(Lead.tags)).where(Lead.id == lead_id)
            )
            if lead is None:
                return False
            lead.name = name.strip()
            lead.contact = contact.strip()
            lead.request_text = request_text.strip()
            if status is not None:
                lead.status = self._normalize_status(status)
            lead.deadline = deadline
            if tags is not None:
                lead.tags = []
                for tag_name in tags:
                    tag = self._get_or_create_tag(session, tag_name)
                    if tag and tag not in lead.tags:
                        lead.tags.append(tag)
            lead.updated_at = datetime.now()
            return True

    def delete_lead(self, lead_id: int) -> bool:
        with self.session() as session:
            lead = session.scalar(
                select(Lead).options(selectinload(Lead.tags)).where(Lead.id == lead_id)
            )
            if lead is None:
                return False
            lead.tags.clear()
            session.delete(lead)
            return True

    @staticmethod
    def _normalize_status(status: str | None) -> str:
        return status if status in _WORKFLOW_STATUS_VALUES else DEFAULT_STATUS

    @staticmethod
    def _effective_status(status: str, deadline: date | None) -> str:
        if (
            status in _OVERDUE_ELIGIBLE_STATUSES
            and deadline is not None
            and deadline < date.today()
        ):
            return "Просрочен"
        return status

    @staticmethod
    def _get_or_create_tag(session: Session, tag_name: str) -> Tag | None:
        clean_name = tag_name.strip()
        if not clean_name:
            return None
        tag = session.scalar(
            select(Tag).where(func.lower(Tag.name) == func.lower(clean_name))
        )
        if tag is None:
            tag = Tag(name=clean_name)
            session.add(tag)
            session.flush()
        return tag

    def add_tag(self, lead_id: int, tag_name: str) -> None:
        with self.session() as session:
            lead = session.get(Lead, lead_id)
            if lead is None:
                return
            tag = self._get_or_create_tag(session, tag_name)
            if tag and tag not in lead.tags:
                lead.tags.append(tag)
                lead.updated_at = datetime.now()

    def remove_tag(self, lead_id: int, tag_id: int) -> None:
        with self.session() as session:
            lead = session.scalar(select(Lead).options(selectinload(Lead.tags)).where(Lead.id == lead_id))
            if lead:
                remaining_tags = [tag for tag in lead.tags if tag.id != tag_id]
                if len(remaining_tags) != len(lead.tags):
                    lead.tags = remaining_tags
                    lead.updated_at = datetime.now()

    def delete_tag(self, tag_id: int) -> bool:
        with self.session() as session:
            tag = session.scalar(select(Tag).options(selectinload(Tag.leads)).where(Tag.id == tag_id))
            if tag is None:
                return False
            changed_at = datetime.now()
            for lead in tag.leads:
                lead.updated_at = changed_at
            tag.leads.clear()
            session.delete(tag)
            return True

    def list_tags(self) -> list[dict]:
        with self.session() as session:
            rows = session.execute(
                select(Tag.id, Tag.name, func.count(lead_tags.c.lead_id).label("lead_count"))
                .outerjoin(lead_tags, lead_tags.c.tag_id == Tag.id)
                .group_by(Tag.id, Tag.name)
                .order_by(func.lower(Tag.name))
            ).all()
            return [
                {**dict(row._mapping), "tone": tag_tone(row.name)}
                for row in rows
            ]

    def list_statuses(self) -> list[dict]:
        with self.session() as session:
            counts: dict[str, int] = {}
            active_count = 0
            for stored_status, deadline in session.execute(
                select(Lead.status, Lead.deadline)
            ).all():
                if stored_status in _OVERDUE_ELIGIBLE_STATUSES:
                    active_count += 1
                effective_status = self._effective_status(stored_status, deadline)
                counts[effective_status] = counts.get(effective_status, 0) + 1
            return [
                {
                    "value": ACTIVE_STATUS,
                    "label": ACTIVE_STATUS,
                    "tone": "active",
                    "lead_count": active_count,
                },
                *(
                    {**status, "lead_count": counts.get(status["value"], 0)}
                    for status in LEAD_STATUSES
                ),
            ]

    def list_sources(self) -> list[dict]:
        with self.session() as session:
            counts = dict(
                session.execute(
                    select(Lead.source, func.count(Lead.id)).group_by(Lead.source)
                ).all()
            )
            return [
                {
                    **source,
                    "lead_count": sum(counts.get(value, 0) for value in source["sources"]),
                }
                for source in LEAD_SOURCES
            ]

    def list_leads(
        self,
        tag_ids: list[int] | None = None,
        status: str | None = None,
        sources: list[str] | None = None,
        search: str = "",
        created_from: date | None = None,
        created_to: date | None = None,
        *,
        sort: str = "created",
        direction: str = "desc",
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        with self.session() as session:
            statement = self._filtered_leads_statement(tag_ids, status, sources, search, created_from, created_to).options(
                selectinload(Lead.tags)
            )
            descending = direction != "asc"
            direction_method = "desc" if descending else "asc"
            if sort == "updated":
                sort_column = Lead.updated_at
            elif sort == "deadline":
                sort_column = Lead.deadline
                statement = statement.order_by(
                    case((Lead.deadline.is_(None), 1), else_=0)
                )
            elif sort == "name":
                sort_column = func.lower(Lead.name)
            elif sort == "status":
                overdue = (
                    Lead.deadline.is_not(None)
                    & (Lead.deadline < date.today())
                    & Lead.status.in_(_OVERDUE_ELIGIBLE_STATUSES)
                )
                sort_column = case(
                    (overdue, 4),
                    (Lead.status == "Новый", 0),
                    (Lead.status == "В работе", 1),
                    (Lead.status == "Успешно", 2),
                    (Lead.status == "Отказ", 3),
                    else_=5,
                )
            else:
                sort_column = Lead.created_at
            ordered_column = getattr(sort_column, direction_method)()
            ordered_id = Lead.id.desc() if descending else Lead.id.asc()
            statement = statement.order_by(ordered_column, ordered_id)
            if limit is not None:
                statement = statement.limit(limit).offset(offset)
            return [self._lead_dict(lead) for lead in session.scalars(statement).all()]

    def count_leads(
        self,
        tag_ids: list[int] | None = None,
        status: str | None = None,
        sources: list[str] | None = None,
        search: str = "",
        created_from: date | None = None,
        created_to: date | None = None,
    ) -> int:
        with self.session() as session:
            statement = self._filtered_leads_statement(tag_ids, status, sources, search, created_from, created_to).with_only_columns(
                func.count(Lead.id)
            ).order_by(None)
            return session.scalar(statement) or 0

    @staticmethod
    def _filtered_leads_statement(
        tag_ids: list[int] | None, status: str | None, sources: list[str] | None = None,
        search: str = "", created_from: date | None = None, created_to: date | None = None,
    ):
        statement = select(Lead)
        selected_tag_ids = list(dict.fromkeys(tag_ids or []))
        if selected_tag_ids:
            matching_leads = (
                select(lead_tags.c.lead_id)
                .where(lead_tags.c.tag_id.in_(selected_tag_ids))
                .distinct()
            )
            statement = statement.where(Lead.id.in_(matching_leads))
        overdue = (
            Lead.deadline.is_not(None)
            & (Lead.deadline < date.today())
            & Lead.status.in_(_OVERDUE_ELIGIBLE_STATUSES)
        )
        if status == ACTIVE_STATUS:
            statement = statement.where(Lead.status.in_(_OVERDUE_ELIGIBLE_STATUSES))
        elif status == "Просрочен":
            statement = statement.where(overdue)
        elif status in _WORKFLOW_STATUS_VALUES:
            statement = statement.where(Lead.status == status)
            if status in _OVERDUE_ELIGIBLE_STATUSES:
                statement = statement.where(~overdue)
        selected_sources = list(dict.fromkeys(sources or []))
        source_values = {
            source_value
            for source in LEAD_SOURCES
            if source["value"] in selected_sources
            for source_value in source["sources"]
        }
        if source_values:
            statement = statement.where(Lead.source.in_(source_values))
        if search:
            pattern = f"%{search}%"
            statement = statement.where(
                Lead.name.ilike(pattern) | Lead.contact.ilike(pattern) | Lead.request_text.ilike(pattern)
            )
        if created_from:
            statement = statement.where(Lead.created_at >= datetime.combine(created_from, time.min))
        if created_to:
            statement = statement.where(
                Lead.created_at < datetime.combine(created_to + timedelta(days=1), time.min)
            )
        return statement

    def get_lead(self, lead_id: int) -> dict | None:
        with self.session() as session:
            lead = session.scalar(select(Lead).options(selectinload(Lead.tags)).where(Lead.id == lead_id))
            return self._lead_dict(lead) if lead else None

    def get_lead_avatar(self, lead_id: int) -> bytes | None:
        with self.session() as session:
            return session.scalar(select(Lead.avatar).where(Lead.id == lead_id))

    @staticmethod
    def _lead_dict(lead: Lead) -> dict:
        effective_status = Database._effective_status(lead.status, lead.deadline)
        return {
            "id": lead.id,
            "name": lead.name,
            "contact": lead.contact,
            "request_text": lead.request_text,
            "source": lead.source,
            "status": effective_status,
            "workflow_status": lead.status,
            "status_tone": status_tone(effective_status),
            "deadline": lead.deadline,
            "telegram_chat_id": lead.telegram_chat_id,
            "has_avatar": lead.avatar is not None,
            "created_at": lead.created_at,
            "updated_at": lead.updated_at,
            "tags": [
                {"id": tag.id, "name": tag.name, "tone": tag_tone(tag.name)}
                for tag in sorted(lead.tags, key=lambda item: item.name.lower())
            ],
        }
