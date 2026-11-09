"""Account login, password changes and administrator-managed users."""

import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from ..auth import (
    DUMMY_HASH, get_current_user, hash_password, require_admin, token_digest, verify_password,
)
from ..captcha import consume_challenge, issue_challenge
from ..config import AUTH_SESSION_HOURS
from ..database import get_db
from ..models import AuthSession, LoginThrottle, User
from ..schemas import APIModel

router = APIRouter(prefix="/api/v1", tags=["accounts"])
Role = Literal["admin", "member", "viewer"]
CaptchaPurpose = Literal["login", "register"]


class Credentials(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value):
        return value.lower()


class UserCreate(Credentials):
    password: str = Field(min_length=12, max_length=128, repr=False)
    role: Role = "member"


class ChallengeCredentials(Credentials):
    captcha_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    captcha_code: str = Field(pattern=r"^[a-zA-Z0-9]{5}$", repr=False)


class Registration(ChallengeCredentials):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    password: str = Field(min_length=12, max_length=128, repr=False)


class UserRead(APIModel):
    id: str
    username: str
    role: Role
    is_active: bool
    created_at: datetime


class LoginRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead


class PasswordChange(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=12, max_length=128, repr=False)


class UserUpdate(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(None, min_length=12, max_length=128, repr=False)


def _reserve_attempt(db: Session, key: str, limit: int, message: str = "登录尝试过多，请稍后再试") -> None:
    """Atomic per-bucket counters shared by API processes, including unknown users."""
    now = datetime.utcnow()
    db.query(LoginThrottle).filter(LoginThrottle.expires_at <= now).delete()
    digest = token_digest(key)
    statement = update(LoginThrottle).where(LoginThrottle.key == digest).values(
        attempts=LoginThrottle.attempts + 1,
    )
    if db.execute(statement).rowcount == 0:
        db.add(LoginThrottle(key=digest, attempts=1, expires_at=now + timedelta(minutes=15)))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return _reserve_attempt(db, key, limit, message)
    bucket = db.get(LoginThrottle, digest)
    attempts, expires_at = bucket.attempts, bucket.expires_at
    db.commit()
    if attempts > limit:
        wait = max(1, int((expires_at - now).total_seconds()))
        raise HTTPException(429, message, headers={"Retry-After": str(wait)})


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/auth/captcha")
def captcha(request: Request, response: Response, purpose: CaptchaPurpose = "login",
            db: Session = Depends(get_db)):
    _reserve_attempt(db, f"captcha:{_client_address(request)}", 180, "验证码请求过多，请稍后再试")
    response.headers["Cache-Control"] = "no-store"
    return issue_challenge(db, purpose)


def _save_user(db: Session, user: User) -> User:
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "账号已存在")
    db.refresh(user)
    return user


@router.post("/auth/register", response_model=UserRead, status_code=201)
def register(payload: Registration, request: Request, db: Session = Depends(get_db)):
    _reserve_attempt(db, f"register:{_client_address(request)}", 10, "注册尝试过多，请稍后再试")
    consume_challenge(db, payload.captcha_id, payload.captcha_code, "register")
    return _save_user(db, User(username=payload.username, password_hash=hash_password(payload.password),
                               role="member", is_active=False))


@router.post("/auth/login", response_model=LoginRead)
def login(payload: ChallengeCredentials, request: Request, response: Response, db: Session = Depends(get_db)):
    _reserve_attempt(db, f"ip:{_client_address(request)}", 60)
    consume_challenge(db, payload.captcha_id, payload.captcha_code, "login")
    _reserve_attempt(db, f"account:{payload.username}", 10)
    user = db.query(User).filter(User.username == payload.username).with_for_update().first()
    valid = verify_password(payload.password, user.password_hash if user else DUMMY_HASH)
    if not valid or not user or not user.is_active:
        raise HTTPException(401, "账号或密码错误")
    db.query(LoginThrottle).filter(LoginThrottle.key == token_digest(f"account:{payload.username}")).delete()
    now = datetime.utcnow()
    db.query(AuthSession).filter(AuthSession.expires_at <= now).delete()
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=token_digest(token), user_id=user.id, security_stamp=user.security_stamp,
                       expires_at=now + timedelta(hours=AUTH_SESSION_HOURS)))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return LoginRead(access_token=token, expires_in=AUTH_SESSION_HOURS * 3600,
                     user=UserRead.model_validate(user))


@router.get("/auth/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/auth/logout", status_code=204)
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = request.headers["authorization"].split()[1]
    db.query(AuthSession).filter(AuthSession.token_hash == token_digest(token)).delete()
    db.commit()


@router.post("/auth/password", status_code=204)
def change_password(payload: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.refresh(user, with_for_update=True)
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "当前密码错误")
    user.password_hash = hash_password(payload.new_password)
    user.security_stamp = secrets.token_hex(16)
    db.query(AuthSession).filter(AuthSession.user_id == user.id).delete()
    db.commit()


@router.get("/users", response_model=list[UserRead])
def list_users(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
               admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.created_at, User.id).offset(offset).limit(limit).all()


@router.post("/users", response_model=UserRead, status_code=201)
def create_user(payload: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _save_user(db, User(username=payload.username, password_hash=hash_password(payload.password),
                               role=payload.role))


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(user_id: str, payload: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    # Serialize admin changes on PostgreSQL to prevent two admins removing each other.
    active_admins = db.query(User).filter(User.role == "admin", User.is_active.is_(True)).order_by(
        User.id
    ).populate_existing().with_for_update().all()
    if admin.id not in {item.id for item in active_admins}:
        raise HTTPException(403, "需要管理员权限")
    user = db.get(User, user_id, populate_existing=True, with_for_update=True)
    if not user:
        raise HTTPException(404, "用户不存在")
    if user.id == admin.id and (payload.role not in {None, "admin"} or payload.is_active is False):
        raise HTTPException(400, "不能禁用自己或降低自己的管理员权限")
    if user.role == "admin" and user.is_active and len(active_admins) <= 1 and (
        payload.role not in {None, "admin"} or payload.is_active is False
    ):
        raise HTTPException(400, "必须保留至少一个启用的管理员")
    changes = {"security_stamp": secrets.token_hex(16)}
    if payload.role is not None:
        changes["role"] = payload.role
    if payload.is_active is not None:
        changes["is_active"] = payload.is_active
    if payload.password is not None:
        changes["password_hash"] = hash_password(payload.password)
    actor = aliased(User)
    actor_active = select(actor.id).where(
        actor.id == admin.id, actor.role == "admin", actor.is_active.is_(True)
    ).exists()
    # SQLite has no row locks: check the actor again inside the atomic UPDATE.
    changed = db.execute(update(User).where(User.id == user.id, actor_active).values(
        **changes
    ).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(403, "需要管理员权限")
    # Any account change revokes all of its sessions, including role changes.
    db.query(AuthSession).filter(AuthSession.user_id == user.id).delete()
    db.commit()
    db.refresh(user)
    return user
