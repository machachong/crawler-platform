from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.database import SessionLocal
from app.models import CrawlResult, ScheduleEvent, Task, TaskRun, TaskSchedule, TemplateVersion
from app.runs import latest_run
from app.scheduling import dispatch_due, slot
from app.worker.tasks import execute_task


@pytest.fixture()
def fake_crawl(monkeypatch):
    from app.crawler.engine import CrawlSummary

    calls = []
    async def crawl(job, on_item, should_stop, dedupe):
        calls.append(job.root_url)
        on_item({"url": job.root_url, "markdown": "saved source body", "status_code": 200, "depth": 0})
        return CrawlSummary(pages_ok=1, requests_count=1)
    monkeypatch.setattr("app.worker.tasks.run_crawl", crawl)
    return calls


def create(client, **overrides):
    response = client.post("/api/v1/tasks", json={"target_url": "https://example.com/", "prompt": "", **overrides})
    assert response.status_code == 200, response.text
    return response.json()


def test_rerun_preserves_history_and_historical_exports(client, fake_crawl):
    task = create(client)
    ident = task["id"]
    assert execute_task(ident)["status"] == "completed"
    first = client.get(f"/api/v1/tasks/{ident}/runs").json()[0]
    assert first["number"] == 1
    assert first["finished_at"].endswith("Z")
    assert first["metrics"]["duration_seconds"] >= 0
    assert client.post(f"/api/v1/tasks/{ident}/rerun").status_code == 200
    assert client.get(f"/api/v1/tasks/{ident}").json()["results"] == []
    historical = client.get(f"/api/v1/tasks/{ident}", params={"run_id": first["id"]})
    assert historical.status_code == 200
    assert historical.json()["results"][0]["raw_text"] == "saved source body"
    for fmt in ["json", "csv", "md", "html"]:
        response = client.get(f"/api/v1/tasks/{ident}/export", params={"format": fmt, "run_id": first["id"]})
        assert response.status_code == 200
        assert "saved source body" in response.text
    assert execute_task(ident)["status"] == "completed"
    assert len(fake_crawl) == 2
    assert [row["number"] for row in client.get(f"/api/v1/tasks/{ident}/runs").json()] == [2, 1]


def test_duplicate_and_superseded_messages_cannot_execute_new_run(client, fake_crawl):
    task = create(client)
    ident = task["id"]
    old = client.get(f"/api/v1/tasks/{ident}/runs").json()[0]["id"]
    client.post(f"/api/v1/tasks/{ident}/stop")
    client.post(f"/api/v1/tasks/{ident}/rerun")
    assert execute_task(ident, old)["status"] == "ignored"
    assert not fake_crawl
    current = client.get(f"/api/v1/tasks/{ident}/runs").json()[0]["id"]
    assert execute_task(ident, current)["status"] == "completed"
    assert execute_task(ident, current)["status"] == "ignored"
    assert len(fake_crawl) == 1


