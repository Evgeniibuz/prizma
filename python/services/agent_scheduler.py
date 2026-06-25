"""
Autonomous agent scheduler — runs each active dedicated agent on its interval.

Reuses the existing background-task pattern from main.py. Picks up agents whose
`last_run_at` is older than their `interval_minutes` and executes one mission,
storing the result as an AgentRun. Safe to run on a single worker.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from database import AsyncSessionLocal
from models import AgentConfig, AgentRun

logger = logging.getLogger(__name__)

# Cap how many agents run per tick so one cycle can't stampede the LLM API.
MAX_PER_TICK = 10


async def run_due_agents() -> int:
    """Execute every active agent whose interval has elapsed. Returns count run."""
    # Imported lazily to avoid a circular import at module load time.
    from routes.agent_routes import deepseek_client, execute_strategy

    if deepseek_client is None:
        return 0

    now = datetime.now(timezone.utc)
    ran = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentConfig).where(AgentConfig.is_active.is_(True))
        )
        configs = result.scalars().all()

        for cfg in configs:
            if ran >= MAX_PER_TICK:
                break
            due_at = (
                cfg.last_run_at + timedelta(minutes=cfg.interval_minutes)
                if cfg.last_run_at
                else now
            )
            if cfg.last_run_at and due_at > now:
                continue

            try:
                report = await execute_strategy(cfg.strategy_prompt, cfg.watch_symbols or [])
            except Exception as exc:
                logger.warning("Autonomous agent run failed (user=%s): %s", cfg.user_id, exc)
                continue

            db.add(
                AgentRun(
                    user_id=cfg.user_id,
                    task=f"Autonomous mission · {', '.join(cfg.watch_symbols or []) or 'market'}",
                    result=report,
                    trigger="auto",
                )
            )
            cfg.last_run_at = now
            ran += 1

        if ran:
            await db.commit()

    if ran:
        logger.info("Autonomous scheduler ran %d agent mission(s)", ran)
    return ran
