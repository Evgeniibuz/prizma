"""
AI Agent routes (DeepSeek via OpenAI-compatible API).

Uses AsyncOpenAI so DeepSeek calls don't block the event loop while waiting
for the LLM. Signals are persisted asynchronously in Postgres via
`signals_cache`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from openai import AsyncOpenAI
except ImportError:  # openai not installed
    AsyncOpenAI = None  # type: ignore

import signals_cache
from auth import get_current_user, require_tier
from database import get_db
from models import AgentConfig, AgentRun, User

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger(__name__)

# ── DeepSeek client ────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

deepseek_client: Optional["AsyncOpenAI"] = None
if DEEPSEEK_API_KEY and AsyncOpenAI:
    deepseek_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


# ── Prompts ────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are PULSΞ — an elite AI crypto market intelligence agent. You synthesize market data, social sentiment, and $CASHTAG activity into actionable intelligence. Sharp, precise, no fluff. Always respond in English only.

ANALYSIS FRAMEWORK:
1. VERDICT: BULLISH / BEARISH / NEUTRAL / CAUTION
2. CONFIDENCE: 0-100%
3. CONTEXT: 2-3 sentences on what's happening now
4. KEY SIGNALS (◆ bullets): price momentum, volume, social/$cashtag velocity, S/R levels
5. RISK: LOW / MEDIUM / HIGH / EXTREME
6. OUTLOOK: 24-72h forecast
7. ACTION: 1 sentence clear recommendation

Rules:
- Always use $CASHTAG format for tickers
- Be data-driven and precise with real numbers from the provided data
- No marketing fluff or generic advice
- Focus on actionable intelligence
- Include specific price levels, percentages, and metrics
- Respond in English only"""

MISSION_PROMPT = """You are PULSΞ's multi-agent crypto intelligence orchestrator. You coordinate 3 specialized AI agents that each analyze different dimensions of the market. Provide DEEP, COMPREHENSIVE analysis — not summaries.

For the requested asset/topic, run ALL 3 agents with detailed output:

━━━ AGENT 1: MARKET ANALYST ━━━
Analyze in detail:
- Current price action and trend (1H, 4H, 1D timeframes)
- Key support and resistance levels with exact prices
- RSI, MACD, Bollinger Bands status
- Volume analysis vs 20-day moving average
- Chart patterns forming (if any)
- ATH distance and significance

━━━ AGENT 2: $CASHTAG SCANNER ━━━
Analyze in detail:
- Mention velocity and acceleration (% change vs baseline)
- Sentiment ratio (bullish/bearish/neutral breakdown)
- KOL (Key Opinion Leader) activity — who is talking
- Organic vs bot activity assessment
- Cross-platform spread (Twitter → Telegram → Discord)
- Narrative forming around the asset

━━━ AGENT 3: ON-CHAIN & DEFI ANALYST ━━━
Analyze in detail:
- TVL changes on related protocols
- DEX volume trends
- Stablecoin flows into/out of the network
- Developer activity (if relevant)
- Upcoming token unlocks or protocol upgrades
- DeFi yield environment

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL SYNTHESIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After all agents report, provide:
- VERDICT: BULLISH / BEARISH / NEUTRAL / CAUTION
- CONFIDENCE: 0-100% (weighted by agent agreement)
- TIMEFRAME: Primary analysis window
- ENTRY STRATEGY: Specific price levels for entry
- TARGETS: 2-3 take-profit levels
- STOP LOSS: Where to cut
- RISK/REWARD: Calculated ratio
- KEY RISK: The #1 thing that could invalidate this thesis

Use REAL data from the market context provided. Be specific with numbers.
Every section should have 4-8 bullet points minimum.
Total response should be comprehensive — this is a full intelligence briefing, not a summary.
English only."""

SIGNAL_PROMPT = """You are an elite crypto trading signal generator for PULSΞ. Analyze market data and generate 5-7 ACTIONABLE trading signals with entry/exit strategies.

For each signal, provide:
- action: BUY / SELL / HOLD / CLOSE
- ticker: Symbol only (e.g., BTC, not $BTC)
- confidence: 0-100 (numerical confidence level)
- entry: Entry price point (use current price if immediate)
- target: Take-profit target price
- stop: Stop-loss price
- timeframe: Position timeframe (e.g., "1-3 days", "Immediate", "4-7 days")
- reason: 1-2 sentences with SPECIFIC data (price %, volume change, Fear&Greed, momentum)

Trade Logic:
- BUY: Strong uptrend, high volume, positive sentiment → open long
- SELL: Downtrend, distribution signs, negative sentiment → open short or exit longs
- CLOSE: Take profit or cut losses on existing position
- HOLD: Consolidation, wait for clearer signals

Use REAL numbers from provided data. Calculate entry/target/stop based on:
- Support/resistance levels (±2-5% from current)
- ATH distances
- Volume patterns
- Fear & Greed extremes

Return ONLY a JSON array (no surrounding prose), with 5-7 signal objects. Each
object must have the exact field names listed above. English only."""


