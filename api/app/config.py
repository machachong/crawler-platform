"""Runtime configuration loaded from the environment."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

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
AUTH_SESSION_HOURS = int(os.getenv("AUTH_SESSION_HOURS", "8"))
if not 1 <= AUTH_SESSION_HOURS <= 168:
    raise ValueError("AUTH_SESSION_HOURS must be between 1 and 168")
CORS_ORIGINS = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",") if origin.strip()]
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
if PUBLIC_BASE_URL:
    _public_url = urlsplit(PUBLIC_BASE_URL)
    if (_public_url.scheme not in {"http", "https"} or not _public_url.hostname
            or _public_url.username or _public_url.password or _public_url.query or _public_url.fragment):
        raise ValueError("PUBLIC_BASE_URL must be an http(s) public base URL without credentials/query/fragment")


def openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()
