"""Pydantic models for the task API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_serializer, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at", "started_at", "finished_at", "next_run_at", "scheduled_at", "dispatched_at", when_used="json", check_fields=False)
    def _serialize_dt(self, value: datetime | None):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.isoformat() + "Z"
        return value.isoformat()


class TaskConfig(BaseModel):
    is_preview: bool = False
    name: str = Field("", max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=10)
    page_title: str = Field("", max_length=120, description="发布网页时使用的默认标题")
    max_depth: int = Field(2, ge=0, le=5, description="全站模式从起始页出发的最大跳数")
    concurrency: int = Field(2, ge=1, le=5)
    respect_robots: bool = True
    max_pages: int = Field(20, ge=1, le=200)
    engine: Literal["http", "stealth"] = "http"
    schedule: Literal["once", "daily", "weekly"] = "once"
    schedule_time: str = Field("09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    schedule_weekday: int = Field(0, ge=0, le=6)
    schedule_timezone: Literal["Asia/Shanghai", "UTC"] = "Asia/Shanghai"
    template_id: UUID | None = None
    template_version: int | None = Field(None, ge=1)
    output_fields: list["OutputField"] = Field(default_factory=list, max_length=30)
    token_budget: int = Field(100000, ge=1, le=10000000)
    keywords: list[str] = Field(default_factory=list)
    download_delay: float = Field(0.2, ge=0, le=5)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value):
        tags = list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))
        if any(len(tag) > 32 for tag in tags):
            raise ValueError("标签不能超过 32 字")
        return tags

    @field_validator("output_fields")
    @classmethod
    def unique_fields(cls, value):
        if len({field.name for field in value}) != len(value):
            raise ValueError("输出字段名不能重复")
        return value


class OutputField(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: Literal["string", "number", "boolean", "date", "array"] = "string"
    required: bool = True

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        if not value.strip():
            raise ValueError("字段名不能为空")
        return value.strip()


class TaskCreate(BaseModel):
    target_url: HttpUrl
    mode: Literal["single", "site"] = "single"
    prompt: str = Field("", max_length=20000)
    config: TaskConfig = Field(default_factory=TaskConfig)


class TaskRead(APIModel):
    id: UUID
    owner_id: str
    target_url: str
    mode: str
    prompt: str | None = None
    status: str
    config: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    pages_crawled: int = 0
    token_usage: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class CrawlResultRead(APIModel):
    id: int
    task_id: UUID
    source_url: str
    raw_text: str | None = None
    extracted_data: Any = None
    token_usage: int = 0
    status: str
    error_message: str | None = None
    depth: int = 0
    created_at: datetime


class TaskDetail(TaskRead):
    results: list[CrawlResultRead] = Field(default_factory=list)


def normalize_config(mode: str, config: TaskConfig) -> dict[str, Any]:
    data = config.model_dump(mode="json")
    data["name"] = data["name"].strip()
    data["keywords"] = [str(item).strip() for item in data.get("keywords") or [] if str(item).strip()]
    data["concurrency"] = max(1, min(5, int(data["concurrency"])))
    data["download_delay"] = float(data["download_delay"])
    if mode == "single":
        data["max_depth"] = 0
        data["max_pages"] = 1
    else:
        data["max_depth"] = max(0, min(5, int(data["max_depth"])))
        data["max_pages"] = max(1, min(200, int(data["max_pages"])))
    if data["engine"] not in ("http", "stealth"):
        data["engine"] = "http"
    return data