# ── Models ─────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    context: str = ""


class ChatResponse(BaseModel):
    reply: Optional[str] = None
    error: Optional[str] = None


class MissionRequest(BaseModel):
    task: str
    market_data: str = ""
    context: str = ""


class MissionResponse(BaseModel):
    synthesis: Optional[str] = None
    error: Optional[str] = None


class Signal(BaseModel):
    action: str
    ticker: str
    confidence: int
    entry: float
    target: float
    stop: float
    timeframe: str
    reason: str


class SignalsRequest(BaseModel):
    market_data: str = ""


class SignalsResponse(BaseModel):
    signals: Optional[list[Signal]] = None
    error: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────
SYMBOL_TO_ID = {
    "btc": "bitcoin", "eth": "ethereum", "sol": "solana", "doge": "dogecoin",
    "xrp": "ripple", "ada": "cardano", "avax": "avalanche-2", "bnb": "binancecoin",
    "dot": "polkadot", "matic": "matic-network", "link": "chainlink",
    "uni": "uniswap", "aave": "aave", "cake": "pancakeswap-token",
    "arb": "arbitrum", "op": "optimism", "sui": "sui", "pepe": "pepe",
    "wif": "dogwifcoin", "bonk": "bonk", "render": "render-token",
    "fet": "fetch-ai", "inj": "injective-protocol", "ton": "the-open-network",
    "near": "near", "atom": "cosmos", "sei": "sei-network",
    "jup": "jupiter-exchange-solana", "pendle": "pendle",
    "gmx": "gmx", "ldo": "lido-dao", "mkr": "maker",
    "trump": "official-trump", "ai16z": "ai16z",
}


async def _fetch_fear_greed() -> str:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get("https://api.alternative.me/fng/")
            if r.status_code == 200:
                d = (r.json() or {}).get("data", [{}])[0]
                if d:
                    return f"\n\nFear & Greed Index: {d.get('value')}/100 ({d.get('value_classification')})"
    except Exception:
        pass
    return ""


async def _fetch_token_context(task: str) -> str:
    """Try to enrich the prompt with live CoinGecko data for the mentioned token."""
    try:
        m = re.search(
            r"\$([a-zA-Z]+)|analyze\s+([a-zA-Z]+)|analysis\s+(?:of\s+)?(?:\$)?([a-zA-Z]+)",
            task.lower(),
        )
        if not m:
            return ""
        sym = (m.group(1) or m.group(2) or m.group(3) or "").lower()
        if not sym:
            return ""
        cg_id = SYMBOL_TO_ID.get(sym, sym)
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "community_data": "false",
                    "developer_data": "false",
                },
            )
            if r.status_code != 200:
                return ""
            cg = r.json() or {}
            md = cg.get("market_data") or {}
            buf = ["\n\n━━━ LIVE TOKEN DATA (CoinGecko real-time — USE THESE PRICES) ━━━"]
            buf.append(f"Token: {cg.get('name', '')} ({(cg.get('symbol') or '').upper()})")
            buf.append(f"Current Price: ${(md.get('current_price') or {}).get('usd', 0):,.6f}")
            buf.append(f"24h Change: {md.get('price_change_percentage_24h', 0) or 0:+.2f}%")
            buf.append(f"7d Change: {md.get('price_change_percentage_7d', 0) or 0:+.2f}%")
            buf.append(f"30d Change: {md.get('price_change_percentage_30d', 0) or 0:+.2f}%")
            buf.append(f"Market Cap: ${(md.get('market_cap') or {}).get('usd', 0):,.0f}")
            buf.append(f"24h Volume: ${(md.get('total_volume') or {}).get('usd', 0):,.0f}")
            ath_usd = (md.get('ath') or {}).get('usd', 0) or 0
            ath_pct = (md.get('ath_change_percentage') or {}).get('usd', 0) or 0
            buf.append(f"ATH: ${ath_usd:,.2f} ({ath_pct:.1f}% from ATH)")
            buf.append(f"24h High: ${(md.get('high_24h') or {}).get('usd', 0):,.6f}")
            buf.append(f"24h Low: ${(md.get('low_24h') or {}).get('usd', 0):,.6f}")
            buf.append("⚠️ USE THESE PRICES. They are real-time. Do NOT use prices from training data.")
            return "\n".join(buf)
    except Exception as exc:
        logger.debug("Token context fetch error: %s", exc)
        return ""


