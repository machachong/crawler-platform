"""Execution identity, immutable terminal snapshots, and queue recovery metadata."""

from copy import deepcopy
from datetime import datetime
from uuid import UUID
import os

from sqlalchemy import update

from .models import CrawlResult, Task, TaskRun
from .schemas import CrawlResultRead, TaskDetail
from .config import AI_MODEL

ACTIVE = {"pending", "crawling", "cleaning", "ai_processing"}


def latest_run(db, task_id):
    return db.query(TaskRun).filter(TaskRun.task_id == task_id).order_by(TaskRun.number.desc()).first()


def task_snapshot(db, task):
    detail = TaskDetail.model_validate(task)
    detail.results = [CrawlResultRead.model_validate(row) for row in db.query(CrawlResult).filter(
        CrawlResult.task_id == task.id
    ).order_by(CrawlResult.id).all()]
    return detail.model_dump(mode="json")


def sync_run(db, task, run=None):
    run = run or latest_run(db, task.id)
    if not run or (getattr(task, "_execution_run_id", None) and run.id != task._execution_run_id):
        return
    previous = run.status
    now = datetime.utcnow()
    run.status = task.status
    run.error_message = task.error_message
    run.pages_crawled = task.pages_crawled
    run.token_usage = task.token_usage
    if task.status in {"crawling", "cleaning", "ai_processing"} and not run.started_at:
        run.started_at = now
    metrics = dict(run.metrics or {})
    if previous != task.status:
        phase_since = metrics.get("phase_since")
        if phase_since:
            durations = dict(metrics.get("phase_seconds") or {})
            durations[previous] = round(durations.get(previous, 0) + (now - datetime.fromisoformat(phase_since)).total_seconds(), 3)
            metrics["phase_seconds"] = durations
        metrics["phase_since"] = now.isoformat()
    if task.status not in ACTIVE:
        run.finished_at = run.finished_at or (task.updated_at if run.trigger == "legacy" else now)
        if run.trigger != "legacy" or run.started_at:
            metrics["duration_seconds"] = round((run.finished_at - run.created_at).total_seconds(), 3)
        if run.started_at:
            metrics["queue_seconds"] = round((run.started_at - run.created_at).total_seconds(), 3)
        db.flush()
        snapshot = task_snapshot(db, task)
        snapshot.update(deepcopy(run.configuration))
        run.snapshot = snapshot
    run.metrics = metrics


def ensure_run(db, task):
    run = latest_run(db, task.id)
    if not run:
        run = TaskRun(task_id=task.id, owner_id=task.owner_id, number=1, trigger="legacy",
                      status=task.status, configuration={"target_url": task.target_url, "mode": task.mode,
                      "prompt": task.prompt or "", "config": deepcopy(task.config or {}), "ai_model": os.getenv("AI_MODEL", AI_MODEL)},
                      created_at=task.created_at or datetime.utcnow())
        db.add(run)
        db.flush()
        if task.status not in ACTIVE:
            sync_run(db, task, run)
    return run


def start_run(db, task, trigger="manual", kind="crawl"):
    previous = latest_run(db, task.id)
    if not previous and task.status != "pending":
        previous = ensure_run(db, task)
    if previous and previous.status not in ACTIVE and previous.snapshot is None:
        sync_run(db, task, previous)
    run = TaskRun(task_id=task.id, owner_id=task.owner_id, number=previous.number + 1 if previous else 1,
                  trigger=trigger, kind=kind, status="pending",
                  configuration={"target_url": task.target_url, "mode": task.mode,
                  "prompt": task.prompt or "", "config": deepcopy(task.config or {}), "ai_model": os.getenv("AI_MODEL", AI_MODEL)},
                  metrics={"phase_since": datetime.utcnow().isoformat()})
    db.add(run)
    db.flush()
    return run


def execution_current(db, task):
    expected = getattr(task, "_execution_run_id", None)
    if expected is None:
        return True
    run = latest_run(db, task.id)
    return bool(run and run.id == expected)


def lock_execution(db, task):
    db.execute(update(Task).where(Task.id == task.id).values(updated_at=Task.updated_at).execution_options(synchronize_session=False))
    if not execution_current(db, task):
        db.rollback()
        return False
    return True


def claim_run(db, task, run_id=None):
    run = ensure_run(db, task)
    if run_id and run.id != UUID(str(run_id)):
        return None
    if run.status != "pending":
        return None
    now = datetime.utcnow()
    metrics = {**(run.metrics or {}), "phase_since": now.isoformat(), "phase_seconds": {"pending": round((now - run.created_at).total_seconds(), 3)}}
    claimed = db.execute(update(TaskRun).where(TaskRun.id == run.id, TaskRun.status == "pending").values(
        status="ai_processing" if run.kind == "reextract" else "crawling", started_at=now, metrics=metrics
    ).execution_options(synchronize_session=False))
    db.commit()
    if claimed.rowcount != 1:
        return None
    db.refresh(run)
    task._execution_run_id = run.id
    return run


def reset_for_run(db, task, kind="crawl", result_ids=None):
    # Snapshot before deleting or changing the latest result cache.
    previous = ensure_run(db, task)
    if previous.snapshot is None:
        sync_run(db, task, previous)
    if kind == "crawl":
        db.query(CrawlResult).filter(CrawlResult.task_id == task.id).delete(synchronize_session=False)
        task.pages_crawled = 0
    else:
        for row in db.query(CrawlResult).filter(CrawlResult.task_id == task.id).all():
            if result_ids is not None and row.id not in result_ids:
                continue
            if row.raw_text and row.status != "fetch_failed":
                row.extracted_data = None
                row.token_usage = 0
                row.status = "ok"
                row.error_message = None
    task.status = "pending"
    task.error_message = None
    task.token_usage = 0
    task.celery_task_id = None
    task.progress = {"phase": "pending", "percent": 0, "pages_done": 0, "pages_failed": 0, "logs": []}
    task.updated_at = datetime.utcnow()
