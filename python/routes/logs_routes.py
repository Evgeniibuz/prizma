"""
Activity log routes.

Filters by `user_id` (not by email), so the log is correct regardless of
whether ActivityLog.email matches the user's record email.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models import User, ActivityLog
from auth import get_current_user

router = APIRouter(prefix="/api/logs", tags=["logs"])


class LogResponse(BaseModel):
    type: str
    username: str
    time: str
    extra_data: dict = {}


@router.get("", response_model=list[LogResponse])
async def get_user_logs(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return up to `limit` most recent activity log entries for the user."""
    limit = max(1, min(limit, 200))

    result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.user_id == current_user.id)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    )

    return [
        LogResponse(
            type=log.action_type,
            username=current_user.username or "",
            time=log.created_at.isoformat(),
            extra_data=log.extra_data or {},
        )
        for log in result.scalars().all()
    ]
