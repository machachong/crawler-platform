"""Persistent daily/weekly scheduling. All instants in the DB are naive UTC."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy import update

from .database import SessionLocal
from .models import ScheduleEvent, ServiceHeartbeat, Task, TaskRun, TaskSchedule, User
from .runs import ACTIVE, latest_run, reset_for_run, start_run

ZONES = {"Asia/Shanghai": timezone(timedelta(hours=8)), "UTC": timezone.utc}


def slot(plan, now, forward=True):
    zone = ZONES[plan.timezone]
    local = now.replace(tzinfo=timezone.utc).astimezone(zone)
    hour, minute = map(int, plan.time_of_day.split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if plan.cadence == "weekly":
        offset = (plan.weekday - candidate.weekday()) % 7 if forward else -((candidate.weekday() - plan.weekday) % 7)
        candidate += timedelta(days=offset)
        period = 7
    else:
        period = 1
    if forward and candidate <= local:
        candidate += timedelta(days=period)
    elif not forward and candidate > local:
        candidate -= timedelta(days=period)
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def configure_schedule(db, task, paused=False, now=None):
    config = task.config or {}
    plan = db.get(TaskSchedule, task.id)
    cadence = config.get("schedule", "once")
    if cadence == "once":
        if plan:
            db.delete(plan)
        return None
    if plan is None:
        plan = TaskSchedule(task_id=task.id)
        db.add(plan)
    plan.cadence = cadence
    plan.time_of_day = config.get("schedule_time", "09:00")
    plan.weekday = config.get("schedule_weekday", 0)
    plan.timezone = config.get("schedule_timezone", "Asia/Shanghai")
    plan.paused = paused
    plan.next_run_at = None if paused else slot(plan, now or datetime.utcnow())
    return plan


def dispatch_due(now=None):
    from .api.tasks import enqueue
    from .crawler.dedupe import clear_cancel, UrlDedupe

    now = now or datetime.utcnow()
    dispatched, skipped = 0, 0
    with SessionLocal() as db:
        ids = [row[0] for row in db.query(TaskSchedule.task_id).filter(
            TaskSchedule.paused.is_(False), TaskSchedule.next_run_at <= now
        ).limit(100).all()]
    for task_id in ids:
        with SessionLocal() as db:
            try:
                db.execute(update(Task).where(Task.id == task_id).values(updated_at=Task.updated_at).execution_options(synchronize_session=False))
                task = db.query(Task).filter(Task.id == task_id).with_for_update().first()
                plan = db.query(TaskSchedule).filter(TaskSchedule.task_id == task_id).with_for_update().first()
                if not task or not plan or plan.paused or not plan.next_run_at or plan.next_run_at > now:
                    continue
                due = slot(plan, now, forward=False)
                # Consume only the latest due slot after downtime, never replay a backlog.
                plan.next_run_at = slot(plan, now)
                owner = db.get(User, task.owner_id)
                reason = "上次运行未结束，本次跳过" if task.status in ACTIVE else (
                    "账号已禁用，本次跳过" if owner and not owner.is_active else None
                )
                event = ScheduleEvent(task_id=task.id, scheduled_at=due,
                                      status="skipped" if reason else "dispatched", reason=reason)
                db.add(event)
                db.flush()  # Unique slot is also the cross-process SQLite race guard.
                if reason:
                    skipped += 1
                    db.commit()
                    continue
                reset_for_run(db, task)
                start_run(db, task, trigger="schedule")
                db.commit()
                UrlDedupe(str(task.id)).clear()
                clear_cancel(str(task.id))
                enqueue(task, db)
                dispatched += 1
            except IntegrityError:
                db.rollback()
    # Outbox recovery: a process may exit after committing a run but before sending it.
    with SessionLocal() as db:
        pending = db.query(TaskRun).filter(TaskRun.status == "pending", TaskRun.dispatched_at.is_(None)).limit(100).all()
        for run in pending:
            task = db.get(Task, run.task_id)
            if task and task.status == "pending" and latest_run(db, task.id).id == run.id:
                clear_cancel(str(task.id))
                enqueue(task, db)
    with SessionLocal() as db:
        changed = db.execute(update(ServiceHeartbeat).where(ServiceHeartbeat.name == "scheduler").values(last_seen_at=datetime.utcnow())).rowcount
        if not changed:
            db.add(ServiceHeartbeat(name="scheduler", last_seen_at=datetime.utcnow()))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"dispatched": dispatched, "skipped": skipped}
