import os

# Force a local sqlite database before the app package is imported.
os.environ["DISABLE_CELERY"] = "1"
os.environ["DATABASE_URL"] = "sqlite:////tmp/crawler_platform_test_84f0.db"
os.environ["OPENAI_API_KEY"] = ""
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"

import pytest


@pytest.fixture(autouse=True)
def _clean_database():
    from app.database import SessionLocal, init_db
    from app.models import Base

    init_db()
    db = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
