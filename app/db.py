"""Async database layer (SQLAlchemy 2.0).

PostgreSQL in production (asyncpg), SQLite for local runs and tests (aiosqlite).
The ORM stores one row per event, keyed by event_id for idempotent ingest.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(64), index=True)
    camera_id: Mapped[str] = mapped_column(String(64))
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    zone_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dwell_ms: Mapped[int] = mapped_column(Integer, default=0)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    queue_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sku_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_seq: Mapped[int] = mapped_column(Integer, default=0)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# composite index supporting the common "store + time-window" scans
Index("ix_events_store_ts", EventRow.store_id, EventRow.timestamp)

_engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


def get_engine():
    return _engine


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """FastAPI dependency yielding a session; rolls back on error."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
