"""
$PULSE token balance + hold-to-access tier resolution (Solana).

The token is not minted yet, so this module runs in three modes:

  * gating disabled (TOKEN_GATING_ENABLED=false) OR no mint configured
        -> every wallet (and even a missing wallet) resolves to the top tier
           ("pro"). This lets us build and demo the full product before the
           mint exists.
  * gating enabled + PULSE_TOKEN_MINT set
        -> balance is read from Solana RPC (getTokenAccountsByOwner, filtered
           by the mint) and cached for BALANCE_CACHE_TTL seconds.

Hold-to-access: a wallet only needs to *hold* the threshold — there is no
transaction per request, just a cached balance read.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Tiers in ascending order of access.
TIERS = ["free", "holder", "pro"]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
PULSE_TOKEN_MINT = os.getenv("PULSE_TOKEN_MINT", "").strip()
TIER_HOLDER_MIN = _env_float("TIER_HOLDER_MIN", 1000.0)
TIER_PRO_MIN = _env_float("TIER_PRO_MIN", 25000.0)
BALANCE_CACHE_TTL = int(os.getenv("BALANCE_CACHE_TTL", "900"))
GATING_ENABLED = os.getenv("TOKEN_GATING_ENABLED", "false").lower() in {"1", "true", "yes"}

# In-process cache: address -> (balance, expires_at). Good enough for a single
# worker; swap for Redis if you scale horizontally.
_balance_cache: dict[str, tuple[float, float]] = {}


def gating_active() -> bool:
    """True only when gating is switched on AND a mint is configured."""
    return GATING_ENABLED and bool(PULSE_TOKEN_MINT)


def tier_rank(tier: str) -> int:
    try:
        return TIERS.index(tier)
    except ValueError:
        return 0


def meets(tier: str, min_tier: str) -> bool:
    """Does `tier` satisfy the `min_tier` requirement?"""
    return tier_rank(tier) >= tier_rank(min_tier)


def tier_for_balance(balance: float) -> str:
    if balance >= TIER_PRO_MIN:
        return "pro"
    if balance >= TIER_HOLDER_MIN:
        return "holder"
    return "free"


async def _rpc_token_balance(address: str) -> float:
    """Sum the $PULSE balance across all token accounts owned by `address`."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            address,
            {"mint": PULSE_TOKEN_MINT},
            {"encoding": "jsonParsed"},
        ],
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(SOLANA_RPC_URL, json=payload)
        r.raise_for_status()
        data = r.json()

    total = 0.0
    accounts = (((data or {}).get("result") or {}).get("value")) or []
    for acc in accounts:
        try:
            amt = (
                acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"]
            )
            if amt:
                total += float(amt)
        except (KeyError, TypeError, ValueError):
            continue
    return total


async def get_token_balance(address: Optional[str]) -> float:
    """Cached $PULSE balance for a wallet.

    Returns a large mock balance when gating is inactive so the whole product
    is usable before the token exists.
    """
    if not gating_active():
        return float("inf")
    if not address:
        return 0.0

    now = time.time()
    cached = _balance_cache.get(address)
    if cached and cached[1] > now:
        return cached[0]

    try:
        balance = await _rpc_token_balance(address)
    except Exception as exc:
        logger.warning("Solana balance lookup failed for %s: %s", address, exc)
        # Fail closed (free tier) but cache briefly so we don't hammer the RPC.
        balance = cached[0] if cached else 0.0

    _balance_cache[address] = (balance, now + BALANCE_CACHE_TTL)
    return balance


async def get_tier(address: Optional[str]) -> str:
    """Resolve the access tier for a wallet address (or None)."""
    if not gating_active():
        return "pro"
    balance = await get_token_balance(address)
    return tier_for_balance(balance)


def thresholds() -> dict:
    """Public tier thresholds, for the frontend to render."""
    return {
        "holder": TIER_HOLDER_MIN,
        "pro": TIER_PRO_MIN,
        "gating_active": gating_active(),
        "mint": PULSE_TOKEN_MINT or None,
    }
