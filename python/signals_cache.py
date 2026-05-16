"""
Signals cache — async, backed by Postgres.

Replaces the old synchronous SQLite implementation. The `signals` table is
provisioned by `database/init.sql`. All public functions are coroutines.
"""
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, delete, func, desc

from database import AsyncSessionLocal
from models import Signal

logger = logging.getLogger(__name__)


def generate_market_hash(market_data: str) -> str:
    """Stable short hash of the market context for deduplication."""
    return hashlib.md5(market_data.encode("utf-8")).hexdigest()[:16]


def _row_to_dict(s: Signal) -> dict:
    return {
        "action": s.action,
        "ticker": s.ticker,
        "confidence": s.confidence,
        "entry": s.entry,
        "target": s.target,
        "stop": s.stop,
        "timeframe": s.timeframe,
        "reason": s.reason,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "generation_id": str(s.generation_id) if s.generation_id else None,
    }


async def save_signals(
    signals: list[dict],
    market_hash: Optional[str] = None,
    generation_id: Optional[str] = None,
) -> int:
    """Persist a batch of signals. Returns the number saved."""
    if not signals:
        return 0

    gen_uuid = None
    if generation_id:
        try:
            gen_uuid = uuid.UUID(generation_id)
        except ValueError:
            gen_uuid = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        rows = [
            Signal(
                action=s.get("action"),
                ticker=s.get("ticker"),
                confidence=int(s.get("confidence", 0)),
                entry=float(s.get("entry", 0) or 0),
                target=float(s.get("target", 0) or 0),
                stop=float(s.get("stop", 0) or 0),
                timeframe=s.get("timeframe", ""),
                reason=s.get("reason", ""),
                market_hash=market_hash,
                generation_id=gen_uuid,
            )
            for s in signals
        ]
        session.add_all(rows)
        await session.commit()
        logger.info("Saved %d signals to cache", len(rows))
        return len(rows)


async def get_recent_signals(hours: int = 24, limit: int = 50) -> list[dict]:
    """Recent signals from the last `hours`, newest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Signal)
            .where(Signal.created_at >= cutoff)
            .order_by(desc(Signal.created_at))
            .limit(limit)
        )
        return [_row_to_dict(s) for s in result.scalars().all()]


async def get_latest_generation_signals(limit: int = 20) -> list[dict]:
    """All signals from the most recent generation batch, by confidence."""
    async with AsyncSessionLocal() as session:
        latest = await session.execute(
            select(Signal.generation_id)
            .where(Signal.generation_id.isnot(None))
            .order_by(desc(Signal.created_at))
            .limit(1)
        )
        gen_id = latest.scalar_one_or_none()
        if gen_id is None:
            return []

        result = await session.execute(
            select(Signal)
            .where(Signal.generation_id == gen_id)
            .order_by(desc(Signal.confidence))
            .limit(limit)
        )
        return [_row_to_dict(s) for s in result.scalars().all()]


async def get_signals_by_ticker(ticker: str, hours: int = 24) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Signal)
            .where(Signal.ticker == ticker.upper(), Signal.created_at >= cutoff)
            .order_by(desc(Signal.created_at))
        )
        return [_row_to_dict(s) for s in result.scalars().all()]


async def cleanup_old_signals(hours: int = 168) -> int:
    """Delete signals older than `hours` (default 7 days). Returns deleted count."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(Signal).where(Signal.created_at < cutoff)
        )
        await session.commit()
        deleted = result.rowcount or 0
        if deleted:
            logger.info("Cleaned up %d old signals (older than %dh)", deleted, hours)
        return deleted


async def get_db_stats() -> dict:
    """Summary stats for monitoring."""
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        recent = (
            await session.execute(
                select(func.count(Signal.id)).where(Signal.created_at >= cutoff_24h)
            )
        ).scalar_one()
        oldest = (await session.execute(select(func.min(Signal.created_at)))).scalar_one()
        top_rows = (
            await session.execute(
                select(Signal.ticker, func.count(Signal.id).label("c"))
                .where(Signal.created_at >= cutoff_24h)
                .group_by(Signal.ticker)
                .order_by(desc("c"))
                .limit(5)
            )
        ).all()
        generations = (
            await session.execute(
                select(func.count(func.distinct(Signal.generation_id))).where(
                    Signal.created_at >= cutoff_24h,
                    Signal.generation_id.isnot(None),
                )
            )
        ).scalar_one()

        return {
            "total_signals": int(total or 0),
            "recent_24h": int(recent or 0),
            "oldest_signal": oldest.isoformat() if oldest else None,
            "top_tickers": [{"ticker": t, "count": int(c)} for t, c in top_rows],
            "generations_24h": int(generations or 0),
        }