def test_concurrent_reruns_create_only_one_new_run(client, fake_crawl):
    task = create(client)
    execute_task(task["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(f"/api/v1/tasks/{task['id']}/rerun").status_code, range(2)))
    assert sorted(responses) == [200, 409]
    assert len(client.get(f"/api/v1/tasks/{task['id']}/runs").json()) == 2


def test_concurrent_worker_claim_executes_once(client, fake_crawl):
    task = create(client)
    run = client.get(f"/api/v1/tasks/{task['id']}/runs").json()[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute_task(task["id"], run["id"])["status"], range(2)))
    assert sorted(results) == ["completed", "ignored"]
    assert len(fake_crawl) == 1


def test_reextract_reuses_body_and_preserves_previous_prompt(client, fake_crawl, monkeypatch):
    task = create(client)
    ident = task["id"]
    execute_task(ident)
    response = client.post(f"/api/v1/tasks/{ident}/reextract", json={"prompt": "new extraction", "output_fields": [{"name": "price", "type": "number", "required": True}]})
    assert response.status_code == 200
    calls = []
    def extract(content, prompt):
        calls.append((content, prompt))
        return {"data": {"price": 3}, "token_usage": 9}
    monkeypatch.setattr("app.worker.tasks.extract_structured", extract)
    assert execute_task(ident)["status"] == "completed"
    assert len(fake_crawl) == 1
    assert calls[0][0] == "saved source body"
    assert "new extraction" in calls[0][1] and "price" in calls[0][1]
    history = client.get(f"/api/v1/tasks/{ident}/runs").json()
    assert history[0]["kind"] == "reextract"
    assert history[1]["configuration"]["prompt"] == ""
    assert client.get(f"/api/v1/tasks/{ident}").json()["results"][0]["extracted_data"] == {"price": 3}


def test_validation_failure_is_visible_and_not_publishable(client, fake_crawl, monkeypatch):
    task = create(client, prompt="get price", config={"output_fields": [{"name": "price", "type": "number"}]})
    monkeypatch.setattr("app.worker.tasks.extract_structured", lambda *_: {"data": {"price": "not a number"}, "token_usage": 4})
    assert execute_task(task["id"])["status"] == "failed"
    result = client.get(f"/api/v1/tasks/{task['id']}").json()["results"][0]
    assert result["status"] == "validation_failed"
    assert "price" in result["error_message"]
    assert result["token_usage"] == 4
    assert client.post(f"/api/v1/tasks/{task['id']}/publication", json={}).status_code == 422


def test_old_worker_does_not_write_into_restarted_task(client, fake_crawl, monkeypatch):
    task = create(client, prompt="extract")
    ident = task["id"]
    def extract(*_):
        assert client.post(f"/api/v1/tasks/{ident}/stop").status_code == 200
        assert client.post(f"/api/v1/tasks/{ident}/rerun").status_code == 200
        return {"data": {"text": "stale answer"}, "token_usage": 3}
    monkeypatch.setattr("app.worker.tasks.extract_structured", extract)
    assert execute_task(ident)["status"] == "stopped"
    detail = client.get(f"/api/v1/tasks/{ident}").json()
    assert detail["status"] == "pending"
    assert detail["results"] == []
    runs = client.get(f"/api/v1/tasks/{ident}/runs").json()
    assert [run["status"] for run in runs] == ["pending", "stopped"]


@pytest.mark.parametrize("cadence,weekday,expected_next,expected_previous", [
    ("daily", 0, datetime(2026, 11, 6, 1), datetime(2026, 11, 5, 1)),
    ("weekly", 0, datetime(2026, 11, 9, 1), datetime(2026, 11, 2, 1)),
])
def test_schedule_timezone_boundaries(cadence, weekday, expected_next, expected_previous):
    plan = SimpleNamespace(cadence=cadence, weekday=weekday, time_of_day="09:00", timezone="Asia/Shanghai")
    now = datetime(2026, 11, 5, 1)
    assert slot(plan, now) == expected_next
    assert slot(plan, now, False) == expected_previous


def prepare_due(client, fake_crawl):
    task = create(client, config={"schedule": "daily", "schedule_time": "09:00"})
    execute_task(task["id"])
    with SessionLocal() as db:
        db.get(TaskSchedule, UUID(task["id"])).next_run_at = datetime(2026, 11, 1, 1)
        db.commit()
    return task["id"]


def test_scheduler_catches_only_latest_slot_once(client, fake_crawl):
    ident = prepare_due(client, fake_crawl)
    now = datetime(2026, 11, 5, 2)
    assert dispatch_due(now)["dispatched"] == 1
    assert dispatch_due(now)["dispatched"] == 0
    with SessionLocal() as db:
        assert db.query(TaskRun).filter(TaskRun.task_id == UUID(ident)).count() == 2
        assert db.query(ScheduleEvent).filter(ScheduleEvent.task_id == UUID(ident)).one().scheduled_at == datetime(2026, 11, 5, 1)
        assert db.get(TaskSchedule, UUID(ident)).next_run_at == datetime(2026, 11, 6, 1)


def test_scheduler_skips_overlap_and_pause_resume(client):
    task = create(client, config={"schedule": "daily"})
    ident = UUID(task["id"])
    with SessionLocal() as db:
        db.get(TaskSchedule, ident).next_run_at = datetime(2026, 11, 5, 1)
        db.commit()
    assert dispatch_due(datetime(2026, 11, 5, 2))["skipped"] == 1
    assert len(client.get(f"/api/v1/tasks/{ident}/runs").json()) == 1
    response = client.put(f"/api/v1/tasks/{ident}/schedule", json={"schedule": "weekly", "schedule_weekday": 2, "paused": True})
    assert response.status_code == 200 and response.json()["next_run_at"] is None
    assert dispatch_due(datetime(2030, 1, 1))["dispatched"] == 0
    response = client.put(f"/api/v1/tasks/{ident}/schedule", json={"schedule": "weekly", "schedule_weekday": 2, "paused": False})
    assert response.json()["next_run_at"].endswith("Z")


def test_scheduler_multiple_instances_do_not_duplicate_run(client, fake_crawl):
    ident = prepare_due(client, fake_crawl)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: dispatch_due(datetime(2026, 11, 5, 2)), range(2)))
    with SessionLocal() as db:
        assert db.query(TaskRun).filter(TaskRun.task_id == UUID(ident)).count() == 2
        assert db.query(ScheduleEvent).filter(ScheduleEvent.task_id == UUID(ident)).count() == 1


