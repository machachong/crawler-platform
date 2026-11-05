"""Task CRUD, export, and queue dispatch."""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import update

from ..auth import get_current_user, require_writer
from ..crawler.dedupe import UrlDedupe, clear_cancel, request_cancel
from ..crawler.links import is_blocked_address, normalize_url
from ..config import DISABLE_CELERY
from ..database import get_db
from ..models import CrawlResult, Task, User
from ..runs import latest_run, start_run, sync_run, reset_for_run
from ..scheduling import configure_schedule
from ..schemas import (
    CrawlResultRead,
    TaskCreate,
    TaskDetail,
    TaskRead,
    normalize_config,
)
from ..publishing import html_response, render_page

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["tasks"])

ACTIVE_STATUSES = {"pending", "crawling", "cleaning", "ai_processing"}
TERMINAL_STATUSES = {"completed", "failed", "stopped"}


def _progress(phase: str = "pending", percent: int = 0) -> dict:
    return {
        "phase": phase,
        "percent": percent,
        "pages_done": 0,
        "pages_failed": 0,
        "logs": [],
    }


def _get_task(db: Session, task_id: UUID, user: User, lock: bool = False) -> Task:
    query = db.query(Task).filter(Task.id == task_id)
    if user.role != "admin":
        query = query.filter(Task.owner_id == user.id)
    if lock:
        statement = update(Task).where(Task.id == task_id)
        if user.role != "admin":
            statement = statement.where(Task.owner_id == user.id)
        # Also acquire a write lock on SQLite, where SELECT FOR UPDATE is ignored.
        db.execute(statement.values(updated_at=Task.updated_at).execution_options(synchronize_session=False))
    task = (query.populate_existing().with_for_update() if lock else query).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _results(db: Session, task_id: UUID) -> list[CrawlResult]:
    return (
        db.query(CrawlResult)
        .filter(CrawlResult.task_id == task_id)
        .order_by(CrawlResult.id.asc())
        .all()
    )


def enqueue(task: Task, db: Session) -> None:
    run = latest_run(db, task.id)
    task_id, run_id = task.id, run.id
    try:
        from ..worker.tasks import run_crawl_task

        async_result = run_crawl_task.apply_async(args=[str(task.id), str(run.id)], task_id=str(run.id))
        db.refresh(task)
        if latest_run(db, task.id).id == run.id:
            task.celery_task_id = async_result.id
        run.dispatched_at = datetime.utcnow()
        task.updated_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        task = db.get(Task, task_id, populate_existing=True)
        current = latest_run(db, task_id)
        if task is None or current is None or current.id != run_id:
            return
        logger.exception("Failed to enqueue task %s", task.id)
        task.status = "failed"
        task.error_message = f"任务队列不可用，请确认 Redis 与 Celery worker 已启动。{exc}"
        task.updated_at = datetime.utcnow()
        sync_run(db, task)
        db.commit()


def _validate_target(url: str) -> str:
    normalized = normalize_url(url)
    if not normalized:
        raise HTTPException(status_code=400, detail="只接受 http/https 页面 URL")
    if is_blocked_address(urlparse(normalized).hostname):
        raise HTTPException(status_code=400, detail="该地址不允许作为抓取目标")
    return normalized


