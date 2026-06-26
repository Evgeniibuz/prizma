"""
Authentication and authorization utilities.

JWT signing key MUST be set via the JWT_SECRET env var in production. The
fallback is a clearly-marked dev value and the app refuses to start without
an override when PULSE_ENV=production.
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models import User, Wallet
from services import token_balance

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────
_DEV_SECRET = "pulse_jwt_dev_secret_change_in_production_min_32_chars_long"
SECRET_KEY = os.getenv("JWT_SECRET", _DEV_SECRET)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "7"))

if os.getenv("PULSE_ENV", "").lower() == "production" and SECRET_KEY == _DEV_SECRET:
    raise RuntimeError(
        "JWT_SECRET must be set to a strong random value in production. "
        "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
    )

if SECRET_KEY == _DEV_SECRET:
    logger.warning("Using DEV JWT_SECRET. Set JWT_SECRET env var for production.")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# auto_error=False so endpoints can decide whether auth is required
security = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a bcrypt hash."""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password with bcrypt."""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT token. Raises 401 on failure."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the current user from the bearer token; 401 if missing/invalid."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    username = payload.get("username")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )
    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Return the current user if a valid token is present, else None."""
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


# ── Token-gating (hold-to-access) ──────────────────────────────────────────
async def user_wallet_address(db: AsyncSession, user: User) -> Optional[str]:
    """Most recently linked, verified wallet address for a user (or None).

    Degrades gracefully (returns None) if the wallets table doesn't exist yet,
    so tier resolution never 500s a request.
    """
    try:
        result = await db.execute(
            select(Wallet)
            .where(Wallet.user_id == user.id, Wallet.verified_at.isnot(None))
            .order_by(Wallet.created_at.desc())
        )
        wallet = result.scalars().first()
        return wallet.address if wallet else None
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.warning("wallet lookup failed (table missing?) — defaulting to no wallet")
        return None


async def get_current_tier(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Resolve the caller's hold-to-access tier ('free' | 'holder' | 'pro')."""
    address = await user_wallet_address(db, current_user)
    return await token_balance.get_tier(address)


def require_tier(min_tier: str):
    """Dependency factory: 403 unless the caller holds at least `min_tier`."""

    async def _dep(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        address = await user_wallet_address(db, current_user)
        tier = await token_balance.get_tier(address)
        if not token_balance.meets(tier, min_tier):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This feature requires the '{min_tier}' tier. "
                    f"Link a wallet holding more $PLSX to unlock."
                ),
            )
        return current_user

    return _dep
