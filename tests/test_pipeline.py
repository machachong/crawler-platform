"""End-to-end crawl against a local site, plus a public quotes page."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.crawler.engine import fetch_and_clean_sync
from app.worker.tasks import execute_task

PAGES = {
    "/robots.txt": "User-agent: *\nDisallow: /private\n",
    "/": """
        <html><body><main>
          <h1>Demo Shop</h1>
          <p>Apple costs 3 dollars. Banana costs 2 dollars.</p>
          <a href="/public">public page</a>
          <a href="/private">private page</a>
          <a href="https://example.com/ad">ad</a>
        </main></body></html>
    """,
    "/public": """
        <html><body><main>
          <h1>About</h1>
          <p>We sell fruit.</p>
          <a href="/public/extra">more</a>
        </main></body></html>
    """,
    "/public/extra": "<html><body><main><p>too deep</p></main></body></html>",
    "/private": "<html><body><main><p>secret</p></main></body></html>",
}

LEGACY_PAGES = {
    "/gbk": ('<meta charset="gbk"><main><p>中文采集内容与预览</p></main>', "gbk", "text/html"),
    "/wrong-charset": ('<meta charset="gb18030"><main><p>中文采集内容与预览</p></main>',
                      "gb18030", "text/html; charset=utf-8"),
    "/big5": ("<main><p>繁體中文採集內容與預覽</p></main>", "big5", "text/html; charset=big5"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        body, encoding, content_type = LEGACY_PAGES.get(path, (
            PAGES.get(path), "utf-8", "text/plain" if path == "/robots.txt" else "text/html; charset=utf-8",
        ))
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode(encoding)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


@pytest.mark.parametrize(("path", "expected"), [
    ("/gbk", "中文采集内容与预览"),
    ("/wrong-charset", "中文采集内容与预览"),
    ("/big5", "繁體中文採集內容與預覽"),
])
def test_legacy_page_charsets_survive_crawl_and_html_preview(client, site, path, expected):
    created = client.post("/api/v1/tasks", json={
        "target_url": site + path,
        "config": {"respect_robots": False, "download_delay": 0},
    })
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    assert execute_task(task_id)["status"] == "completed"
    task_path = f"/api/v1/tasks/{task_id}"
    assert expected in client.get(task_path).json()["results"][0]["raw_text"]
    preview = client.get(task_path + "/preview")
    assert preview.status_code == 200
    assert "charset=utf-8" in preview.headers["content-type"]
    assert expected in preview.content.decode("utf-8", errors="strict")


def test_site_mode_bfs_robots_and_ai(client, site, monkeypatch):
    calls = []

    def fake_extract(content, prompt):
        assert prompt == "总结页面在卖什么，返回 JSON。"
        calls.append(content)
        return {"data": {"summary": "fruit"}, "token_usage": 7}

    monkeypatch.setattr("app.worker.tasks.extract_structured", fake_extract)
    created = client.post(
        "/api/v1/tasks",
        json={
            "target_url": site + "/",
            "mode": "site",
            "prompt": "总结页面在卖什么，返回 JSON。",
            "config": {
                "max_depth": 1,
                "max_pages": 10,
                "concurrency": 2,
                "respect_robots": True,
                "engine": "http",
                "download_delay": 0,
                "keywords": ["fruit"],
            },
        },
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    outcome = execute_task(task_id)
    assert outcome["status"] == "completed"

    detail = client.get(f"/api/v1/tasks/{task_id}").json()
    urls = [row["source_url"] for row in detail["results"]]
    assert any(url.rstrip("/").endswith(":{}".format(site.rsplit(":", 1)[-1])) or url.rstrip("/") == site.rstrip("/") for url in urls)
    assert any(url.endswith("/public") for url in urls)
    assert not any("/private" in url for url in urls)
    assert not any("example.com" in url for url in urls)
    assert not any(url.endswith("/public/extra") for url in urls)
    assert len(calls) == 1
    assert "fruit" in calls[0].lower()
    public = next(row for row in detail["results"] if row["source_url"].endswith("/public"))
    assert public["extracted_data"] == {"summary": "fruit"}
    assert public["token_usage"] == 7
    home = next(row for row in detail["results"] if row["depth"] == 0)
    assert home["extracted_data"]["skipped"] is True
    assert detail["token_usage"] == 7
    assert detail["status"] == "completed"
    published = client.post(f"/api/v1/tasks/{task_id}/publication", json={"title": "本地采集报告"})
    assert published.status_code == 200, published.text
    page = client.get(f"/p/{published.json()['slug']}.html", headers={"Authorization": ""})
    assert page.status_code == 200
    assert "fruit" in page.text
    assert "<title>本地采集报告</title>" in page.text


def test_single_page_without_api_key_keeps_clean_text(client, site, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    created = client.post(
        "/api/v1/tasks",
        json={
            "target_url": site + "/",
            "mode": "single",
            "prompt": "提取商品和价格，返回 JSON。",
            "config": {"respect_robots": True, "engine": "http", "download_delay": 0},
        },
    )
    task_id = created.json()["id"]
    outcome = execute_task(task_id)
    assert outcome["status"] == "failed"
    detail = client.get(f"/api/v1/tasks/{task_id}").json()
    assert "OPENAI_API_KEY" in (detail["error_message"] or "")
    assert len(detail["results"]) == 1
    assert "Apple" in detail["results"][0]["raw_text"]
    assert detail["results"][0]["source_url"].startswith(site)


def test_quotes_to_scrape_markdown_contains_quote():
    text = fetch_and_clean_sync("https://quotes.toscrape.com/")
    assert text
    assert "Albert Einstein" in text or "Einstein" in text
