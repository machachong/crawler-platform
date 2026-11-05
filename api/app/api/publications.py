"""Authenticated publication management and anonymous HTML snapshot delivery."""

from datetime import datetime
import re
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_writer
from ..config import PUBLIC_BASE_URL
from ..database import get_db
from ..models import PublishedPage, User
from ..publishing import html_response, render_page
from ..schemas import APIModel
from .tasks import ACTIVE_STATUSES, _get_task, _results

router = APIRouter(tags=["publishing"])


class PublishOptions(BaseModel):
    title: str = Field("", max_length=120)


class PublicationRead(APIModel):
    slug: str
    title: str
    url: str
    created_at: datetime
    updated_at: datetime


def _read(page: PublishedPage, request: Request) -> PublicationRead:
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/") + "/p"
    return PublicationRead(slug=page.slug, title=page.title, url=f"{base}/{page.slug}",
                           created_at=page.created_at, updated_at=page.updated_at)


def _ready(task):
    if task.status in ACTIVE_STATUSES:
        raise HTTPException(409, "任务仍在执行，请等待结束或停止后再生成网页")


@router.get("/api/v1/tasks/{task_id}/publication", response_model=PublicationRead | None)
def publication(task_id: UUID, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_task(db, task_id, user)
    page = db.query(PublishedPage).filter(PublishedPage.task_id == task_id).first()
    return _read(page, request) if page else None


@router.get("/api/v1/tasks/{task_id}/preview")
def preview(task_id: UUID, title: str = Query("", max_length=120),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _get_task(db, task_id, user)
    _ready(task)
    _, html = render_page(task, _results(db, task_id), title)
    return html_response(html)


@router.post("/api/v1/tasks/{task_id}/publication", response_model=PublicationRead)
def publish(task_id: UUID, payload: PublishOptions, request: Request,
            user: User = Depends(require_writer), db: Session = Depends(get_db)):
    task = _get_task(db, task_id, user, lock=True)
    _ready(task)
    title, html = render_page(task, _results(db, task_id), payload.title)
    page = db.query(PublishedPage).filter(PublishedPage.task_id == task_id).first()
    if not page:
        page = PublishedPage(task_id=task_id)
        db.add(page)
    page.title, page.html, page.updated_at = title, html, datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "另一个发布操作已完成，请刷新后重试")
    db.refresh(page)
    return _read(page, request)


@router.delete("/api/v1/tasks/{task_id}/publication", status_code=204)
def unpublish(task_id: UUID, user: User = Depends(require_writer), db: Session = Depends(get_db)):
    _get_task(db, task_id, user, lock=True)
    db.query(PublishedPage).filter(PublishedPage.task_id == task_id).delete()
    db.commit()


@router.get("/p/{slug}")
@router.get("/p/{slug}.html", include_in_schema=False)
@router.get("/crawler/{slug}")
def public_page(slug: str, db: Session = Depends(get_db)):
    if not re.fullmatch(r"[0-9a-f]{32}", slug):
        raise HTTPException(404, "页面不存在或已撤下")
    page = db.get(PublishedPage, slug)
    if not page:
        raise HTTPException(404, "页面不存在或已撤下")
    return html_response(page.html)


# The configured public prefix is served directly, without stripping it at the proxy.
_public_prefix = urlsplit(PUBLIC_BASE_URL).path.rstrip("/") if PUBLIC_BASE_URL else "/p"
if _public_prefix not in {"/p", "/crawler"}:
    router.add_api_route(f"{_public_prefix}/{{slug}}", public_page, methods=["GET"], include_in_schema=False)
