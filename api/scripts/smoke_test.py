"""Create a single-page task and wait until it finishes.

Usage (API already running):
    python scripts/smoke_test.py

Environment:
    API_BASE   default http://localhost:8000/api/v1
"""

from __future__ import annotations

import base64
import json
import getpass
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1").rstrip("/")
TARGET = os.getenv("SMOKE_URL", "https://quotes.toscrape.com/")
PROMPT = os.getenv(
    "SMOKE_PROMPT",
    "提取页面中的所有名言，返回 JSON 数组，每项包含 text 和 author。",
)


def main() -> int:
    with httpx.Client(timeout=30.0) as client:
        health = client.get(f"{API_BASE}/health")
        health.raise_for_status()
        print("health", json.dumps(health.json(), ensure_ascii=False))

        username = os.getenv("SMOKE_USERNAME") or input("Username: ")
        password = os.getenv("SMOKE_PASSWORD") or getpass.getpass("Password: ")
        challenge = client.get(f"{API_BASE}/auth/captcha")
        challenge.raise_for_status()
        captcha = challenge.json()
        with tempfile.TemporaryDirectory(prefix="crawler-smoke-") as folder:
            image = Path(folder) / "captcha.png"
            image.write_bytes(base64.b64decode(captcha["image"].split(",", 1)[1], validate=True))
            code = input(f"Open {image} and enter its 5 characters: ").strip()
        signed_in = client.post(f"{API_BASE}/auth/login", json={
            "username": username, "password": password, "captcha_id": captcha["captcha_id"], "captcha_code": code,
        })
        signed_in.raise_for_status()
        client.headers["Authorization"] = "Bearer " + signed_in.json()["access_token"]

        created = client.post(
            f"{API_BASE}/tasks",
            json={
                "target_url": TARGET,
                "mode": "single",
                "prompt": PROMPT,
                "config": {
                    "respect_robots": True,
                    "engine": "http",
                    "concurrency": 1,
                    "download_delay": 0.2,
                },
            },
        )
        if created.status_code >= 400:
            print(created.status_code, created.text)
            return 1
        task_id = created.json()["id"]
        print("created", task_id)

        payload = {}
        for _ in range(90):
            current = client.get(f"{API_BASE}/tasks/{task_id}")
            current.raise_for_status()
            payload = current.json()
            progress = payload.get("progress") or {}
            print(payload["status"], progress.get("percent"), payload.get("error_message") or "")
            if payload["status"] in {"completed", "failed", "stopped"}:
                break
            time.sleep(2)
        else:
            print("timeout waiting for task")
            return 1

        results = payload.get("results") or []
        print("pages", len(results))
        if not results or not (results[0].get("raw_text") or "").strip():
            print("crawl produced no cleaned text")
            return 1
        print(results[0]["raw_text"][:400])
        print("extracted", json.dumps(results[0].get("extracted_data"), ensure_ascii=False)[:500])

        error = payload.get("error_message") or ""
        if payload["status"] == "failed" and "OPENAI_API_KEY" in error:
            print("crawl+clean stored; AI skipped because OPENAI_API_KEY is empty")
            return 0
        if payload["status"] != "completed":
            return 1
        print("task completed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
