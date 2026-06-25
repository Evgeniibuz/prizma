"""
Prediction staking routes — Phase 3 (coming soon).

On-chain prediction staking is not live yet: it needs an audited Solana
program, an oracle/resolution layer, and a legal review. This endpoint exposes
its status so the frontend can render the "release soon" surface and let users
register interest. No funds move here.
"""
from __future__ import annotations

import os

from fastapi import APIRouter

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


@router.get("/status")
async def predictions_status():
    return {
        "status": "coming_soon",
        "phase": 3,
        "headline": "Prediction Staking",
        "tagline": "Put conviction on the line. Stake on a call, earn when you're right.",
        "subtext": "Skin in the game, on-chain. Launching after audit + oracle go-live.",
        "release": os.getenv("STAKING_RELEASE_LABEL", "Release soon"),
        "chain": "solana",
        "notify_enabled": True,
    }
