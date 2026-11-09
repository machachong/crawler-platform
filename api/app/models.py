"""ORM models aligned with PRD v1.1 (`tasks`, `crawl_results`)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    Column,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
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
    owner_id = Column(String(128), nullable=False, default="local", index=True)
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
    publication = relationship("PublishedPage", uselist=False, cascade="all, delete-orphan", back_populates="task")
    runs = relationship("TaskRun", cascade="all, delete-orphan", passive_deletes=True)
    schedule_plan = relationship("TaskSchedule", uselist=False, cascade="all, delete-orphan", passive_deletes=True)


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


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    security_stamp = Column(String(64), nullable=False, default=lambda: uuid.uuid4().hex)
    role = Column(String(16), nullable=False, default="member")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash = Column(String(64), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    security_stamp = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"

    key = Column(String(64), primary_key=True)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False, index=True)


class CaptchaChallenge(Base):
    __tablename__ = "captcha_challenges"

    id = Column(String(32), primary_key=True)
    purpose = Column(String(16), nullable=False)
    answer_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)


class PublishedPage(Base):
    __tablename__ = "published_pages"

    slug = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    task_id = Column(GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True)
    title = Column(String(120), nullable=False)
    html = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    task = relationship("Task", back_populates="publication")


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (UniqueConstraint("task_id", "number"),)

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    task_id = Column(GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = Column(String(128), nullable=False, index=True)
    number = Column(Integer, nullable=False)
    trigger = Column(String(24), nullable=False, default="manual")
    kind = Column(String(24), nullable=False, default="crawl")
    status = Column(String(32), nullable=False, default="pending", index=True)
    configuration = Column(JSONType, nullable=False, default=dict)
    snapshot = Column(JSONType, nullable=True)
    metrics = Column(JSONType, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    pages_crawled = Column(Integer, nullable=False, default=0)
    token_usage = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    dispatched_at = Column(DateTime, nullable=True)


class TaskSchedule(Base):
    __tablename__ = "task_schedules"

    task_id = Column(GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    cadence = Column(String(16), nullable=False)
    time_of_day = Column(String(5), nullable=False, default="09:00")
    weekday = Column(Integer, nullable=False, default=0)
    timezone = Column(String(32), nullable=False, default="Asia/Shanghai")
    paused = Column(Boolean, nullable=False, default=False)
    next_run_at = Column(DateTime, nullable=True, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class ScheduleEvent(Base):
    __tablename__ = "schedule_events"
    __table_args__ = (UniqueConstraint("task_id", "scheduled_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String(32), nullable=False)
    reason = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class TaskTemplate(Base):
    __tablename__ = "task_templates"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    owner_id = Column(String(128), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=False, default="")
    version = Column(Integer, nullable=False, default=1)
    prompt = Column(Text, nullable=False)
    fields = Column(JSONType, nullable=False, default=list)
    is_system = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(GUID(), ForeignKey("task_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    prompt = Column(Text, nullable=False)
    fields = Column(JSONType, nullable=False, default=list)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class ServiceHeartbeat(Base):
    __tablename__ = "service_heartbeats"
    name = Column(String(64), primary_key=True)
    last_seen_at = Column(DateTime, nullable=False)
