from datetime import datetime, timedelta

import pytest

from conftest import captcha_payload, login_request


def sign_in(client, username="admin", password="test-password-123"):
    response = login_request(client, username, password)
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_password_storage():
    from app.auth import hash_password, verify_password

    first = hash_password("correct horse battery staple")
    assert first != hash_password("correct horse battery staple")
    assert "correct horse" not in first
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong", first)
    assert not verify_password("wrong", "invalid")


@pytest.mark.parametrize("method,path", [
    ("get", "/tasks"), ("post", "/tasks"), ("get", "/users"),
    ("get", "/tasks/00000000-0000-0000-0000-000000000001"),
    ("get", "/tasks/00000000-0000-0000-0000-000000000001/results"),
    ("get", "/tasks/00000000-0000-0000-0000-000000000001/export"),
    ("post", "/tasks/00000000-0000-0000-0000-000000000001/stop"),
    ("post", "/tasks/00000000-0000-0000-0000-000000000001/rerun"),
    ("delete", "/tasks/00000000-0000-0000-0000-000000000001"),
])
def test_anonymous_cannot_access(anonymous_client, method, path):
    assert getattr(anonymous_client, method)("/api/v1" + path).status_code == 401


def test_login_errors_and_no_password_echo(anonymous_client, account_factory):
    account_factory()
    account_factory("disabled", is_active=False)
    for username in ["admin", "unknown", "disabled"]:
        response = login_request(anonymous_client, username, "wrong")
        assert response.status_code == 401
        assert response.json()["detail"] == "账号或密码错误"
    secret = "p" * 129
    response = login_request(anonymous_client, "admin", secret)
    assert response.status_code == 422
    assert secret not in response.text


def test_sessions_expiry_logout_and_hash_storage(anonymous_client, account_factory):
    from app.auth import token_digest
    from app.database import SessionLocal
    from app.models import AuthSession

    account_factory()
    headers = sign_in(anonymous_client, "ADMIN")
    token = headers["Authorization"].split()[1]
    with SessionLocal() as db:
        session = db.get(AuthSession, token_digest(token))
        assert session is not None
        assert session.token_hash != token
    assert anonymous_client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert anonymous_client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert anonymous_client.get("/api/v1/auth/me", headers=headers).status_code == 401
    headers = sign_in(anonymous_client)
    with SessionLocal() as db:
        db.query(AuthSession).update({AuthSession.expires_at: datetime.utcnow() - timedelta(seconds=1)})
        db.commit()
    assert anonymous_client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert anonymous_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_credential_stamp_rejects_stale_session(anonymous_client, account_factory):
    from app.database import SessionLocal
    from app.models import User

    user = account_factory()
    headers = sign_in(anonymous_client)
    # Simulate a login that raced with revocation and kept an old session row.
    with SessionLocal() as db:
        db.query(User).filter(User.id == user.id).update({User.security_stamp: "changed"})
        db.commit()
    assert anonymous_client.get("/api/v1/auth/me", headers=headers).status_code == 401


@pytest.mark.parametrize("suffix,method", [
    ("", "get"), ("/results", "get"), ("/export?format=json", "get"),
    ("/export?format=csv", "get"), ("/export?format=md", "get"),
    ("/stop", "post"), ("/rerun", "post"), ("", "delete"),
])
def test_cross_user_access_denied(anonymous_client, account_factory, monkeypatch, suffix, method):
    from app.api import tasks

    monkeypatch.setattr(tasks, "enqueue", lambda *_: None)
    alice = account_factory("alice", "member")
    bob = account_factory("bob", "member")
    alice_headers = sign_in(anonymous_client, "alice")
    bob_headers = sign_in(anonymous_client, "bob")
    created = anonymous_client.post("/api/v1/tasks", headers=alice_headers, json={
        "target_url": "https://example.com", "owner_id": bob.id,
    })
    assert created.status_code == 200
    assert created.json()["owner_id"] == alice.id
    task_id = created.json()["id"]
    assert anonymous_client.get("/api/v1/tasks", headers=bob_headers).json() == []
    assert getattr(anonymous_client, method)(f"/api/v1/tasks/{task_id}{suffix}", headers=bob_headers).status_code == 404


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_user_management_is_admin_only(anonymous_client, account_factory, role):
    user = account_factory("restricted-user", role)
    headers = sign_in(anonymous_client, "restricted-user")
    assert anonymous_client.get("/api/v1/users", headers=headers).status_code == 403
    assert anonymous_client.post("/api/v1/users", headers=headers, json={
        "username": "unauthorized", "password": "test-password-123", "role": "admin",
    }).status_code == 403
    assert anonymous_client.patch(f"/api/v1/users/{user.id}", headers=headers, json={
        "role": "admin", "password": "reset-password-123",
    }).status_code == 403