# ── Endpoints ──────────────────────────────────────────────────────────────
@router.post("/chat", response_model=ChatResponse)
async def chat_with_agent(request: ChatRequest):
    if not request.message:
        raise HTTPException(status_code=400, detail="No message provided")
    if not deepseek_client:
        return ChatResponse(reply=None, error="AI agent not configured")

    full_message = request.message
    if request.context:
        full_message += f"\n\nMarket Data:\n{request.context}"

    try:
        response = await deepseek_client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": full_message},
            ],
            max_tokens=2048,
            temperature=0.7,
        )
        return ChatResponse(reply=response.choices[0].message.content)
    except Exception as exc:
        logger.exception("Chat error")
        return ChatResponse(reply=None, error=str(exc))


@router.post("/mission", response_model=MissionResponse)
async def run_mission(request: MissionRequest):
    if not request.task:
        raise HTTPException(status_code=400, detail="No task provided")
    if not deepseek_client:
        return MissionResponse(synthesis=None, error="AI agent not configured")

    full_task = request.task

    # Market data block (optional)
    if request.market_data:
        try:
            coins = json.loads(request.market_data) or []
            if coins:
                total_mcap = sum(c.get("market_cap", 0) for c in coins)
                total_volume = sum(c.get("total_volume", 0) for c in coins)
                avg_24h = sum(
                    c.get("price_change_percentage_24h_in_currency", 0) or 0 for c in coins
                ) / len(coins)

                buf = ["\n\n━━━ LIVE MARKET DATA ━━━"]
                buf.append(f"Total Market Cap: ${total_mcap / 1e9:.1f}B")
                buf.append(f"24h Volume: ${total_volume / 1e9:.1f}B")
                buf.append(f"Average 24h Change: {avg_24h:+.2f}%\n")
                buf.append("Asset Details:")
                for c in coins[:15]:
                    sym = (c.get("symbol") or "").upper()
                    buf.append(
                        f"${sym}: ${c.get('current_price', 0):,.4f} | "
                        f"1h: {c.get('price_change_percentage_1h_in_currency', 0) or 0:+.2f}% | "
                        f"24h: {c.get('price_change_percentage_24h_in_currency', 0) or 0:+.2f}% | "
                        f"7d: {c.get('price_change_percentage_7d_in_currency', 0) or 0:+.2f}% | "
                        f"Vol: ${(c.get('total_volume', 0) or 0) / 1e9:.2f}B"
                    )
                full_task += "\n".join(buf)
        except Exception:
            full_task += f"\n\nMarket Data:\n{request.market_data[:2000]}"

    if request.context:
        full_task += f"\n\nAdditional Context:\n{request.context}"

    full_task += await _fetch_fear_greed()
    full_task += await _fetch_token_context(request.task)

    try:
        response = await deepseek_client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": MISSION_PROMPT},
                {"role": "user", "content": full_task},
            ],
            max_tokens=4096,
            temperature=0.8,
        )
        return MissionResponse(synthesis=response.choices[0].message.content)
    except Exception as exc:
        logger.exception("Mission error")
        return MissionResponse(synthesis=None, error=str(exc))


