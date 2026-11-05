"""Celery entry: Scrapling crawl, clean, then optional LLM extraction."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm.attributes import flag_modified

from ..ai.extractor import AIConfigError, extract_structured, keyword_hit
from ..crawler.dedupe import UrlDedupe, is_cancelled
from ..crawler.engine import CrawlJob, friendly_crawl_error, run_crawl
from ..database import SessionLocal, init_db
from ..models import CrawlResult, Task
from .celery_app import celery_app

logger = logging.getLogger(__name__)

ACTIVE = {"pending", "crawling", "cleaning", "ai_processing"}


def _now() -> datetime:
    return datetime.utcnow()


def _log(progress: dict, message: str, level: str = "info") -> dict:
    logs = list(progress.get("logs") or [])
    logs.append({"time": _now().isoformat() + "Z", "level": level, "message": message})
    updated = dict(progress)
    updated["logs"] = logs[-80:]
    return updated


def _load(db, task_id: str) -> Task | None:
    return db.query(Task).filter(Task.id == UUID(str(task_id))).first()


def _stopped(db, task: Task) -> bool:
    db.refresh(task)
    return task.status == "stopped" or is_cancelled(str(task.id))


def _save_fields(db, task: Task, **fields) -> bool:
    db.refresh(task)
    if task.status == "stopped" and fields.get("status") not in (None, "stopped"):
        return False
    for key, value in fields.items():
        setattr(task, key, value)
    task.updated_at = _now()
    flag_modified(task, "progress")
    flag_modified(task, "config")
    db.commit()
    return True


def _job_from_task(task: Task) -> CrawlJob:
    config = task.config or {}
    mode = task.mode or "single"
    if mode == "single":
        max_depth = 0
        max_pages = 1
    else:
        max_depth = int(config.get("max_depth") or 0)
        max_pages = int(config.get("max_pages") or 20)
    return CrawlJob(
        root_url=task.target_url,
        mode=mode,
        max_depth=max(0, min(5, max_depth)),
        max_pages=max(1, min(200, max_pages)),
        concurrency=max(1, min(5, int(config.get("concurrency") or 1))),
        respect_robots=bool(config.get("respect_robots", True)),
        engine=config.get("engine") or "http",
        download_delay=float(config.get("download_delay") or 0),
    )


def _percent(phase: str, done: int, total: int) -> int:
    if phase == "crawling":
        if total <= 0:
            return 10
        return min(65, 10 + int(55 * done / total))
    if phase == "cleaning":
        return 75
    if phase == "ai_processing":
        if total <= 0:
            return 80
        return min(95, 78 + int(17 * done / total))
    if phase == "completed":
        return 100
    return 0


def execute_task(task_id: str) -> dict:
    """Run one crawl end to end. Called by Celery and by tests."""
    init_db()
    db = SessionLocal()
    task = None
    try:
        task = _load(db, task_id)
        if not task:
            return {"status": "missing"}
        if task.status == "stopped":
            return {"status": "stopped"}

        job = _job_from_task(task)
        progress = _log(_progress_base(task, "crawling", 8), f"开始抓取 {job.root_url}")
        if not _save_fields(
            db,
            task,
            status="crawling",
            error_message=None,
            progress=progress,
        ):
            return {"status": "stopped"}

        seen_urls: set[str] = set()

        def should_stop() -> bool:
            return _stopped(db, task)

        def on_item(item: dict) -> None:
            source = str(item.get("url") or "")
            if not source or source in seen_urls:
                return
            seen_urls.add(source)
            error = item.get("error")
            row = CrawlResult(
                task_id=task.id,
                source_url=source,
                raw_text=item.get("markdown") or "",
                status="fetch_failed" if error else "ok",
                error_message=error,
                depth=int(item.get("depth") or 0),
            )
            db.add(row)
            db.flush()
            done = len(seen_urls)
            failed = (
                db.query(CrawlResult)
                .filter(CrawlResult.task_id == task.id, CrawlResult.status != "ok")
                .count()
            )
            ok = done - failed
            progress = dict(task.progress or {})
            progress.update(
                {
                    "phase": "crawling",
                    "pages_done": done,
                    "pages_failed": failed,
                    "percent": _percent("crawling", done, job.max_pages),
                }
            )
            label = f"HTTP {item.get('status_code') or '-'}"
            message = f"抓取 {source}（深度 {row.depth}，{label}）"
            if error:
                message = f"{message} 失败: {error}"
            progress = _log(progress, message, "error" if error else "info")
            task.pages_crawled = ok
            task.progress = progress
            task.updated_at = _now()
            flag_modified(task, "progress")
            db.commit()

        try:
            summary = asyncio.run(
                run_crawl(job, on_item, should_stop, UrlDedupe(str(task.id)))
            )
        except Exception as exc:
            logger.exception("Crawl failed for %s", task_id)
            _save_fields(
                db,
                task,
                status="failed",
                error_message=friendly_crawl_error(exc),
                progress=_log(dict(task.progress or {}), friendly_crawl_error(exc), "error"),
            )
            return {"status": "failed", "error": str(exc)}

        db.refresh(task)
        if task.status == "stopped" or should_stop():
            progress = _log(dict(task.progress or {}), "任务已停止，保留已抓取页面。", "warning")
            progress["phase"] = "stopped"
            _save_fields(db, task, status="stopped", progress=progress, error_message=task.error_message or "任务已停止。已抓取的页面会保留。")
            return {"status": "stopped"}

        if summary.pages_ok == 0:
            if summary.robots_disallowed:
                message = (
                    "未抓取到页面：robots.txt 不允许访问起始地址或站内链接。"
                    "仅在你有权抓取该站时，才可以关闭“遵守 robots.txt”。"
                )
            elif summary.failures:
                message = "抓取失败: " + "; ".join(
                    f"{item.get('url')}: {item.get('error')}" for item in summary.failures[:3]
                )
            else:
                message = "没有成功抓取到页面。请检查 URL 是否可访问。"
            _save_fields(
                db,
                task,
                status="failed",
                error_message=message[:1000],
                progress=_log(dict(task.progress or {}), message, "error"),
            )
            return {"status": "failed"}

        clean_progress = _log(
            {**dict(task.progress or {}), "phase": "cleaning", "percent": 75},
            f"正文清洗完成，共 {summary.pages_ok} 页。",
        )
        if not _save_fields(db, task, status="cleaning", progress=clean_progress):
            return {"status": "stopped"}

        return _run_ai(db, task, job)
    except SoftTimeLimitExceeded:
        if task is not None:
            _save_fields(db, task, status="failed", error_message="任务超时，已保留已抓取的页面。")
        return {"status": "failed", "error": "timeout"}
    except Exception as exc:
        logger.exception("Task %s crashed", task_id)
        if task is not None:
            _save_fields(db, task, status="failed", error_message=f"任务失败: {exc}"[:1000])
        return {"status": "failed", "error": str(exc)}
    finally:
        db.close()


def _progress_base(task: Task, phase: str, percent: int) -> dict:
    progress = dict(task.progress or {})
    progress["phase"] = phase
    progress["percent"] = percent
    return progress


def _run_ai(db, task: Task, job: CrawlJob) -> dict:
    prompt = (task.prompt or "").strip()
    keywords = list((task.config or {}).get("keywords") or [])
    rows = (
        db.query(CrawlResult)
        .filter(CrawlResult.task_id == task.id)
        .order_by(CrawlResult.id.asc())
        .all()
    )
    if not prompt:
        progress = _log(
            {**dict(task.progress or {}), "phase": "completed", "percent": 100},
            "未填写提取指令，已保存清洗后的正文。",
        )
        _save_fields(db, task, status="completed", progress=progress, error_message=None)
        return {"status": "completed", "ai": False}

    targets = [
        row
        for row in rows
        if row.status == "ok" and (row.raw_text or "").strip() and keyword_hit(row.raw_text or "", keywords)
    ]
    for row in rows:
        if row.status == "ok" and row not in targets and (row.raw_text or "").strip() and keywords:
            row.extracted_data = {"skipped": True, "reason": "未命中关键词"}
    if not targets:
        progress = _log(
            {**dict(task.progress or {}), "phase": "completed", "percent": 100},
            "没有页面需要 AI 提取。",
        )
        _save_fields(db, task, status="completed", progress=progress)
        return {"status": "completed", "ai": False}

    ai_progress = _log(
        {**dict(task.progress or {}), "phase": "ai_processing", "percent": 80},
        f"开始 AI 提取，共 {len(targets)} 页。",
    )
    if not _save_fields(db, task, status="ai_processing", progress=ai_progress):
        return {"status": "stopped"}

    total_tokens = 0
    errors: list[str] = []
    for index, row in enumerate(targets, start=1):
        if _stopped(db, task):
            progress = _log(dict(task.progress or {}), "任务已停止，保留已完成的提取。", "warning")
            progress["phase"] = "stopped"
            _save_fields(
                db,
                task,
                status="stopped",
                progress=progress,
                token_usage=total_tokens,
                error_message="任务已停止。已抓取的页面会保留。",
            )
            return {"status": "stopped"}
        try:
            extracted = extract_structured(row.raw_text or "", prompt)
        except AIConfigError as exc:
            progress = _log(dict(task.progress or {}), str(exc), "error")
            progress["phase"] = "failed"
            progress["percent"] = progress.get("percent") or 80
            _save_fields(
                db,
                task,
                status="failed",
                error_message=str(exc),
                progress=progress,
                token_usage=total_tokens,
            )
            return {"status": "failed", "error": "missing_api_key"}
        except Exception as exc:
            logger.exception("AI failed for %s", row.source_url)
            message = str(exc)[:500]
            row.extracted_data = {"error": message}
            row.token_usage = 0
            errors.append(f"{row.source_url}: {message}")
        else:
            row.extracted_data = extracted["data"]
            row.token_usage = int(extracted["token_usage"] or 0)
            total_tokens += row.token_usage
        progress = dict(task.progress or {})
        progress["phase"] = "ai_processing"
        progress["percent"] = _percent("ai_processing", index, len(targets))
        progress = _log(progress, f"AI 处理 {row.source_url}（{row.token_usage} tokens）")
        task.progress = progress
        task.token_usage = total_tokens
        task.updated_at = _now()
        flag_modified(task, "progress")
        db.commit()

    if errors and len(errors) == len(targets):
        message = "AI 提取失败: " + "；".join(errors[:3])
        progress = _log(dict(task.progress or {}), message, "error")
        progress["phase"] = "failed"
        _save_fields(db, task, status="failed", error_message=message[:1000], progress=progress, token_usage=total_tokens)
        return {"status": "failed"}

    message = None
    if errors:
        message = "部分页面 AI 提取失败: " + "；".join(errors[:3])
    progress = _log(
        {**dict(task.progress or {}), "phase": "completed", "percent": 100},
        message or f"完成。消耗 {total_tokens} tokens。",
        "warning" if message else "info",
    )
    _save_fields(
        db,
        task,
        status="completed",
        error_message=message,
        progress=progress,
        token_usage=total_tokens,
    )
    return {"status": "completed", "token_usage": total_tokens}


@celery_app.task(bind=True, name="tasks.run_crawl")
def run_crawl_task(self, task_id: str):
    logger.info("Running crawl task %s", task_id)
    try:
        return execute_task(task_id)
    except SoftTimeLimitExceeded:
        db = SessionLocal()
        try:
            task = _load(db, task_id)
            if task and task.status not in {"completed", "stopped"}:
                _save_fields(db, task, status="failed", error_message="任务超时，已保留已抓取的页面。")
        finally:
            db.close()
        raise
