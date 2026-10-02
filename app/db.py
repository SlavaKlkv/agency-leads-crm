from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, Text, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, selectinload


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
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
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


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)

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
        telegram_chat_id: str | None = None,
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
                    telegram_chat_id=telegram_chat_id,
                    session=own_session,
                )

        lead = Lead(
            name=name.strip(),
            contact=contact.strip(),
            request_text=request_text.strip(),
            source=source,
            telegram_chat_id=telegram_chat_id,
        )
        session.add(lead)
        for tag_name in tags or []:
            tag = self._get_or_create_tag(session, tag_name)
            if tag and tag not in lead.tags:
                lead.tags.append(tag)
        session.flush()
        return lead.id

    @staticmethod
    def _get_or_create_tag(session: Session, tag_name: str) -> Tag | None:
        clean_name = tag_name.strip()
        if not clean_name:
            return None
        tag = session.scalar(select(Tag).where(func.lower(Tag.name) == clean_name.lower()))
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

    def remove_tag(self, lead_id: int, tag_id: int) -> None:
        with self.session() as session:
            lead = session.scalar(select(Lead).options(selectinload(Lead.tags)).where(Lead.id == lead_id))
            if lead:
                lead.tags = [tag for tag in lead.tags if tag.id != tag_id]

    def list_tags(self) -> list[dict]:
        with self.session() as session:
            rows = session.execute(
                select(Tag.id, Tag.name, func.count(lead_tags.c.lead_id).label("lead_count"))
                .outerjoin(lead_tags, lead_tags.c.tag_id == Tag.id)
                .group_by(Tag.id, Tag.name)
                .order_by(func.lower(Tag.name))
            ).all()
            return [dict(row._mapping) for row in rows]

    def list_leads(self, tag_id: int | None = None) -> list[dict]:
        with self.session() as session:
            statement = select(Lead).options(selectinload(Lead.tags))
            if tag_id is not None:
                statement = statement.join(lead_tags).where(lead_tags.c.tag_id == tag_id)
            statement = statement.order_by(Lead.created_at.desc(), Lead.id.desc())
            return [self._lead_dict(lead) for lead in session.scalars(statement).all()]

    def get_lead(self, lead_id: int) -> dict | None:
        with self.session() as session:
            lead = session.scalar(select(Lead).options(selectinload(Lead.tags)).where(Lead.id == lead_id))
            return self._lead_dict(lead) if lead else None

    @staticmethod
    def _lead_dict(lead: Lead) -> dict:
        return {
            "id": lead.id,
            "name": lead.name,
            "contact": lead.contact,
            "request_text": lead.request_text,
            "source": lead.source,
            "telegram_chat_id": lead.telegram_chat_id,
            "created_at": lead.created_at,
            "tags": [
                {"id": tag.id, "name": tag.name}
                for tag in sorted(lead.tags, key=lambda item: item.name.lower())
            ],
        }
