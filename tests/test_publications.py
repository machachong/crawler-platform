from uuid import UUID
import re

import pytest

from app.database import SessionLocal
from app.models import CrawlResult, PublishedPage, Task
from conftest import login_request


@pytest.fixture()
def result_task(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    with SessionLocal() as db:
        task = Task(owner_id=owner_id, target_url="https://example.com/", status="completed",
                    prompt="private-prompt-marker", error_message="private-error-marker",
                    progress={"logs": [{"message": "private-log-marker"}]},
                    config={"page_title": "行业观察", "private": "private-config-marker"})
        db.add(task)
        db.flush()
        db.add(CrawlResult(task_id=task.id, source_url="https://example.com/article",
                           raw_text="private-raw-marker", extracted_data={"text": "# 本周观察\n\n**核心结论**\n\n- 第一条\n- 第二条"}))
        db.commit()
        return str(task.id)


def test_preview_is_private_and_does_not_publish(client, result_task):
    path = f"/api/v1/tasks/{result_task}"
    response = client.get(path + "/preview", params={"title": "预览标题"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>预览标题</title>" in response.text
    assert re.search(r'<h2 id="section-\d+">本周观察</h2>', response.text)
    assert "<strong>核心结论</strong>" in response.text
    assert "<ul><li>第一条</li><li>第二条</li></ul>" in re.sub(r">\s+<", "><", response.text)
    for secret in ["private-prompt-marker", "private-error-marker", "private-log-marker",
                   "private-config-marker", "private-raw-marker"]:
        assert secret not in response.text
    assert client.get(path + "/publication").json() is None
    assert client.get(path + "/preview", headers={"Authorization": ""}).status_code == 401
    assert client.get(path + "/publication", headers={"Authorization": ""}).status_code == 401


def test_publish_anonymous_html_and_configured_domain(client, result_task, monkeypatch):
    monkeypatch.setattr("app.api.publications.PUBLIC_BASE_URL", "https://www.zhengaihe.top/crawler")
    path = f"/api/v1/tasks/{result_task}"
    response = client.post(path + "/publication", json={"title": "每周报告"})
    assert response.status_code == 200, response.text
    publication = response.json()
    assert publication["url"] == f"https://www.zhengaihe.top/crawler/{publication['slug']}"
    assert client.get(path + "/publication").json() == publication
    public = client.get(f"/p/{publication['slug']}.html", headers={"Authorization": ""})
    assert public.status_code == 200
    assert "<title>每周报告</title>" in public.text
    assert public.headers["cache-control"] == "no-store"
    assert public.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in public.headers["content-security-policy"]
    assert public.headers["x-robots-tag"] == "noindex, nofollow"
    canonical = client.get(f"/crawler/{publication['slug']}", headers={"Authorization": ""})
    assert canonical.status_code == 200
    assert canonical.text == public.text
    assert client.get("/crawler/not-a-hashid").status_code == 404


def test_snapshot_survives_rerun_and_updates_only_on_publish(client, result_task):
    path = f"/api/v1/tasks/{result_task}"
    first = client.post(path + "/publication", json={}).json()
    public_path = f"/p/{first['slug']}.html"
    before = client.get(public_path).text
    assert first["url"] == f"http://testserver/p/{first['slug']}"
    assert client.post(path + "/rerun").status_code == 200
    assert client.get(public_path).text == before
    assert client.post(path + "/publication", json={}).status_code == 409
    with SessionLocal() as db:
        task = db.get(Task, UUID(result_task))
        task.status = "completed"
        db.add(CrawlResult(task_id=task.id, source_url="https://example.com/new", extracted_data={"text": "新一版结果"}))
        db.commit()
    assert client.get(public_path).text == before
    second = client.post(path + "/publication", json={"title": "新标题"}).json()
    assert second["slug"] == first["slug"]
    assert second["created_at"] == first["created_at"]
    assert "新一版结果" in client.get(public_path).text
    assert "本周观察" not in client.get(public_path).text


def test_unpublish_and_task_delete_revoke_public_access(client, result_task):
    path = f"/api/v1/tasks/{result_task}"
    first = client.post(path + "/publication", json={}).json()
    assert client.delete(path + "/publication").status_code == 204
    assert client.get(f"/p/{first['slug']}.html").status_code == 404
    assert client.get(f"/crawler/{first['slug']}").status_code == 404
    assert client.get(path + "/publication").json() is None
    assert client.delete(path + "/publication").status_code == 204
    second = client.post(path + "/publication", json={}).json()
    assert second["slug"] != first["slug"]
    assert client.delete(path).status_code == 200
    assert client.get(f"/p/{second['slug']}.html").status_code == 404
    with SessionLocal() as db:
        assert db.query(PublishedPage).count() == 0


@pytest.mark.parametrize("status", ["pending", "crawling", "cleaning", "ai_processing"])
def test_active_tasks_cannot_render_or_publish(client, result_task, status):
    with SessionLocal() as db:
        db.get(Task, UUID(result_task)).status = status
        db.commit()
    path = f"/api/v1/tasks/{result_task}"
    assert client.get(path + "/preview").status_code == 409
    assert client.get(path + "/export?format=html").status_code == 409
    assert client.post(path + "/publication", json={}).status_code == 409


def test_empty_results_rejected_and_existing_publication_preserved(client, result_task):
    path = f"/api/v1/tasks/{result_task}"
    published = client.post(path + "/publication", json={}).json()
    with SessionLocal() as db:
        db.query(CrawlResult).filter(CrawlResult.task_id == UUID(result_task)).delete()
        db.commit()
    assert client.post(path + "/publication", json={}).status_code == 422
    assert client.get(path + "/preview").status_code == 422
    assert client.get(f"/p/{published['slug']}.html").status_code == 200


def test_html_download_and_safe_escaping(client, result_task):
    with SessionLocal() as db:
        row = db.query(CrawlResult).filter(CrawlResult.task_id == UUID(result_task)).one()
        row.source_url = "javascript:alert(1)"
        row.extracted_data = {"text": '<script>alert("xss")</script>\n\n<img src=x onerror=alert(1)>'}
        db.commit()
    path = f"/api/v1/tasks/{result_task}"
    response = client.post(path + "/publication", json={"title": "</title><script>alert(2)</script>"})
    assert response.status_code == 200
    public = client.get(f"/p/{response.json()['slug']}.html")
    assert "<script>" not in public.text
    assert "<img " not in public.text
    assert 'href="javascript:' not in public.text
    assert "&lt;script&gt;" in public.text
    download = client.get(path + "/export?format=html")
    assert download.status_code == 200
    assert download.headers["content-disposition"].endswith('.html"')
    assert download.text.startswith("<!doctype html>")


def test_raw_fallback_table_render_and_skipped_results(client, result_task):
    with SessionLocal() as db:
        task = db.get(Task, UUID(result_task))
        task.status = "failed"
        row = db.query(CrawlResult).filter(CrawlResult.task_id == task.id).one()
        row.extracted_data = None
        row.raw_text = "保存的正文"
        db.add_all([
            CrawlResult(task_id=task.id, source_url="https://example.com/table", extracted_data=[{"name": "Apple", "price": 3}, {"name": "Banana", "price": 2}]),
            CrawlResult(task_id=task.id, source_url="https://example.com/skip", raw_text="skipped-secret", extracted_data={"skipped": True}),
            CrawlResult(task_id=task.id, source_url="https://example.com/error", raw_text="error-secret", extracted_data={"error": "provider-error"}),
            CrawlResult(task_id=task.id, source_url="https://example.com/zero", extracted_data=0),
            CrawlResult(task_id=task.id, source_url="https://example.com/no-matches", raw_text="empty-result-private", extracted_data=[]),
        ])
        db.commit()
    response = client.get(f"/api/v1/tasks/{result_task}/preview")
    assert response.status_code == 200
    assert "保存的正文" in response.text
    assert "<table>" in response.text and "<td>Apple</td>" in response.text
    assert "<p>0</p>" in response.text
    assert "skipped-secret" not in response.text
    assert "error-secret" not in response.text
    assert "provider-error" not in response.text
    assert "empty-result-private" not in response.text


@pytest.mark.parametrize("role,expected", [("member", 404), ("viewer", 403)])
def test_publication_permissions(client, result_task, account_factory, role, expected):
    user = account_factory("other", role)
    response = login_request(client, "other", "test-password-123")
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    path = f"/api/v1/tasks/{result_task}"
    assert client.get(path + "/preview", headers=headers).status_code == 404
    assert client.get(path + "/publication", headers=headers).status_code == 404
    assert client.get(path + "/export?format=html", headers=headers).status_code == 404
    assert client.post(path + "/publication", headers=headers, json={}).status_code == expected
    assert client.delete(path + "/publication", headers=headers).status_code == expected
    with SessionLocal() as db:
        db.get(Task, UUID(result_task)).owner_id = user.id
        db.commit()
    assert client.get(path + "/preview", headers=headers).status_code == 200
    assert client.post(path + "/publication", headers=headers, json={}).status_code == (403 if role == "viewer" else 200)


def test_anonymous_cannot_publish_or_unpublish(anonymous_client):
    path = "/api/v1/tasks/00000000-0000-0000-0000-000000000001/publication"
    assert anonymous_client.post(path, json={}).status_code == 401
    assert anonymous_client.delete(path).status_code == 401


def test_title_validation_and_prompt_persistence(client):
    response = client.post("/api/v1/tasks", json={"target_url": "https://example.com/",
        "prompt": "按照我的要求生成文章", "config": {"page_title": "我的采集文章"}})
    assert response.status_code == 200
    task = client.get(f"/api/v1/tasks/{response.json()['id']}").json()
    assert task["prompt"] == "按照我的要求生成文章"
    assert task["config"]["page_title"] == "我的采集文章"
    assert client.post(f"/api/v1/tasks/{task['id']}/publication", json={"title": "a" * 121}).status_code == 422


def test_markdown_code_blocks_keep_untrusted_markup_inert():
    from app.publishing import render_text

    html = render_text('## 标题\n\n1. 第一项\n2. 第二项\n\n```html\n<script>x</script>\n```')
    assert re.search(r'<h3 id="section-\d+">标题</h3>', html)
    assert "<ol><li>第一项</li><li>第二项</li></ol>" in re.sub(r">\s+<", "><", html)
    assert "<pre><code>&lt;script&gt;x&lt;/script&gt;</code></pre>" in html