@router.post("/signals", response_model=SignalsResponse)
async def generate_signals(request: SignalsRequest):
    if not deepseek_client:
        return SignalsResponse(signals=None, error="AI agent not configured")

    market_context = "Current crypto market overview:\n\n"

    if request.market_data:
        try:
            coins = json.loads(request.market_data) or []
            if coins:
                total_mcap = sum(c.get("market_cap", 0) for c in coins)
                total_volume = sum(c.get("total_volume", 0) for c in coins)
                avg_24h = sum(
                    c.get("price_change_percentage_24h_in_currency", 0) or 0 for c in coins
                ) / len(coins)

                top_gainers = sorted(
                    coins,
                    key=lambda x: x.get("price_change_percentage_24h_in_currency", 0) or 0,
                    reverse=True,
                )[:6]
                top_losers = sorted(
                    coins,
                    key=lambda x: x.get("price_change_percentage_24h_in_currency", 0) or 0,
                )[:6]

                market_context += f"Total Market Cap: ${total_mcap / 1e9:.1f}B\n"
                market_context += f"24h Volume: ${total_volume / 1e9:.1f}B\n"
                market_context += f"Average 24h Change: {avg_24h:+.2f}%\n\n"

                market_context += "Top Gainers (24h):\n"
                for c in top_gainers:
                    market_context += (
                        f"- {(c.get('symbol') or '').upper()}: ${c.get('current_price', 0):.4f} "
                        f"({c.get('price_change_percentage_24h_in_currency', 0) or 0:+.2f}%)\n"
                    )
                market_context += "\nTop Losers (24h):\n"
                for c in top_losers:
                    market_context += (
                        f"- {(c.get('symbol') or '').upper()}: ${c.get('current_price', 0):.4f} "
                        f"({c.get('price_change_percentage_24h_in_currency', 0) or 0:+.2f}%)\n"
                    )

                market_context += "\nTop Assets:\n"
                for c in coins[:25]:
                    market_context += (
                        f"{(c.get('symbol') or '').upper()}: ${c.get('current_price', 0):.4f} | "
                        f"1h: {c.get('price_change_percentage_1h_in_currency', 0) or 0:+.2f}% | "
                        f"24h: {c.get('price_change_percentage_24h_in_currency', 0) or 0:+.2f}% | "
                        f"7d: {c.get('price_change_percentage_7d_in_currency', 0) or 0:+.2f}% | "
                        f"Vol: ${(c.get('total_volume', 0) or 0) / 1e9:.2f}B\n"
                    )
        except Exception:
            market_context += request.market_data[:1000]

    market_context += await _fetch_fear_greed()
    prompt = (
        f"{market_context}\n\n"
        "Generate 5-7 trading signals with entry/exit strategies based on this data."
    )

    try:
        response = await deepseek_client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SIGNAL_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2000,
            temperature=0.7,
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:
        logger.exception("Signals error")
        return SignalsResponse(signals=None, error=str(exc))

    # Parse JSON array out of the response
    start = content.find("[")
    end = content.rfind("]") + 1
    if start < 0 or end <= start:
        logger.warning("AI response had no JSON array. Snippet: %s", content[:300])
        return SignalsResponse(signals=[], error=None)

    try:
        signals_raw = json.loads(content[start:end])
        signals = [Signal(**sig) for sig in signals_raw]
    except Exception as parse_err:
        logger.warning("Signal parse error: %s", parse_err)
        return SignalsResponse(signals=[], error=None)

    if signals:
        try:
            generation_id = str(uuid.uuid4())
            market_hash = signals_cache.generate_market_hash(market_context[:500])
            await signals_cache.save_signals(
                [sig.model_dump() for sig in signals],
                market_hash=market_hash,
                generation_id=generation_id,
            )
        except Exception as cache_err:
            logger.warning("Cache save error: %s", cache_err)

    return SignalsResponse(signals=signals)


@router.get("/signals/cached")
async def get_cached_signals(hours: int = 24, limit: int = 50):
    try:
        signals = await signals_cache.get_recent_signals(hours=hours, limit=limit)
        return {"signals": signals, "count": len(signals), "hours": hours}
    except Exception as exc:
        logger.exception("get_cached_signals error")
        return {"signals": [], "error": str(exc)}


@router.get("/signals/latest")
async def get_latest_signals(limit: int = 20):
    try:
        signals = await signals_cache.get_latest_generation_signals(limit=limit)
        return {"signals": signals, "count": len(signals)}
    except Exception as exc:
        logger.exception("get_latest_signals error")
        return {"signals": [], "error": str(exc)}


@router.get("/signals/ticker/{ticker}")
async def get_ticker_signals(ticker: str, hours: int = 24):
    try:
        signals = await signals_cache.get_signals_by_ticker(ticker, hours=hours)
        return {
            "ticker": ticker.upper(),
            "signals": signals,
            "count": len(signals),
            "hours": hours,
        }
    except Exception as exc:
        logger.exception("get_ticker_signals error")
        return {"signals": [], "error": str(exc)}


@router.delete("/signals/cleanup")
async def cleanup_signals(hours: int = 168):
    try:
        deleted = await signals_cache.cleanup_old_signals(hours=hours)
        return {
            "deleted": deleted,
            "hours": hours,
            "message": f"Deleted {deleted} signals older than {hours} hours",
        }
    except Exception as exc:
        logger.exception("cleanup_signals error")
        return {"deleted": 0, "error": str(exc)}


