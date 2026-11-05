"""Runtime configuration loaded from the environment."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://user:pass@localhost:5432/crawler_db"
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")
AI_CHUNK_CHARS = int(os.getenv("AI_CHUNK_CHARS", "12000"))
AI_CHUNK_OVERLAP = int(os.getenv("AI_CHUNK_OVERLAP", "400"))
CRAWLER_USER_AGENT = os.getenv(
    "CRAWLER_USER_AGENT",
    "CrawlerPlatformBot/1.0 (+https://github.com/machachong/crawler-platform; respects robots.txt)",
)
DISABLE_CELERY = os.getenv("DISABLE_CELERY", "0") == "1"


def openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()
