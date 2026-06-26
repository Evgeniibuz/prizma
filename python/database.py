"""
Database connection — single source of truth for SQLAlchemy Base, engine,
async session factory and a dependency for FastAPI routes.

All models live in `models.py` and import `Base` from here. Do not re-declare
`Base` anywhere else, otherwise tables end up bound to different metadata
objects and `create_all` becomes a no-op.
"""
import os
import logging
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy import text

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://pulse_user:pulse_dev_password@localhost:5432/pulse_db",
)

# Coerce to asyncpg driver (we accept both forms in the env var)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


# ── Schema bootstrap ─────────────────────────────────────────────────────────
def _find_init_sql() -> Path | None:
    """Locate database/init.sql under both Docker and Nixpacks/Railway layouts."""
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "database" / "init.sql",          # repo layout (Nixpacks)
        Path("/app/database/init.sql"),                 # docker copied
        Path(os.getenv("INIT_SQL_PATH", "")) if os.getenv("INIT_SQL_PATH") else None,
    ]
    for p in candidates:
        if p and p.exists():
            return p
    return None


async def _bootstrap_schema_if_empty() -> None:
    """Run init.sql once if the `users` table doesn't exist yet.

    Idempotent: if any of the schema is already there, we skip. This lets us
    deploy on Railway (managed Postgres, no docker-entrypoint-initdb.d) without
    a separate migration step. For real migrations later, swap this for Alembic.
    """
    if os.getenv("SKIP_DB_BOOTSTRAP", "").lower() in {"1", "true", "yes"}:
        logger.info("DB bootstrap skipped (SKIP_DB_BOOTSTRAP set)")
        return

    sql_path = _find_init_sql()
    if not sql_path:
        logger.warning("init.sql not found, skipping bootstrap")
        return

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables "
                "  WHERE table_schema = 'public' AND table_name = 'users'"
                ")"
            )
        )
        users_exists = bool(result.scalar())

    if users_exists:
        logger.info("Schema already present, skipping bootstrap")
        return

    logger.info("Empty database detected — running init.sql bootstrap from %s", sql_path)
    sql = sql_path.read_text(encoding="utf-8")

    statements = _split_sql_statements(sql)
    executed = 0
    async with engine.begin() as conn:
        for stmt in statements:
            stripped = stmt.strip()
            if not stripped:
                continue
            # Skip pure-comment chunks
            non_comment_lines = [
                line for line in stripped.splitlines()
                if not line.lstrip().startswith("--")
            ]
            if not any(line.strip() for line in non_comment_lines):
                continue
            await conn.execute(text(stripped))
            executed += 1
    logger.info("Schema bootstrap completed (%d statements executed)", executed)


def _split_sql_statements(sql: str) -> list[str]:
    """Split a PostgreSQL script into top-level statements.

    Respects dollar-quoted strings ($$ … $$ or $tag$ … $tag$) and single-quoted
    strings (incl. escaped '' doubling), so function bodies stay intact.
    Line comments (-- …) and block comments (/* … */) are skipped as well.
    """
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    in_line_comment = False
    in_block_comment = False
    dollar_tag: str | None = None  # current dollar-quote tag including $...$

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue

        if dollar_tag is not None:
            # Inside a dollar-quoted block — look for closing tag
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1
            continue

        if in_single:
            buf.append(ch)
            if ch == "'":
                # '' = escaped quote, stay inside the string
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        # Not inside any quoted/comment context
        if ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append(ch); buf.append(nxt)
            i += 2
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == "$":
            # Try to read a dollar-quote opener: $tag$ where tag may be empty
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                dollar_tag = sql[i : j + 1]
                buf.append(dollar_tag)
                i = j + 1
                continue
            # not a dollar-quote opener; treat as ordinary char
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            out.append("".join(buf))
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out

async def _ensure_runtime_tables() -> None:
    """Create tables added after first deploy, even when the DB is not empty."""
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wallets (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                chain VARCHAR(20) NOT NULL DEFAULT 'solana',
                address VARCHAR(64) NOT NULL UNIQUE,
                verified_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_wallets_user_id ON wallets(user_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_wallets_address ON wallets(address)"
        ))

async def init_db() -> None:
    """
    Verify the database is reachable on startup and bootstrap the schema if
    the database is empty (first deploy on a fresh Postgres).
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection OK")
    except Exception as exc:
        logger.error("Database connection failed: %s", exc)
        raise

    try:
        await _bootstrap_schema_if_empty()
        await _ensure_runtime_tables()
    except Exception as exc:
        logger.error("Schema bootstrap failed: %s", exc)
        raise


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an AsyncSession."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
