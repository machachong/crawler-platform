"""Task-center search, execution history, schedules, and reusable templates."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_writer
from ..database import get_db
from ..models import CrawlResult, ScheduleEvent, ServiceHeartbeat, Task, TaskRun, TaskSchedule, TaskTemplate, TemplateVersion, User
from ..runs import ACTIVE, ensure_run, latest_run, reset_for_run, start_run
from ..schemas import APIModel, OutputField, TaskConfig, TaskRead
from ..scheduling import configure_schedule
from .tasks import _get_task, enqueue

router = APIRouter(prefix="/api/v1", tags=["workspace"])


@router.get("/service-status")
def service_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    heartbeat = db.get(ServiceHeartbeat, "scheduler")
    return {"scheduler_online": bool(heartbeat and heartbeat.last_seen_at >= datetime.utcnow() - timedelta(seconds=90)),
            "scheduler_last_seen": heartbeat.last_seen_at.isoformat() + "Z" if heartbeat else None}


class RunRead(APIModel):
    id: UUID
    number: int
    trigger: str
    kind: str
    status: str
    configuration: dict
    metrics: dict
    error_message: str | None
    pages_crawled: int
    token_usage: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ScheduleRead(APIModel):
    cadence: str
    time_of_day: str
    weekday: int
    timezone: str
    paused: bool
    next_run_at: datetime | None


class ScheduleUpdate(BaseModel):
    schedule: str = Field(pattern="^(once|daily|weekly)$")
    schedule_time: str = Field("09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    schedule_weekday: int = Field(0, ge=0, le=6)
    schedule_timezone: str = Field("Asia/Shanghai", pattern="^(Asia/Shanghai|UTC)$")
    paused: bool = False


class Reextract(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    output_fields: list[OutputField] | None = Field(None, max_length=30)


class TemplateWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=1000)
    prompt: str = Field(min_length=1, max_length=20000)
    fields: list[OutputField] = Field(default_factory=list, max_length=30)

    @field_validator("name", "prompt")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()

    @field_validator("fields")
    @classmethod
    def unique_fields(cls, value):
        if len({field.name for field in value}) != len(value):
            raise ValueError("输出字段名不能重复")
        return value


class TemplateRead(APIModel):
    id: UUID
    name: str
    description: str
    prompt: str
    fields: list[OutputField]
    version: int
    is_system: bool


@router.get("/task-center")
def task_center(q: str = Query("", max_length=120), status: str = "", domain: str = Query("", max_length=253),
                created_after: datetime | None = None, created_before: datetime | None = None,
                limit: int = Query(12, ge=1, le=100), offset: int = Query(0, ge=0),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(Task)
    if user.role != "admin":
        query = query.filter(Task.owner_id == user.id)
    if q.strip():
        value = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(or_(Task.target_url.ilike(value, escape="\\"),
            Task.config["name"].as_string().ilike(value, escape="\\"),
            cast(Task.config["tags"], String).ilike(value, escape="\\")))
    if domain.strip():
        host = domain.strip().lower()
        query = query.filter(or_(*(Task.target_url.startswith(prefix, autoescape=True) for prefix in (
            f"http://{host}/", f"https://{host}/", f"http://{host}:", f"https://{host}:"))))
    for value, is_start in [(created_after, True), (created_before, False)]:
        if value:
            value = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
            query = query.filter(Task.created_at >= value if is_start else Task.created_at < value)
    counts = dict(query.with_entities(Task.status, func.count(Task.id)).group_by(Task.status).all())
    usage = db.query(func.count(TaskRun.id), func.coalesce(func.sum(TaskRun.token_usage), 0)).filter(
        TaskRun.task_id.in_(query.with_entities(Task.id)), TaskRun.created_at >= datetime.utcnow() - timedelta(days=7)
    ).one()
    if status:
        query = query.filter(Task.status == status)
    total = query.count()
    tasks = query.order_by(Task.created_at.desc(), Task.id).offset(offset).limit(limit).all()
    ids = [task.id for task in tasks]
    plans = {plan.task_id: plan for plan in db.query(TaskSchedule).filter(TaskSchedule.task_id.in_(ids)).all()} if ids else {}
    run_counts = dict(db.query(TaskRun.task_id, func.max(TaskRun.number)).filter(TaskRun.task_id.in_(ids)).group_by(TaskRun.task_id).all()) if ids else {}
    successes = dict(db.query(TaskRun.task_id, func.max(TaskRun.finished_at)).filter(TaskRun.task_id.in_(ids), TaskRun.status == "completed").group_by(TaskRun.task_id).all()) if ids else {}
    items = []
    owner_ids = {task.owner_id for task in tasks}
    owners = {owner.id: owner.username for owner in db.query(User).filter(User.id.in_(owner_ids)).all()} if owner_ids else {}
    for task in tasks:
        data = TaskRead.model_validate(task).model_dump(mode="json")
        data["progress"] = {key: value for key, value in data["progress"].items() if key != "logs"}
        data["run_count"] = run_counts.get(task.id, 0)
        data["last_success_at"] = successes[task.id].isoformat() + "Z" if successes.get(task.id) else None
        data["owner_name"] = owners.get(task.owner_id, "历史任务")
        data["schedule_plan"] = ScheduleRead.model_validate(plans[task.id]).model_dump(mode="json") if task.id in plans else None
        items.append(data)
    return {"items": items, "total": total, "counts": counts, "usage": {"runs_7d": usage[0], "tokens_7d": usage[1]}, "scope": "all" if user.role == "admin" else "own"}


@router.get("/tasks/{task_id}/runs", response_model=list[RunRead])
def runs(task_id: UUID, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _get_task(db, task_id, user)
    ensure_run(db, task)
    db.commit()
    return db.query(TaskRun).filter(TaskRun.task_id == task_id).order_by(TaskRun.number.desc()).offset(offset).limit(limit).all()


@router.get("/tasks/{task_id}/schedule", response_model=ScheduleRead | None)
def schedule(task_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_task(db, task_id, user)
    return db.get(TaskSchedule, task_id)


@router.put("/tasks/{task_id}/schedule", response_model=ScheduleRead | None)
def update_schedule(task_id: UUID, payload: ScheduleUpdate, user: User = Depends(require_writer), db: Session = Depends(get_db)):
    task = _get_task(db, task_id, user, lock=True)
    task.config = {**(task.config or {}), **payload.model_dump(exclude={"paused"})}
    plan = configure_schedule(db, task, payload.paused)
    db.commit()
    if plan:
        db.refresh(plan)
    return plan


@router.get("/tasks/{task_id}/schedule-events")
def schedule_events(task_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_task(db, task_id, user)
    return [{"scheduled_at": row.scheduled_at.isoformat() + "Z", "status": row.status, "reason": row.reason}
            for row in db.query(ScheduleEvent).filter(ScheduleEvent.task_id == task_id).order_by(ScheduleEvent.scheduled_at.desc()).limit(20)]


@router.post("/tasks/{task_id}/reextract", response_model=TaskRead)
def reextract(task_id: UUID, payload: Reextract, user: User = Depends(require_writer), db: Session = Depends(get_db)):
    task = _get_task(db, task_id, user, lock=True)
    if task.status in ACTIVE:
        raise HTTPException(409, "任务仍在执行，请先停止")
    if not db.query(CrawlResult).filter(CrawlResult.task_id == task_id, CrawlResult.raw_text.isnot(None), CrawlResult.status != "fetch_failed").count():
        raise HTTPException(422, "没有可复用的正文，请先抓取页面")
    reset_for_run(db, task, "reextract")
    task.prompt = payload.prompt.strip()
    if not task.prompt:
        raise HTTPException(422, "提示词不能为空")
    if payload.output_fields is not None:
        task.config = {**(task.config or {}), "output_fields": [field.model_dump() for field in payload.output_fields]}
    start_run(db, task, trigger="reextract", kind="reextract")
    db.commit()
    from ..crawler.dedupe import clear_cancel
    clear_cancel(str(task.id))
    enqueue(task, db)
    db.refresh(task)
    return task


@router.get("/templates", response_model=list[TemplateRead])
def templates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..templates import seed_templates
    seed_templates(db)
    return db.query(TaskTemplate).filter(or_(TaskTemplate.is_system.is_(True), TaskTemplate.owner_id == user.id)).order_by(TaskTemplate.is_system.desc(), TaskTemplate.name).all()


@router.post("/tasks/{task_id}/retry-failed", response_model=TaskRead)
def retry_failed(task_id: UUID, user: User = Depends(require_writer), db: Session = Depends(get_db)):
    task = _get_task(db, task_id, user, lock=True)
    if task.status in ACTIVE:
        raise HTTPException(409, "任务仍在执行，请先停止")
    ids = [row[0] for row in db.query(CrawlResult.id).filter(CrawlResult.task_id == task_id,
        CrawlResult.status.in_(["fetch_failed", "ai_failed", "validation_failed", "ai_pending"])).all()]
    if not ids:
        raise HTTPException(422, "没有需要重试的失败页面")
    reset_for_run(db, task, "retry_failed", ids)
    run = start_run(db, task, trigger="retry", kind="retry_failed")
    run.configuration = {**run.configuration, "result_ids": ids}
    db.commit()
    from ..crawler.dedupe import clear_cancel
    clear_cancel(str(task.id))
    enqueue(task, db)
    db.refresh(task)
    return task


@router.post("/templates", response_model=TemplateRead, status_code=201)
def create_template(payload: TemplateWrite, user: User = Depends(require_writer), db: Session = Depends(get_db)):
    row = TaskTemplate(owner_id=user.id, **payload.model_dump())
    db.add(row)
    db.flush()
    db.add(TemplateVersion(template_id=row.id, version=row.version, prompt=row.prompt, fields=row.fields))
    db.commit()
    db.refresh(row)
    return row


@router.put("/templates/{template_id}", response_model=TemplateRead)
def update_template(template_id: UUID, payload: TemplateWrite, user: User = Depends(require_writer), db: Session = Depends(get_db)):
    row = db.query(TaskTemplate).filter(TaskTemplate.id == template_id, TaskTemplate.owner_id == user.id,
                                       TaskTemplate.is_system.is_(False)).with_for_update().first()
    if not row:
        raise HTTPException(404, "模板不存在或不可修改")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.version += 1
    db.add(TemplateVersion(template_id=row.id, version=row.version, prompt=row.prompt, fields=row.fields))
    db.commit()
    db.refresh(row)
    return row
