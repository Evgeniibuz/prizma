"""
Wallet routes — Sign-In With Solana (link a Phantom wallet to a user).

Flow:
  1. GET  /api/wallet/nonce?address=<pubkey>  -> returns a one-time message
  2. Frontend asks Phantom to signMessage(message)
  3. POST /api/wallet/verify {address, signature}  -> verifies the ed25519
     signature, attaches the wallet to the current user, resolves the tier.

Signature verification is done locally with PyNaCl (ed25519) — no full Solana
SDK needed. Nonces are kept in-process with a short TTL.
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone

import base58
from fastapi import APIRouter, Depends, HTTPException, status
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, user_wallet_address
from database import get_db
from models import User, Wallet
from services import token_balance

router = APIRouter(prefix="/api/wallet", tags=["wallet"])
logger = logging.getLogger(__name__)

NONCE_TTL_SECONDS = 300

# key: f"{user_id}:{address}" -> (message, expires_at)
_nonces: dict[str, tuple[str, float]] = {}


def _prune_nonces() -> None:
    now = time.time()
    for k in [k for k, v in _nonces.items() if v[1] < now]:
        _nonces.pop(k, None)


def _valid_b58_pubkey(address: str) -> bool:
    try:
        return len(base58.b58decode(address)) == 32
    except Exception:
        return False


class VerifyRequest(BaseModel):
    address: str
    signature: str  # base58-encoded 64-byte ed25519 signature

    @field_validator("address")
    @classmethod
    def _check_address(cls, v: str) -> str:
        v = v.strip()
        if not _valid_b58_pubkey(v):
            raise ValueError("Invalid Solana address")
        return v


async def _tier_payload(address: str | None) -> dict:
    balance = await token_balance.get_token_balance(address)
    tier = await token_balance.get_tier(address)
    return {
        "balance": None if balance == float("inf") else balance,
        "tier": tier,
        "gating_active": token_balance.gating_active(),
    }


@router.get("/nonce")
async def get_nonce(address: str, current_user: User = Depends(get_current_user)):
    """Issue a one-time message for the user to sign with their wallet."""
    address = address.strip()
    if not _valid_b58_pubkey(address):
        raise HTTPException(status_code=400, detail="Invalid Solana address")

    _prune_nonces()
    nonce = secrets.token_hex(16)
    issued = datetime.now(timezone.utc).isoformat()
    message = (
        "Sign in to PULSΞ\n\n"
        f"Wallet: {address}\n"
        f"Nonce: {nonce}\n"
        f"Issued: {issued}\n\n"
        "Signing is free and does not authorize any transaction."
    )
    _nonces[f"{current_user.id}:{address}"] = (message, time.time() + NONCE_TTL_SECONDS)
    return {"message": message}


@router.post("/verify")
async def verify_wallet(
    body: VerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify the signed nonce and link the wallet to the current user."""
    key = f"{current_user.id}:{body.address}"
    record = _nonces.get(key)
    if not record or record[1] < time.time():
        _nonces.pop(key, None)
        raise HTTPException(status_code=400, detail="Nonce expired — request a new one")
    message = record[0]

    # Verify the ed25519 signature against the message.
    try:
        pubkey_bytes = base58.b58decode(body.address)
        sig_bytes = base58.b58decode(body.signature)
        VerifyKey(pubkey_bytes).verify(message.encode("utf-8"), sig_bytes)
    except (BadSignatureError, ValueError):
        raise HTTPException(status_code=401, detail="Signature verification failed")
    except Exception as exc:
        logger.warning("Wallet verify error: %s", exc)
        raise HTTPException(status_code=400, detail="Could not verify signature")

    _nonces.pop(key, None)

    # Upsert the wallet. An address may only belong to one account.
    try:
        result = await db.execute(select(Wallet).where(Wallet.address == body.address))
        wallet = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)

        if wallet and wallet.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This wallet is already linked to another account",
            )

        if wallet is None:
            wallet = Wallet(
                user_id=current_user.id,
                chain="solana",
                address=body.address,
                verified_at=now,
            )
            db.add(wallet)
        else:
            wallet.verified_at = now

        await db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("Wallet upsert failed")
        raise HTTPException(
            status_code=503,
            detail="Could not save wallet — the wallets table may be missing. "
                   "Redeploy the backend so startup creates it.",
        )

    payload = await _tier_payload(body.address)
    return {"linked": True, "address": body.address, **payload}


@router.get("/status")
async def wallet_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Current wallet link + tier for the caller."""
    address = await user_wallet_address(db, current_user)
    payload = await _tier_payload(address)
    return {
        "linked": address is not None,
        "address": address,
        "thresholds": token_balance.thresholds(),
        **payload,
    }


@router.delete("/unlink")
async def unlink_wallet(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove all wallets linked to the caller."""
    result = await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))
    for wallet in result.scalars().all():
        await db.delete(wallet)
    await db.commit()
    return {"linked": False}
