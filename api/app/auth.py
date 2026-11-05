"""Password hashing and revocable, database-backed bearer sessions."""

import hashlib
import hmac
import secrets
from datetime import datetime

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuthSession, User

bearer = HTTPBearer(auto_error=False)
ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


# Unknown accounts take the same password-hash path as real accounts.
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    session = db.get(AuthSession, token_digest(credentials.credentials)) if credentials else None
    user = db.get(User, session.user_id) if session and session.expires_at > datetime.utcnow() else None
    if not user or not user.is_active or session.security_stamp != user.security_stamp:
        raise HTTPException(401, "请登录或重新登录", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_writer(user: User = Depends(get_current_user)) -> User:
    if user.role not in {"admin", "member"}:
        raise HTTPException(403, "只读账号不能修改任务")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user
