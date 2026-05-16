"""
DexScreener proxy routes.

Each handler returns JSON-serializable data, including on upstream errors,
so the frontend never gets a thrown response.
"""
import logging
import os

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/dex", tags=["dexscreener"])
logger = logging.getLogger(__name__)

BASE = os.getenv("DEXSCREENER_BASE_URL", "https://api.dexscreener.com")
TIMEOUT = 10.0


async def _get(path: str, params: dict | None = None) -> dict | list | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{BASE}{path}", params=params)
            if r.status_code != 200:
                logger.warning("Dexscreener %s %s: %s", path, r.status_code, r.text[:200])
                return {"error": "upstream", "status": r.status_code}
            return r.json()
    except Exception as exc:
        logger.warning("Dexscreener %s error: %s", path, exc)
        return {"error": "fetch_failed", "message": str(exc)}


@router.get("/new-tokens")
async def get_new_tokens():
    return await _get("/token-profiles/latest/v1")


@router.get("/boosted")
async def get_boosted():
    return await _get("/token-boosts/top/v1")


@router.get("/search/{query}")
async def search_token(query: str):
    return await _get("/latest/dex/search", {"q": query})


@router.get("/token/{chain}/{address}")
async def get_token(chain: str, address: str):
    return await _get(f"/tokens/v1/{chain}/{address}")


@router.get("/pairs/{chain}/{pair_address}")
async def get_pair(chain: str, pair_address: str):
    return await _get(f"/latest/dex/pairs/{chain}/{pair_address}")
