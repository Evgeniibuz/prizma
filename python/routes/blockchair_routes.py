"""
Blockchair routes — pulls top transactions per chain.

Each handler returns a JSON-serializable dict even on failure; the frontend
should never have to handle a thrown response from json().
"""
import logging
import os

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/chain-whales", tags=["chain-whales"])
logger = logging.getLogger(__name__)

BASE = os.getenv("BLOCKCHAIR_BASE_URL", "https://api.blockchair.com")
SUPPORTED_CHAINS = {"bitcoin", "ethereum", "solana", "bnb", "litecoin", "dogecoin"}


@router.get("/{chain}")
async def get_whale_txs(chain: str = "bitcoin", limit: int = 10):
    chain = chain.lower().strip()
    limit = max(1, min(int(limit), 100))
    if chain not in SUPPORTED_CHAINS:
        return {"error": "unsupported_chain", "chain": chain, "supported": sorted(SUPPORTED_CHAINS)}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{BASE}/{chain}/transactions",
                params={"limit": limit, "s": "value(desc)"},
            )
            if r.status_code != 200:
                logger.warning("Blockchair %s: %s", r.status_code, r.text[:200])
                return {"error": "upstream", "status": r.status_code, "chain": chain}
            return r.json()
    except Exception as exc:
        logger.warning("Blockchair fetch error for %s: %s", chain, exc)
        return {"error": "fetch_failed", "chain": chain, "message": str(exc)}


@router.get("/multi/latest")
async def get_all_chains():
    chains = ["bitcoin", "ethereum", "solana", "bnb"]
    results: dict = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for chain in chains:
                try:
                    r = await client.get(
                        f"{BASE}/{chain}/transactions",
                        params={"limit": 5, "s": "value(desc)"},
                    )
                    results[chain] = r.json() if r.status_code == 200 else {"error": "upstream"}
                except Exception as exc:
                    results[chain] = {"error": "timeout", "message": str(exc)}
    except Exception as exc:
        return {"error": str(exc), "results": results}
    return results