def test_admin_manages_users_and_legacy_tasks(client, account_factory):
    from app.database import SessionLocal
    from app.models import Task

    with SessionLocal() as db:
        task = Task(owner_id="local", target_url="https://example.com", config={})
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = str(task.id)
    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 200
    assert len(client.get("/api/v1/tasks").json()) == 1
    created = client.post("/api/v1/users", json={"username": "new-user", "password": "test-password-123", "role": "member"})
    assert created.status_code == 201
    assert "password_hash" not in created.text
    assert "password_hash" not in client.get("/api/v1/users").text
    assert client.post("/api/v1/users", json={"username": "NEW-USER", "password": "test-password-123"}).status_code == 409
    headers = sign_in(client, "new-user")
    assert client.get("/api/v1/users", headers=headers).status_code == 403
    assert client.post("/api/v1/users", headers=headers, json={"username": "hacker", "password": "test-password-123", "role": "admin"}).status_code == 403
    assert client.patch(f"/api/v1/users/{created.json()['id']}", headers=headers, json={"role": "admin"}).status_code == 403
    assert client.get(f"/api/v1/tasks/{task_id}", headers=headers).status_code == 404
    assert client.patch(f"/api/v1/users/{created.json()['id']}", json={"is_active": False}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    own_id = client.get("/api/v1/auth/me").json()["id"]
    assert client.patch(f"/api/v1/users/{own_id}", json={"role": "member"}).status_code == 400
    assert client.patch(f"/api/v1/users/{own_id}", json={"is_active": False}).status_code == 400


def test_viewer_reads_own_tasks_but_cannot_write(anonymous_client, account_factory):
    from app.database import SessionLocal
    from app.models import Task

    viewer = account_factory("viewer", "viewer")
    with SessionLocal() as db:
        task = Task(owner_id=viewer.id, target_url="https://example.com", config={})
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = str(task.id)
    headers = sign_in(anonymous_client, "viewer")
    assert anonymous_client.get(f"/api/v1/tasks/{task_id}", headers=headers).status_code == 200
    assert anonymous_client.get(f"/api/v1/tasks/{task_id}/export", headers=headers).status_code == 200
    assert anonymous_client.post("/api/v1/tasks", headers=headers, json={"target_url": "https://example.com"}).status_code == 403
    for suffix, method in [("/stop", "post"), ("/rerun", "post"), ("", "delete")]:
        assert getattr(anonymous_client, method)(f"/api/v1/tasks/{task_id}{suffix}", headers=headers).status_code == 403


def test_password_change_revokes_all_sessions(anonymous_client, account_factory):
    account_factory()
    first = sign_in(anonymous_client)
    second = sign_in(anonymous_client)
    assert anonymous_client.post("/api/v1/auth/password", headers=first, json={
        "current_password": "wrong", "new_password": "new-password-456",
    }).status_code == 400
    assert anonymous_client.post("/api/v1/auth/password", headers=first, json={
        "current_password": "test-password-123", "new_password": "new-password-456",
    }).status_code == 204
    for headers in [first, second]:
        assert anonymous_client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert login_request(anonymous_client, "admin", "test-password-123").status_code == 401
    assert sign_in(anonymous_client, password="new-password-456")


def test_admin_role_and_password_change_revoke_sessions(client, account_factory):
    user = account_factory("member", "member")
    headers = sign_in(client, "member")
    assert client.patch(f"/api/v1/users/{user.id}", json={"role": "viewer"}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    headers = sign_in(client, "member")
    assert client.get("/api/v1/auth/me", headers=headers).json()["role"] == "viewer"
    assert client.patch(f"/api/v1/users/{user.id}", json={"password": "reset-password-789"}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert sign_in(client, "member", "reset-password-789")


def test_login_throttle_shared_across_clients(anonymous_client):
    from app.database import SessionLocal
    from app.models import LoginThrottle

    for _ in range(10):
        assert login_request(anonymous_client, "unknown", "wrong").status_code == 401
    response = login_request(anonymous_client, "unknown", "wrong")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    with SessionLocal() as db:
        db.query(LoginThrottle).update({LoginThrottle.expires_at: datetime.utcnow() - timedelta(seconds=1)})
        db.commit()
    assert login_request(anonymous_client, "unknown", "wrong").status_code == 401


def test_login_captcha_is_an_image_and_is_single_use(anonymous_client, account_factory):
    account_factory()
    credentials = {"username": "admin", "password": "test-password-123"}
    assert anonymous_client.post("/api/v1/auth/login", json=credentials).status_code == 422
    challenge = anonymous_client.get("/api/v1/auth/captcha")
    assert challenge.headers["cache-control"] == "no-store"
    assert challenge.json()["image"].startswith("data:image/png;base64,iVBOR")
    assert "captcha_code" not in challenge.json()
    credentials["captcha_id"] = challenge.json()["captcha_id"]
    assert anonymous_client.post("/api/v1/auth/login", json={**credentials, "captcha_code": "AAAAA"}).status_code == 400
    assert anonymous_client.post("/api/v1/auth/login", json={**credentials, "captcha_code": "R7K5M"}).status_code == 400
    assert login_request(anonymous_client, "admin", "test-password-123").status_code == 200


def test_registration_uses_its_own_captcha_and_requires_admin_activation(client):
    credentials = {"username": "New-Account", "password": "test-password-123"}
    path = "/api/v1/auth/register"
    assert client.post(path, json=credentials).status_code == 422
    assert client.post(path, json={**credentials, **captcha_payload(client)}).status_code == 400
    challenge = captcha_payload(client, "register")
    assert client.post(path, json={**credentials, **challenge, "role": "admin"}).status_code == 422
    response = client.post(path, json={**credentials, **challenge})
    assert response.status_code == 201
    user = response.json()
    assert user["username"] == "new-account" and user["role"] == "member"
    assert user["is_active"] is False and "password_hash" not in user
    assert client.post(path, json={**credentials, **challenge}).status_code == 400
    assert client.post(path, json={**credentials, **captcha_payload(client, "register")}).status_code == 409
    assert login_request(client, "new-account", "test-password-123").status_code == 401
    assert client.patch(f"/api/v1/users/{user['id']}", json={"is_active": True}).status_code == 200
    member = sign_in(client, "new-account")
    assert client.get("/api/v1/users", headers=member).status_code == 403


def test_expired_captcha_cannot_log_in(anonymous_client, account_factory):
    from app.database import SessionLocal
    from app.models import CaptchaChallenge

    account_factory()
    payload = captcha_payload(anonymous_client)
    with SessionLocal() as db:
        db.query(CaptchaChallenge).update({CaptchaChallenge.expires_at: datetime.utcnow() - timedelta(seconds=1)})
        db.commit()
    response = anonymous_client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "test-password-123", **payload,
    })
    assert response.status_code == 400


def test_cors_allows_only_configured_origins(anonymous_client):
    for origin, allowed in [("http://localhost:3000", True), ("https://evil.example", False)]:
        response = anonymous_client.options("/api/v1/auth/login", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        assert (response.headers.get("access-control-allow-origin") == origin) == allowed
