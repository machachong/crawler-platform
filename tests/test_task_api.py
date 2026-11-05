def test_postgres_url_uses_psycopg2():
    from app.database import _normalize_url

    assert _normalize_url("postgresql://user:pass@db:5432/crawler_db").startswith("postgresql+psycopg2://")
    assert _normalize_url("sqlite:///tmp/x.db") == "sqlite:///tmp/x.db"


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] is True
    assert body["ai_configured"] is False
    assert body["scrapling"]


def test_task_lifecycle_and_export(client):
    created = client.post(
        "/api/v1/tasks",
        json={
            "target_url": "https://quotes.toscrape.com/",
            "mode": "single",
            "prompt": "提取名言，返回 JSON。",
            "config": {"max_depth": 4, "max_pages": 30, "concurrency": 3, "respect_robots": True},
        },
    )
    assert created.status_code == 200
    task = created.json()
    assert task["status"] == "pending"
    assert task["target_url"] == "https://quotes.toscrape.com/"
    assert task["config"]["max_depth"] == 0
    assert task["config"]["max_pages"] == 1
    task_id = task["id"]

    listed = client.get("/api/v1/tasks")
    assert listed.status_code == 200
    assert any(item["id"] == task_id for item in listed.json())

    fetched = client.get(f"/api/v1/tasks/{task_id}")
    assert fetched.status_code == 200
    assert fetched.json()["results"] == []

    stopped = client.post(f"/api/v1/tasks/{task_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"

    rerun = client.post(f"/api/v1/tasks/{task_id}/rerun")
    assert rerun.status_code == 200
    assert rerun.json()["status"] == "pending"

    exported = client.get(f"/api/v1/tasks/{task_id}/export", params={"format": "json"})
    assert exported.status_code == 200
    assert exported.json()["id"] == task_id

    csv_export = client.get(f"/api/v1/tasks/{task_id}/export", params={"format": "csv"})
    assert csv_export.status_code == 200
    assert "source_url" in csv_export.text

    md_export = client.get(f"/api/v1/tasks/{task_id}/export", params={"format": "md"})
    assert md_export.status_code == 200
    assert "抓取任务" in md_export.text

    deleted = client.delete(f"/api/v1/tasks/{task_id}")
    assert deleted.status_code == 200
    missing = client.get(f"/api/v1/tasks/{task_id}")
    assert missing.status_code == 404


def test_rejects_bad_depth_and_metadata_ip(client):
    invalid = client.post(
        "/api/v1/tasks",
        json={"target_url": "https://quotes.toscrape.com/", "mode": "site", "config": {"max_depth": 9}},
    )
    assert invalid.status_code == 422

    blocked = client.post(
        "/api/v1/tasks",
        json={"target_url": "http://169.254.169.254/latest/meta-data", "mode": "single"},
    )
    assert blocked.status_code == 400


def test_rerun_conflict_while_pending(client):
    created = client.post(
        "/api/v1/tasks",
        json={"target_url": "https://quotes.toscrape.com/tag/life/", "mode": "site", "prompt": "总结"},
    )
    task_id = created.json()["id"]
    again = client.post(f"/api/v1/tasks/{task_id}/rerun")
    assert again.status_code == 409