@router.get("/signals/stats")
async def get_signals_stats():
    try:
        return await signals_cache.get_db_stats()
    except Exception as exc:
        logger.exception("get_signals_stats error")
        return {"error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════
# Dedicated Agent (pro tier) — personal strategy + autonomous missions
# ════════════════════════════════════════════════════════════════════════════

DEDICATED_SYSTEM_PROMPT = """You are the user's PRIVATE PULSΞ intelligence agent, tuned to their personal trading strategy. Run an autonomous mission: scan the watched assets through the lens of the strategy below and report a current, data-driven read — not a training-data guess.

Output:
- READ: one-line current take per watched asset
- ALIGNMENT: how today's market fits / breaks the user's strategy
- ACTIONS: concrete, strategy-consistent next steps (with levels if relevant)
- RISK: the #1 thing that would invalidate the thesis right now

Be sharp and specific. Use the real numbers provided. English only."""


async def execute_strategy(strategy_prompt: str, symbols: list[str]) -> str:
    """Run one autonomous mission for a dedicated agent. Reusable by the scheduler."""
    if not deepseek_client:
        raise RuntimeError("AI agent not configured")

    syms = [s.upper() for s in (symbols or []) if s][:12]
    watch = ", ".join(f"${s}" for s in syms) if syms else "the overall market"
    task = (
        f"STRATEGY:\n{strategy_prompt.strip() or 'General momentum + sentiment read.'}\n\n"
        f"WATCHED ASSETS: {watch}\n\n"
        "Produce the autonomous mission report now."
    )
    task += await _fetch_fear_greed()
    # Enrich with live data for the first watched symbol (anchors prices to now).
    if syms:
        task += await _fetch_token_context(f"${syms[0]}")

    response = await deepseek_client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": DEDICATED_SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ],
        max_tokens=2048,
        temperature=0.6,
    )
    return response.choices[0].message.content or ""


class AgentConfigBody(BaseModel):
    strategy_prompt: str = ""
    watch_symbols: list[str] = []
    interval_minutes: int = 360
    is_active: bool = False

    @field_validator("strategy_prompt")
    @classmethod
    def _cap_prompt(cls, v: str) -> str:
        if len(v) > 4000:
            raise ValueError("Strategy prompt too long (max 4000 chars)")
        return v

    @field_validator("watch_symbols")
    @classmethod
    def _clean_symbols(cls, v: list[str]) -> list[str]:
        out = [s.strip().upper().lstrip("$") for s in v if s and s.strip()]
        return out[:12]

    @field_validator("interval_minutes")
    @classmethod
    def _clamp_interval(cls, v: int) -> int:
        return max(30, min(int(v), 1440))


def _config_dto(cfg: AgentConfig) -> dict:
    return {
        "strategy_prompt": cfg.strategy_prompt,
        "watch_symbols": cfg.watch_symbols or [],
        "interval_minutes": cfg.interval_minutes,
        "is_active": cfg.is_active,
        "last_run_at": cfg.last_run_at.isoformat() if cfg.last_run_at else None,
    }


@router.get("/config")
async def get_agent_config(
    current_user: User = Depends(require_tier("pro")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.user_id == current_user.id)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        return {"configured": False, "config": None}
    return {"configured": True, "config": _config_dto(cfg)}


@router.post("/config")
async def save_agent_config(
    body: AgentConfigBody,
    current_user: User = Depends(require_tier("pro")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.user_id == current_user.id)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = AgentConfig(user_id=current_user.id)
        db.add(cfg)
    cfg.strategy_prompt = body.strategy_prompt
    cfg.watch_symbols = body.watch_symbols
    cfg.interval_minutes = body.interval_minutes
    cfg.is_active = body.is_active
    await db.commit()
    await db.refresh(cfg)
    return {"configured": True, "config": _config_dto(cfg)}


@router.post("/run")
async def run_agent_now(
    current_user: User = Depends(require_tier("pro")),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger the dedicated agent (in addition to the schedule)."""
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.user_id == current_user.id)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=400, detail="Configure your agent first")
    if not deepseek_client:
        raise HTTPException(status_code=503, detail="AI agent not configured")

    try:
        report = await execute_strategy(cfg.strategy_prompt, cfg.watch_symbols or [])
    except Exception as exc:
        logger.exception("Manual agent run failed")
        raise HTTPException(status_code=500, detail=str(exc))

    run = AgentRun(
        user_id=current_user.id,
        task=f"Manual mission · {', '.join(cfg.watch_symbols or []) or 'market'}",
        result=report,
        trigger="manual",
    )
    db.add(run)
    cfg.last_run_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(run)
    return {"id": str(run.id), "result": report, "created_at": run.created_at.isoformat()}


@router.get("/runs")
async def list_agent_runs(
    limit: int = 20,
    current_user: User = Depends(require_tier("pro")),
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 100))
    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.user_id == current_user.id)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    runs = result.scalars().all()
    return {
        "runs": [
            {
                "id": str(r.id),
                "task": r.task,
                "result": r.result,
                "trigger": r.trigger,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ]
    }
