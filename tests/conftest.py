import os
import tempfile
from pathlib import Path

_test_dir = tempfile.TemporaryDirectory(prefix="crawler-tests-")

# Force a local sqlite database before the app package is imported.
os.environ["DISABLE_CELERY"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(_test_dir.name) / "test.db").as_posix()
os.environ["OPENAI_API_KEY"] = ""
os.environ["PUBLIC_BASE_URL"] = ""
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"

import pytest


@pytest.fixture(scope="session", autouse=True)
def _close_database_at_exit():
    yield
    from app.database import engine

    engine.dispose()
    _test_dir.cleanup()


@pytest.fixture(autouse=True)
def _clean_database(monkeypatch):
    from app.database import SessionLocal, init_db
    from app.models import Base

    # CRUD tests exercise queue dispatch separately from the worker pipeline.
    # Pipeline tests call execute_task explicitly against their local HTTP fixture.
    monkeypatch.setattr("app.api.tasks.enqueue", lambda *_: None)

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
def anonymous_client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def password_hash():
    from app.auth import hash_password

    return hash_password("test-password-123")


@pytest.fixture()
def account_factory(password_hash):
    from app.database import SessionLocal
    from app.models import User

    def create(username="admin", role="admin", is_active=True):
        with SessionLocal() as db:
            user = User(username=username, role=role, is_active=is_active, password_hash=password_hash)
            db.add(user)
            db.commit()
            db.refresh(user)
            db.expunge(user)
            return user
    return create


@pytest.fixture()
def client(anonymous_client, account_factory):
    account_factory()
    response = anonymous_client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"})
    assert response.status_code == 200
    anonymous_client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return anonymous_client
