"""
Authentication routes (register, login, me, logout).

All endpoints return the same `AuthResponse(token, username)` contract that
the frontend (index.html, dashboard.html) saves into localStorage.
"""
import re
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models import User, ActivityLog
from auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


# ── Request / response models ─────────────────────────────────────────────
class RegisterRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 50:
            raise ValueError("Username too long")
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("Username can only contain letters, numbers, _ and -")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 4:
            raise ValueError("Password must be at least 4 characters")
        if len(v) > 128:
            raise ValueError("Password too long")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def trim_username(cls, v: str) -> str:
        return v.strip()

    @field_validator("password")
    @classmethod
    def cap_password(cls, v: str) -> str:
        if len(v) > 128:
            raise ValueError("Password too long")
        return v


class AuthResponse(BaseModel):
    token: str
    username: str


class UserResponse(BaseModel):
    username: str
    display_name: Optional[str] = None
    auth_method: str = "username"


# ── Helpers ────────────────────────────────────────────────────────────────
def _client_meta(request: Request) -> tuple[str, str]:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    ip = xff or (request.client.host if request.client else "") or "unknown"
    ua = request.headers.get("user-agent", "")[:500]
    return ip, ua


async def _log_activity(
    db: AsyncSession,
    user: User,
    action: str,
    request: Request,
    extra: Optional[dict] = None,
) -> None:
    ip, ua = _client_meta(request)
    db.add(
        ActivityLog(
            user_id=user.id,
            email=user.email,
            action_type=action,
            ip_address=ip,
            user_agent=ua,
            extra_data=extra or {},
        )
    )
    await db.commit()


# ── Endpoints ──────────────────────────────────────────────────────────────
@router.post("/register", response_model=AuthResponse)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user with username + password."""
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already taken"
        )

    user = User(
        username=body.username,
        email=f"{body.username}@local",  # synthetic — kept for legacy NOT NULL
        password_hash=get_password_hash(body.password),
        auth_method="username",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await _log_activity(db, user, "register", request)

    return AuthResponse(
        token=create_access_token({"username": user.username}),
        username=user.username,
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Login with username + password."""
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    # Unified error message so we don't leak whether a username exists
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
    )
    if not user or not user.password_hash:
        raise invalid
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        )
    if not verify_password(body.password, user.password_hash):
        raise invalid

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    await _log_activity(db, user, "login", request)

    return AuthResponse(
        token=create_access_token({"username": user.username}),
        username=user.username,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return UserResponse(
        username=current_user.username,
        display_name=current_user.display_name,
        auth_method=current_user.auth_method,
    )


@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stateless JWT logout — frontend drops the token. We just log the event."""
    await _log_activity(db, current_user, "logout", request)
    return {"ok": True}