def test_scheduler_recovers_committed_unsent_run(client, monkeypatch):
    task = create(client)
    sent = []
    cleared = []
    monkeypatch.setattr("app.crawler.dedupe.clear_cancel", lambda ident: cleared.append(ident))
    def enqueue(task, db):
        run = latest_run(db, task.id)
        sent.append(str(run.id))
        run.dispatched_at = datetime.utcnow()
        db.commit()
    monkeypatch.setattr("app.api.tasks.enqueue", enqueue)
    dispatch_due()
    dispatch_due()
    assert len(sent) == 1
    assert cleared == [task["id"]]


def test_scheduler_does_not_start_new_runs_for_disabled_owner(client, fake_crawl):
    from app.models import User
    ident = prepare_due(client, fake_crawl)
    with SessionLocal() as db:
        task = db.get(Task, UUID(ident))
        db.get(User, task.owner_id).is_active = False
        db.commit()
    result = dispatch_due(datetime(2026, 11, 5, 2))
    assert result["dispatched"] == 0 and result["skipped"] == 1
    with SessionLocal() as db:
        assert "禁用" in db.query(ScheduleEvent).filter(ScheduleEvent.task_id == UUID(ident)).one().reason


def test_one_page_preview_is_independent_and_never_scheduled(client):
    task = create(client, mode="site", config={"is_preview": True, "max_pages": 200, "max_depth": 5, "schedule": "daily"})
    assert task["mode"] == "single"
    assert task["config"]["max_pages"] == 1 and task["config"]["max_depth"] == 0
    assert task["config"]["schedule"] == "once"
    assert client.get(f"/api/v1/tasks/{task['id']}/schedule").json() is None
    assert client.get(f"/api/v1/tasks/{task['id']}/runs").json()[0]["trigger"] == "preview"


def test_retry_failed_pages_preserves_successes_and_prior_failures(client, fake_crawl, monkeypatch):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    with SessionLocal() as db:
        task = Task(owner_id=owner_id, target_url="https://example.com/", status="failed", prompt="extract", config={})
        db.add(task)
        db.flush()
        db.add_all([
            CrawlResult(task_id=task.id, source_url="https://example.com/ok", raw_text="keep raw", extracted_data={"value": "keep success"}, token_usage=7),
            CrawlResult(task_id=task.id, source_url="https://example.com/bad-fetch", status="fetch_failed", error_message="HTTP failure"),
            CrawlResult(task_id=task.id, source_url="https://example.com/bad-ai", raw_text="retry AI raw", status="ai_failed", error_message="AI failure"),
        ])
        db.commit()
        ident = str(task.id)
    seen = []
    def extract(content, prompt):
        seen.append(content)
        return {"data": {"text": "fixed"}, "token_usage": 3}
    monkeypatch.setattr("app.worker.tasks.extract_structured", extract)
    assert client.post(f"/api/v1/tasks/{ident}/retry-failed").status_code == 200
    assert execute_task(ident)["status"] == "completed"
    assert fake_crawl == ["https://example.com/bad-fetch"]
    assert len(seen) == 2 and "keep raw" not in seen
    detail = client.get(f"/api/v1/tasks/{ident}").json()
    assert detail["token_usage"] == 6
    assert detail["results"][0]["extracted_data"] == {"value": "keep success"}
    runs = client.get(f"/api/v1/tasks/{ident}/runs").json()
    old = client.get(f"/api/v1/tasks/{ident}", params={"run_id": runs[1]["id"]}).json()
    assert old["results"][1]["status"] == "fetch_failed"
    assert old["results"][2]["error_message"] == "AI failure"


def test_migration_is_idempotent_and_preserves_legacy_results(client):
    import runpy
    from pathlib import Path
    from app.database import engine
    from app.models import PublishedPage, ServiceHeartbeat, TaskTemplate
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    # Emulate the pre-P1 database: remove only the new, empty tables in this test DB.
    for model in [TemplateVersion, TaskTemplate, ScheduleEvent, TaskSchedule, TaskRun, ServiceHeartbeat]:
        model.__table__.drop(engine)
    with SessionLocal() as db:
        task = Task(owner_id=owner_id, target_url="https://example.com/", status="completed", prompt="legacy prompt", config={"schedule": "daily"})
        db.add(task)
        db.flush()
        db.add(CrawlResult(task_id=task.id, source_url=task.target_url, raw_text="legacy body"))
        db.add(PublishedPage(task_id=task.id, title="legacy", html="<h1>legacy public page</h1>"))
        db.commit()
        ident = str(task.id)
    path = str(Path(__file__).resolve().parents[1] / "api" / "scripts" / "migrate_v2.py")
    runpy.run_path(path, run_name="__main__")
    runpy.run_path(path, run_name="__main__")
    with SessionLocal() as db:
        assert db.query(TaskRun).count() == 1
        assert db.query(CrawlResult).count() == 1 and db.query(PublishedPage).count() == 1
        assert db.query(TaskSchedule).count() == 0  # Legacy config must not silently activate paid recurring work.
        assert db.query(TaskRun).one().snapshot["results"][0]["raw_text"] == "legacy body"