@router.post("/tasks", response_model=TaskRead)
def create_task(payload: TaskCreate, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    target = _validate_target(str(payload.target_url))
    mode = "single" if payload.config.is_preview else payload.mode
    config = normalize_config(mode, payload.config)
    if payload.config.is_preview:
        config["schedule"] = "once"
    task = Task(
        owner_id=user.id,
        target_url=target,
        mode=mode,
        prompt=(payload.prompt or "").strip(),
        status="pending",
        config=config,
        progress=_progress(),
        pages_crawled=0,
        token_usage=0,
    )
    db.add(task)
    db.flush()
    from ..templates import bind_template
    bind_template(db, task)
    start_run(db, task, trigger="preview" if payload.config.is_preview else "create")
    configure_schedule(db, task)
    db.commit()
    db.refresh(task)
    clear_cancel(str(task.id))
    enqueue(task, db)
    db.refresh(task)
    return task


@router.get("/tasks", response_model=list[TaskRead])
def list_tasks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Task)
    if user.role != "admin":
        query = query.filter(Task.owner_id == user.id)
    return (
        query
        .order_by(Task.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: UUID, run_id: UUID | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = _get_task(db, task_id, user)
    if run_id:
        from ..models import TaskRun
        run = db.query(TaskRun).filter(TaskRun.id == run_id, TaskRun.task_id == task.id).first()
        if not run:
            raise HTTPException(404, "运行记录不存在")
        if run.snapshot:
            return TaskDetail.model_validate(run.snapshot)
    detail = TaskDetail.model_validate(task)
    detail.results = [CrawlResultRead.model_validate(row) for row in _results(db, task.id)]
    return detail


@router.get("/tasks/{task_id}/results", response_model=list[CrawlResultRead])
def list_results(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _get_task(db, task_id, user)
    return _results(db, task_id)


@router.post("/tasks/{task_id}/stop", response_model=TaskRead)
def stop_task(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    task = _get_task(db, task_id, user, lock=True)
    if task.status in TERMINAL_STATUSES:
        return task
    request_cancel(str(task.id))
    if task.celery_task_id and not DISABLE_CELERY:
        try:
            from ..worker.celery_app import celery_app

            celery_app.control.revoke(task.celery_task_id, terminate=False)
        except Exception as exc:
            logger.info("Celery revoke skipped: %s", exc)
    task.status = "stopped"
    task.error_message = "任务已停止。已抓取的页面会保留。"
    progress = dict(task.progress or {})
    progress["phase"] = "stopped"
    task.progress = progress
    task.updated_at = datetime.utcnow()
    sync_run(db, task)
    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/rerun", response_model=TaskRead)
def rerun_task(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    task = _get_task(db, task_id, user, lock=True)
    if task.status in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="任务仍在执行，请先停止")
    reset_for_run(db, task)
    UrlDedupe(str(task.id)).clear()
    clear_cancel(str(task.id))
    start_run(db, task)
    db.commit()
    db.refresh(task)
    enqueue(task, db)
    db.refresh(task)
    return task


@router.delete("/tasks/{task_id}")
def delete_task(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(require_writer)):
    task = _get_task(db, task_id, user, lock=True)
    if task.status in ACTIVE_STATUSES:
        request_cancel(str(task.id))
        if task.celery_task_id and not DISABLE_CELERY:
            try:
                from ..worker.celery_app import celery_app

                celery_app.control.revoke(task.celery_task_id, terminate=False)
            except Exception:
                pass
    UrlDedupe(str(task.id)).clear()
    clear_cancel(str(task.id))
    db.delete(task)
    db.commit()
    return {"deleted": True, "id": str(task_id)}


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _export_rows(results: list[CrawlResult]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for result in results:
        data = result.extracted_data
        if isinstance(data, list) and data:
            for item in data:
                if isinstance(item, dict):
                    rows.append(
                        {"source_url": result.source_url, **{key: _cell(val) for key, val in item.items()}}
                    )
                else:
                    rows.append({"source_url": result.source_url, "value": _cell(item)})
            continue
        if isinstance(data, dict) and data:
            rows.append({"source_url": result.source_url, **{key: _cell(val) for key, val in data.items()}})
            continue
        rows.append(
            {
                "source_url": result.source_url,
                "raw_text": result.raw_text or "",
                "extracted": _cell(data),
            }
        )
    return rows


@router.get("/tasks/{task_id}/export")
def export_task(
    task_id: UUID,
    format: str = Query("json", pattern="^(json|csv|md|html)$"),
    run_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = _get_task(db, task_id, user)
    results = _results(db, task.id)
    if run_id:
        detail = get_task(task_id, run_id, db, user)
        task, results = detail, detail.results
    if format == "html":
        if task.status in ACTIVE_STATUSES:
            raise HTTPException(409, "任务仍在执行，请等待结束或停止后再生成网页")
        _, html = render_page(task, results)
        return html_response(html, f"task-{task.id}.html")
    filename = f"task-{task.id}.{format if format != 'md' else 'md'}"
    if format == "json":
        detail = TaskDetail.model_validate(task)
        detail.results = [CrawlResultRead.model_validate(row) for row in results]
        body = json.dumps(detail.model_dump(mode="json"), ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "csv":
        rows = _export_rows(results)
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames or ["source_url"])
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            content="\ufeff" + buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    parts = [
        f"# 抓取任务 {task.id}",
        "",
        f"- 目标: {task.target_url}",
        f"- 模式: {task.mode}",
        f"- 状态: {task.status}",
        f"- Token: {task.token_usage}",
        "",
        "## 指令",
        "",
        task.prompt or "（无）",
        "",
    ]
    if task.error_message:
        parts.extend(["## 错误", "", task.error_message, ""])
    for result in results:
        parts.extend(
            [
                f"## {result.source_url}",
                "",
                f"- 深度: {result.depth}",
                f"- 状态: {result.status}",
                f"- Token: {result.token_usage}",
                "",
                "### 清洗正文",
                "",
                result.raw_text or "（空）",
                "",
                "### 提取结果",
                "",
                "```json",
                json.dumps(result.extracted_data, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return Response(
        content="\n".join(parts),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="task-{task.id}.md"'},
    )
