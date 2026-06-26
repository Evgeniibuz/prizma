"""
═══════════════════════════════════════════════════════════════
PULSΞ Backend — FastAPI server
Auth · Market data · AI agent · Sentiment
═══════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database import init_db
from services.sentiment import SentimentService

import signals_cache

from routes import (
    market_router,
    agent_router,
    auth_router,
    radar_router,
    logs_router,
    dex_router,
    chain_whale_router,
    wallet_router,
    predictions_router,
)

# ── Logging setup ─────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

# ── Service singletons (populated in lifespan) ─────────────────────────────
sentiment_service: SentimentService | None = None
_cleanup_task: asyncio.Task | None = None
_agent_task: asyncio.Task | None = None

# How often the autonomous agent scheduler wakes up to check for due missions.
AGENT_SCHEDULER_INTERVAL = int(os.getenv("AGENT_SCHEDULER_INTERVAL_SEC", "300"))
AGENT_SCHEDULER_ENABLED = os.getenv("AGENT_SCHEDULER_ENABLED", "true").lower() in {
    "1", "true", "yes",
}


async def _periodic_cleanup() -> None:
    """Background task: delete signals older than 7 days every 6 hours."""
    while True:
        try:
            await asyncio.sleep(6 * 3600)
            deleted = await signals_cache.cleanup_old_signals(hours=168)
            if deleted:
                logger.info("Periodic cleanup removed %d old signals", deleted)
        except asyncio.CancelledError:
            logger.info("Cleanup task cancelled")
            break
        except Exception as exc:
            logger.warning("Cleanup task error: %s", exc)


async def _agent_scheduler_loop() -> None:
    """Background task: run due dedicated-agent missions on their intervals."""
    from services.agent_scheduler import run_due_agents

    while True:
        try:
            await asyncio.sleep(AGENT_SCHEDULER_INTERVAL)
            await run_due_agents()
        except asyncio.CancelledError:
            logger.info("Agent scheduler cancelled")
            break
        except Exception as exc:
            logger.warning("Agent scheduler error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown handlers."""
    global sentiment_service, _cleanup_task, _agent_task

    await init_db()

    sentiment_service = SentimentService()
    app.state.sentiment_service = sentiment_service

    _cleanup_task = asyncio.create_task(_periodic_cleanup())
    logger.info("Started periodic signals cleanup (every 6h)")

    if AGENT_SCHEDULER_ENABLED:
        _agent_task = asyncio.create_task(_agent_scheduler_loop())
        logger.info("Started autonomous agent scheduler (every %ds)", AGENT_SCHEDULER_INTERVAL)

    yield

    for task in (_cleanup_task, _agent_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    if sentiment_service:
        try:
            await sentiment_service.close()
        except Exception as exc:
            logger.warning("Sentiment close error: %s", exc)

    app.state.sentiment_service = None


app = FastAPI(
    title="PULSΞ Backend",
    description="Crypto market intelligence platform with AI agent",
    version="2.2.0",
    lifespan=lifespan,
    # Move interactive API docs off /docs so the product docs page can use it.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────
allowed_origins_env = os.getenv("PULSE_ALLOWED_ORIGINS", "").strip()
if allowed_origins_env:
    allowed_origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
    allow_credentials = True
else:
    allowed_origins = ["*"]
    allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static / frontend mounts ───────────────────────────────────────────────
# Auto-detect frontend/images directories so the same code runs under Docker
# (where they're copied to /frontend, /images) and Nixpacks/Railway or local
# venv (where they sit next to the python/ package at the repo root).
def _resolve_dir(env_var: str, docker_path: str, repo_subdir: str) -> Path:
    override = os.getenv(env_var)
    if override:
        return Path(override)
    repo_local = Path(__file__).resolve().parent.parent / repo_subdir
    if repo_local.exists():
        return repo_local
    return Path(docker_path)


FRONTEND_PATH = _resolve_dir("FRONTEND_PATH", "/frontend", "frontend")
IMAGES_PATH = _resolve_dir("IMAGES_PATH", "/images", "images")
logger.info("Frontend path: %s (exists=%s)", FRONTEND_PATH, FRONTEND_PATH.exists())
logger.info("Images path:   %s (exists=%s)", IMAGES_PATH, IMAGES_PATH.exists())

if FRONTEND_PATH.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_PATH)), name="static")
if IMAGES_PATH.exists():
    app.mount("/images", StaticFiles(directory=str(IMAGES_PATH)), name="images")

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(market_router)
app.include_router(agent_router)
app.include_router(auth_router)
app.include_router(radar_router)
app.include_router(logs_router)
app.include_router(dex_router)
app.include_router(chain_whale_router)
app.include_router(wallet_router)
app.include_router(predictions_router)


# ── Frontend file routes ───────────────────────────────────────────────────
def _serve_frontend(filename: str, media_type: str | None = None) -> FileResponse:
    path = FRONTEND_PATH / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    return FileResponse(str(path), media_type=media_type) if media_type else FileResponse(str(path))


@app.get("/style.css")
async def serve_css():
    return _serve_frontend("style.css", media_type="text/css")


@app.get("/utils.js")
async def serve_utils():
    return _serve_frontend("utils.js", media_type="application/javascript")


@app.get("/api.js")
async def serve_api():
    return _serve_frontend("api.js", media_type="application/javascript")


@app.get("/config.js")
async def serve_config():
    return _serve_frontend("config.js", media_type="application/javascript")


@app.get("/")
async def root():
    return _serve_frontend("index.html")


@app.get("/dashboard")
@app.get("/dashboard.html")
async def dashboard():
    return _serve_frontend("dashboard.html")


@app.get("/agents")
@app.get("/agents.html")
async def agents():
    return _serve_frontend("agents.html")


@app.get("/radar")
@app.get("/radar.html")
async def radar():
    return _serve_frontend("radar.html")


@app.get("/predictions")
@app.get("/predictions.html")
@app.get("/staking")
async def predictions():
    return _serve_frontend("predictions.html")


@app.get("/roadmap")
@app.get("/roadmap.html")
async def roadmap():
    return _serve_frontend("roadmap.html")


@app.get("/privacy")
@app.get("/privacy.html")
async def privacy():
    return _serve_frontend("privacy.html")


@app.get("/docs")
@app.get("/docs.html")
async def docs():
    return _serve_frontend("docs.html")


# ── Health ────────────────────────────────────────────────────────────────
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "services": {
            "sentiment": sentiment_service is not None,
        },
    }


# ── Convenience helpers ───────────────────────────────────────────────────
@app.get("/api/sentiment/{symbol}")
async def get_sentiment(symbol: str, hours: int = 24):
    if not sentiment_service:
        raise HTTPException(status_code=503, detail="Sentiment service not available")
    try:
        return await sentiment_service.get_sentiment_data(
            symbol=symbol.upper(), hours=hours
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/sentiment/analyze")
async def analyze_sentiment(symbols: list[str] | None = None):
    if not sentiment_service:
        raise HTTPException(status_code=503, detail="Sentiment service not available")
    try:
        return await sentiment_service.analyze_and_cache(
            symbols or ["BTC", "ETH", "SOL", "DOGE"]
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/analysis/{symbol}")
async def get_comprehensive_analysis(symbol: str):
    """Sentiment-only comprehensive analysis for a symbol."""
    try:
        sym = symbol.upper()
        sentiment = await get_sentiment(sym, hours=24)
        return {
            "symbol": sym,
            "sentiment": sentiment,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