def test_service_status_and_duplicate_fields(client):
    assert client.get("/api/v1/service-status").json()["scheduler_online"] is False
    dispatch_due()
    assert client.get("/api/v1/service-status").json()["scheduler_online"] is True
    fields = [{"name": "x", "type": "string"}, {"name": "x", "type": "number"}]
    assert client.post("/api/v1/templates", json={"name": "x", "prompt": "x", "fields": fields}).status_code == 422
    assert client.post("/api/v1/tasks", json={"target_url": "https://example.com", "config": {"output_fields": fields}}).status_code == 422


def test_task_center_filters_pagination_and_owner_scope(client, account_factory):
    other = account_factory("other", "member")
    for number in range(15):
        create(client, config={"name": f"Price {number}", "tags": ["catalog"]})
    with SessionLocal() as db:
        db.add(Task(owner_id=other.id, target_url="https://other.example/", config={"name": "private report"}))
        db.commit()
    body = client.get("/api/v1/task-center", params={"q": "price", "limit": 12}).json()
    assert body["total"] == 15 and len(body["items"]) == 12
    assert len(client.get("/api/v1/task-center", params={"q": "price", "offset": 12}).json()["items"]) == 3
    assert client.get("/api/v1/task-center", params={"q": "catalog"}).json()["total"] == 15
    assert client.get("/api/v1/task-center", params={"domain": "example.com"}).json()["total"] == 15
    token = client.post("/api/v1/auth/login", json={"username": "other", "password": "test-password-123"}).json()["access_token"]
    scoped = client.get("/api/v1/task-center", headers={"Authorization": "Bearer " + token}).json()
    assert scoped["total"] == 1 and scoped["scope"] == "own"


def test_templates_are_private_and_versioned(client, account_factory):
    presets = client.get("/api/v1/templates").json()
    assert len(presets) == 5 and all(row["is_system"] for row in presets)
    first = client.post("/api/v1/templates", json={"name": "My template", "prompt": "original", "fields": []}).json()
    updated = client.put(f"/api/v1/templates/{first['id']}", json={"name": "My template", "prompt": "updated", "fields": []}).json()
    assert updated["version"] == 2
    task = create(client, prompt="original", config={"template_id": first["id"], "template_version": 1})
    assert task["config"]["template_version"] == 1
    with SessionLocal() as db:
        assert db.query(TemplateVersion).filter(TemplateVersion.template_id == UUID(first["id"])).count() == 2
    account_factory("other", "member")
    token = client.post("/api/v1/auth/login", json={"username": "other", "password": "test-password-123"}).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    assert first["id"] not in [row["id"] for row in client.get("/api/v1/templates", headers=headers).json()]
    assert client.post("/api/v1/tasks", headers=headers, json={"target_url": "https://example.com", "config": {"template_id": first["id"]}}).status_code == 404
    assert client.put(f"/api/v1/templates/{first['id']}", headers=headers, json={"name": "hijack", "prompt": "x"}).status_code == 404


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_new_endpoints_keep_task_permission_boundaries(client, account_factory, role):
    task = create(client)
    account_factory("other", role)
    token = client.post("/api/v1/auth/login", json={"username": "other", "password": "test-password-123"}).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    base = f"/api/v1/tasks/{task['id']}"
    assert client.get(base + "/runs", headers=headers).status_code == 404
    assert client.get(base + "/schedule", headers=headers).status_code == 404
    expected = 403 if role == "viewer" else 404
    assert client.put(base + "/schedule", headers=headers, json={"schedule": "daily"}).status_code == expected
    assert client.post(base + "/reextract", headers=headers, json={"prompt": "x"}).status_code == expected


def test_output_validation_rules():
    from app.templates import validate_output
    fields = [{"name": "price", "type": "number", "required": True}, {"name": "date", "type": "date", "required": False}]
    assert not validate_output({"price": 0, "date": "2026-11-05"}, fields)
    assert validate_output({"price": True}, fields)
    assert validate_output({}, fields)
    assert validate_output({"price": 3, "date": "bad"}, fields)


def test_map_reduce_budget_stops_further_calls_and_counts_partial_usage(monkeypatch):
    from app.ai import extractor
    import httpx
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": '{"part":1}'}}], "usage": {"total_tokens": 5}}
    def post(*args, **kwargs):
        calls.append(1)
        return Response()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(httpx.Client, "post", post)
    monkeypatch.setattr(extractor, "AI_CHUNK_CHARS", 500)
    with extractor.token_budget(1) as meter:
        with pytest.raises(extractor.AIBudgetError):
            extractor.extract_structured("x" * 2000, "extract")
    assert len(calls) == 1 and meter["spent"] == 5
