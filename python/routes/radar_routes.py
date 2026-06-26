"""
Radar routes — synthesizes a single score per coin from market and social
inputs and exposes a per-symbol "breakdown" endpoint with optional AI
summary.

Scoring is deterministic for a given input snapshot, so the radar feed
doesn't jump every refresh. Tags currently come from price momentum,
volume relative to market cap, and social mention velocity.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import inspect as sa_inspect

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None  # type: ignore

try:
    import redis.asyncio as redis  # type: ignore
except Exception:
    redis = None  # type: ignore

from auth import get_current_user, get_current_tier
from models import User
from services import token_balance

router = APIRouter(prefix="/api/radar", tags=["radar"])
logger = logging.getLogger(__name__)

COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

STABLE_SYMBOLS = {
    "USDT", "USDC", "DAI", "TUSD", "BUSD", "USDP", "FDUSD",
    "USDE", "FRAX", "PYUSD", "GUSD", "LUSD",
}
STABLE_IDS = {
    "tether", "usd-coin", "dai", "true-usd", "binance-usd", "paxos-standard",
    "first-digital-usd", "ethena-usde", "frax", "paypal-usd", "gemini-dollar",
    "liquity-usd", "usd1-wlfi",
}

# ── Caches & rate limiting ─────────────────────────────────────────────────
_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_breakdown_cache: dict[str, Any] = {"ts": {}, "data": {}}
_rl_mem: dict[str, Any] = {"buckets": {}}

REDIS_URL = os.getenv("REDIS_URL", "")
_redis_client = None

# ── DeepSeek ───────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

_deepseek_client: Optional["AsyncOpenAI"] = None
if DEEPSEEK_API_KEY and AsyncOpenAI:
    _deepseek_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


# ── Helpers ────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_key(request: Request, user: User) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    ip = xff or (request.client.host if request.client else "") or "unknown"

    user_id = ""
    try:
        state = sa_inspect(user)
        if state.identity:
            user_id = str(state.identity[0])
        else:
            user_id = str(user.__dict__.get("id", ""))
    except Exception:
        user_id = str(getattr(user, "__dict__", {}).get("id", ""))

    return f"u:{user_id}:{ip}"


async def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL or not redis:
        return None
    try:
        _redis_client = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        return _redis_client
    except Exception as exc:
        logger.debug("Redis init failed: %s", exc)
        return None


async def _rate_limit(
    request: Request, user: User, scope: str, limit: int, window_sec: int
) -> None:
    """Simple fixed-window rate limit, per user+IP, with Redis or in-memory fallback."""
    key = f"rl:{scope}:{_client_key(request, user)}:{int(time.time()) // window_sec}"

    r = await _get_redis()
    if r is not None:
        try:
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, window_sec)
            if n > limit:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            return
        except HTTPException:
            raise
        except Exception:
            pass

    buckets: dict[str, Any] = _rl_mem["buckets"]
    now = int(time.time())
    bucket = buckets.get(key) or {"n": 0, "exp": now + window_sec}
    if bucket["exp"] <= now:
        bucket = {"n": 0, "exp": now + window_sec}
    bucket["n"] += 1
    buckets[key] = bucket
    if bucket["n"] > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def _safe_symbol(s: Optional[str]) -> str:
    return (s or "").strip().upper()


def _is_stable(coin: dict[str, Any]) -> bool:
    sym = _safe_symbol(coin.get("symbol"))
    cid = (coin.get("id") or "").strip().lower()
    name = (coin.get("name") or "").strip().lower()
    if sym in STABLE_SYMBOLS or cid in STABLE_IDS:
        return True
    if sym.startswith("USD"):
        return True
    if " usd" in f" {name} ":
        price = coin.get("current_price")
        if isinstance(price, (int, float)) and 0.97 <= float(price) <= 1.03:
            return True
    return False


def _clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


def _score_coin(
    coin: dict[str, Any], social_velocity: Optional[float] = None
) -> tuple[int, list[str]]:
    """Deterministic score based on momentum, volume, acceleration, social."""
    ch24 = float(coin.get("price_change_percentage_24h_in_currency") or 0.0)
    ch1h = float(coin.get("price_change_percentage_1h_in_currency") or 0.0)
    mcap = float(coin.get("market_cap") or 0.0)
    vol = float(coin.get("total_volume") or 0.0)

    vol_ratio = (vol / mcap) if mcap > 0 else 0.0
    momentum = _clamp(abs(ch24) / 12.0, 0.0, 1.0)
    accel = _clamp(abs(ch1h) / 3.0, 0.0, 1.0)
    volume = _clamp(vol_ratio / 0.25, 0.0, 1.0)
    social = _clamp((social_velocity or 0.0) / 500.0, 0.0, 1.0)

    raw = 50.0 + 30.0 * momentum + 15.0 * volume + 10.0 * accel + 15.0 * social
    score = int(_clamp(raw, 50.0, 100.0))

    tags: list[str] = []
    if volume >= 0.65:
        tags.append("volume")
    if social >= 0.5:
        tags.append("social")
    if accel >= 0.6:
        tags.append("kol")
    if momentum >= 0.8:
        tags.append("momentum")
    if not tags:
        tags.append("volume" if volume > 0.25 else "social")
    return score, list(dict.fromkeys(tags))


async def _fetch_top_coins(limit: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "vs_currency": "usd",
        "order": "volume_desc",
        "per_page": min(limit, 250),
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h,7d",
    }
    if COINGECKO_API_KEY:
        params["x_cg_demo_api_key"] = COINGECKO_API_KEY

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            "https://api.coingecko.com/api/v3/coins/markets", params=params
        )
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail="CoinGecko unavailable")
        return r.json() or []


# ── Endpoints ──────────────────────────────────────────────────────────────
# Free tier sees a capped radar feed; holders+ get the full depth.
FREE_TIER_SIGNAL_LIMIT = 25


@router.get("/signals")
async def radar_signals(
    request: Request,
    limit: int = 80,
    current_user: User = Depends(get_current_user),
    tier: str = Depends(get_current_tier),
):
    if limit < 1 or limit > 120:
        raise HTTPException(status_code=400, detail="limit must be 1..120")
    await _rate_limit(request, current_user, scope="radar_signals", limit=30, window_sec=60)

    # Hold-to-access: free tier is capped to a teaser slice of the feed.
    capped = tier == "free" and limit > FREE_TIER_SIGNAL_LIMIT
    if capped:
        limit = FREE_TIER_SIGNAL_LIMIT

    ts = time.time()
    if _cache["data"] is not None and (ts - float(_cache["ts"])) < 20:
        cached = _cache["data"]
        sliced = cached["signals"][:limit]
        return {"ts": cached["ts"], "signals": sliced, "tier": tier, "capped": capped}

    coins = await _fetch_top_coins(limit=limit)
    coins = [c for c in coins if not _is_stable(c)]
    symbol_list = [s for s in (_safe_symbol(c.get("symbol")) for c in coins) if s]

    # Social velocity (numeric — see sentiment.py)
    social_velocity_map: dict[str, float] = {}
    sentiment_service = getattr(request.app.state, "sentiment_service", None)
    if sentiment_service:
        try:
            result = await sentiment_service.get_cashtag_metrics(symbol_list[:25])
            if result and result.get("status") == "ok":
                for item in result.get("data", []):
                    sym = _safe_symbol(item.get("symbol"))
                    v = item.get("velocity_pct")
                    if sym and isinstance(v, (int, float)):
                        social_velocity_map[sym] = float(v)
        except Exception as exc:
            logger.debug("Sentiment fetch failed: %s", exc)

    out: list[dict[str, Any]] = []
    for c in coins[:limit]:
        sym = _safe_symbol(c.get("symbol"))
        score, tags = _score_coin(c, social_velocity_map.get(sym))

        heat = "hot" if score >= 85 else "warm" if score >= 70 else "cold"
        out.append(
            {
                "coin_id": c.get("id"),
                "symbol": sym,
                "name": c.get("name"),
                "type": tags[0],
                "types": tags,
                "score": score,
                "heat": heat,
                "price": c.get("current_price"),
                "change": c.get("price_change_percentage_24h_in_currency"),
                "mcap": c.get("market_cap"),
                "volume": c.get("total_volume"),
                "time": "live",
                "meta": {
                    "social_velocity": social_velocity_map.get(sym, 0.0),
                },
            }
        )

    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    payload = {"ts": _now_iso(), "signals": out}
    _cache["ts"] = ts
    _cache["data"] = payload
    return {"ts": payload["ts"], "signals": out[:limit], "tier": tier, "capped": capped}


@router.get("/breakdown/{symbol}")
async def radar_breakdown(
    request: Request,
    symbol: str,
    include_ai: int = 0,
    current_user: User = Depends(get_current_user),
    tier: str = Depends(get_current_tier),
):
    sym = _safe_symbol(symbol)
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    if len(sym) > 10 or not sym.replace("$", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid symbol")

    # Hold-to-access: the AI summary is a holder+ perk. Free users still get
    # the raw breakdown, just with the AI summary locked (soft gate).
    ai_locked = bool(include_ai) and not token_balance.meets(tier, "holder")
    if ai_locked:
        include_ai = 0

    await _rate_limit(request, current_user, scope="radar_breakdown", limit=20, window_sec=60)
    if include_ai:
        await _rate_limit(
            request, current_user, scope="radar_breakdown_ai", limit=5, window_sec=300
        )

    ck = f"{sym}:{1 if include_ai else 0}"
    now = time.time()
    prev_ts = _breakdown_cache["ts"].get(ck)
    if prev_ts and (now - float(prev_ts)) < 60 and ck in _breakdown_cache["data"]:
        return _breakdown_cache["data"][ck]

    sentiment_service = getattr(request.app.state, "sentiment_service", None)

    sentiment = None
    if sentiment_service:
        try:
            sentiment = await sentiment_service.get_sentiment_data(sym, hours=24)
        except Exception:
            sentiment = None

    cashtags = None
    if sentiment_service:
        try:
            cashtags = await sentiment_service.get_cashtag_metrics([sym])
        except Exception:
            cashtags = None

    breakdown = {
        "symbol": sym,
        "ts": _now_iso(),
        "sentiment": sentiment,
        "cashtags": cashtags,
    }

    summary = None
    if include_ai and _deepseek_client:
        try:
            prompt = (
                f"You are PULSΞ Radar. Provide a concise breakdown for ${sym}. "
                "Return 4-6 bullet points with numbers if available. "
                "Focus on: price momentum, volume, mention velocity, sentiment. "
                "No hype, just facts and interpretation."
            )
            response = await _deepseek_client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": str(breakdown)[:12000]},
                ],
                temperature=0.2,
            )
            summary = response.choices[0].message.content
        except Exception as exc:
            logger.debug("Radar AI summary failed: %s", exc)
            summary = None

    payload = {"breakdown": breakdown, "summary": summary, "tier": tier, "ai_locked": ai_locked}
    _breakdown_cache["ts"][ck] = now
    _breakdown_cache["data"][ck] = payload
    return payload
