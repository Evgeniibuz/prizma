"""
Market data routes — CoinGecko proxy with a small in-memory cache.

We use a process-local cache to insulate the frontend from CoinGecko rate
limits (50 req/min on demo tier). Each endpoint has its own TTL.

httpx note: we don't pass `proxies={}` (deprecated and removed in httpx
0.28). If the env var HTTP_PROXY is set globally and we *don't* want to use
it (e.g. for CoinGecko), set `trust_env=False` on the client.
"""
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["market"])

COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
IDS = "bitcoin,ethereum,solana,dogecoin,ripple,cardano,the-open-network,avalanche-2"

_cache: dict = {"market": None, "ts": 0.0, "price": {}, "top": {}}


def _client(timeout: float = 10.0) -> httpx.AsyncClient:
    """Async HTTP client that ignores the env proxy (CoinGecko hates proxies)."""
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def _cg_params(extra: Optional[dict] = None) -> dict:
    params = dict(extra or {})
    if COINGECKO_API_KEY:
        params["x_cg_demo_api_key"] = COINGECKO_API_KEY
    return params


@router.get("/top/{limit}")
async def get_top_coins(limit: int = 100):
    """Top N coins by market cap (CoinGecko max 250). Cached 2 min."""
    limit = max(1, min(int(limit), 250))
    cache_key = str(limit)

    cached = _cache["top"].get(cache_key)
    if cached and (time.time() - cached[1]) < 120:
        return cached[0]

    try:
        async with _client(timeout=15.0) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params=_cg_params(
                    {
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": limit,
                        "page": 1,
                        "sparkline": "false",
                        "price_change_percentage": "1h,24h,7d",
                    }
                ),
            )
            if r.status_code == 200:
                data = r.json()
                _cache["top"][cache_key] = (data, time.time())
                return data
            if cached:
                return cached[0]
            raise HTTPException(status_code=502, detail="CoinGecko unavailable")
    except HTTPException:
        raise
    except Exception as exc:
        if cached:
            return cached[0]
        raise HTTPException(status_code=502, detail=f"Failed to fetch top coins: {exc}")


@router.get("/price")
async def get_simple_price(ids: str = "bitcoin", vs_currencies: str = "usd"):
    """Mirror of /simple/price. Cached 30s."""
    cache_key = f"{ids}_{vs_currencies}"
    cached = _cache["price"].get(cache_key)
    if cached and (time.time() - cached[1]) < 30:
        return cached[0]

    try:
        async with _client() as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params=_cg_params({"ids": ids, "vs_currencies": vs_currencies}),
            )
            if r.status_code == 200:
                data = r.json()
                _cache["price"][cache_key] = (data, time.time())
                return data
            raise HTTPException(status_code=502, detail="CoinGecko unavailable")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch price: {exc}")


@router.get("")
async def get_market_data():
    """Market data for the eight supported coins. Cached 30s."""
    if _cache["market"] and (time.time() - _cache["ts"]) < 300:
        return _cache["market"]

    try:
        async with _client() as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params=_cg_params(
                    {
                        "vs_currency": "usd",
                        "ids": IDS,
                        "order": "market_cap_desc",
                        "sparkline": "false",
                        "price_change_percentage": "1h,24h,7d",
                    }
                ),
            )
            if r.status_code == 200:
                data = r.json()
                _cache["market"] = data
                _cache["ts"] = time.time()
                return data
            if _cache["market"]:
                return _cache["market"]
            raise HTTPException(status_code=502, detail="CoinGecko unavailable")
    except HTTPException:
        raise
    except Exception as exc:
        if _cache["market"]:
            return _cache["market"]
        raise HTTPException(status_code=502, detail=f"Failed to fetch market: {exc}")


@router.get("/fng")
async def get_fear_greed_index():
    """Fear & Greed Index from alternative.me. Returns None on failure."""
    try:
        async with _client(timeout=5.0) as client:
            r = await client.get("https://api.alternative.me/fng/?limit=1")
            if r.status_code == 200:
                data = (r.json() or {}).get("data", [])
                return data[0] if data else None
            return None
    except Exception:
        return None


@router.get("/cashtags")
async def get_cashtag_metrics(symbols: str = "btc,eth,sol,doge,xrp,ada,ton,avax"):
    """Cashtag mention/sentiment/velocity for given symbols (proxied via SentimentService)."""
    from services.sentiment import SentimentService

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return {"status": "error", "message": "No symbols", "data": []}

    service = SentimentService()
    try:
        return await service.get_cashtag_metrics(symbol_list)
    except Exception as exc:
        logger.exception("cashtag metrics error")
        return {"status": "error", "message": str(exc), "data": []}
    finally:
        await service.close()


@router.get("/image-proxy/{path:path}")
async def image_proxy(path: str):
    """Proxy CoinGecko images to dodge CORS/connection issues."""
    try:
        async with _client() as client:
            r = await client.get(f"https://coin-images.coingecko.com/{path}")
            if r.status_code == 200:
                return Response(
                    content=r.content,
                    media_type=r.headers.get("content-type", "image/png"),
                )
            raise HTTPException(status_code=404, detail="Image not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Image error: {exc}")


@router.get("/{coin_id}")
async def get_coin_data(coin_id: str):
    """Detailed data for a single coin id (CoinGecko id, e.g. 'bitcoin')."""
    try:
        async with _client() as client:
            r = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                params=_cg_params(
                    {
                        "localization": "false",
                        "tickers": "false",
                        "community_data": "false",
                        "developer_data": "false",
                        "sparkline": "false",
                    }
                ),
            )
            if r.status_code == 200:
                return r.json()
            raise HTTPException(status_code=404, detail="Coin not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Coin not found: {exc}")
