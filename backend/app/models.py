"""ORM models aligned with PRD v1.1 (`tasks`, `crawl_results`)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.types import CHAR, TypeDecorator

Base = declarative_base()

TASK_STATUSES = (
    "pending",
    "crawling",
    "cleaning",
    "ai_processing",
    "completed",
    "failed",
    "stopped",
)
TASK_MODES = ("single", "site")


class GUID(TypeDecorator):
    """UUID column that stores native UUID on PostgreSQL and CHAR(36) on SQLite."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return parsed
        return str(parsed)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


JSONType = JSON().with_variant(JSONB, "postgresql")


def _utcnow():
    return datetime.datetime.utcnow()


class Task(Base):
    __tablename__ = "tasks"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    owner_id = Column(String(128), nullable=False, default="local")
    target_url = Column(String(2048), nullable=False)
    mode = Column(String(16), nullable=False, default="single")
    prompt = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    config = Column(JSONType, nullable=False, default=dict)
    progress = Column(JSONType, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    celery_task_id = Column(String(128), nullable=True)
    pages_crawled = Column(Integer, nullable=False, default=0)
    token_usage = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    results = relationship(
        "CrawlResult",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="CrawlResult.id",
    )


class CrawlResult(Base):
    __tablename__ = "crawl_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    source_url = Column(String(2048), nullable=False)
    raw_text = Column(Text, nullable=True)
    extracted_data = Column(JSONType, nullable=True)
    token_usage = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="ok")
    error_message = Column(Text, nullable=True)
    depth = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    task = relationship("Task", back_populates="results")
