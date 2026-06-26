"""
Routes package — central registration of all FastAPI routers.

Add a new router?
  1. Create routes/your_module.py with `router = APIRouter(...)`.
  2. Import + re-export it here.
  3. main.py picks it up via `app.include_router(...)`.
"""
from .market_routes import router as market_router
from .agent_routes import router as agent_router
from .auth_routes import router as auth_router
from .radar_routes import router as radar_router
from .logs_routes import router as logs_router
from .dexscreener_routes import router as dex_router
from .blockchair_routes import router as chain_whale_router
from .wallet_routes import router as wallet_router
from .predictions_routes import router as predictions_router

__all__ = [
    "market_router",
    "agent_router",
    "auth_router",
    "radar_router",
    "logs_router",
    "dex_router",
    "chain_whale_router",
    "wallet_router",
    "predictions_router",
]
