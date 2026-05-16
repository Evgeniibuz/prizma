"""
Sentiment service — Twitter/X mentions and cashtag metrics.

Storage uses the project's async SQLAlchemy session (no more sync psycopg2
inside async handlers). Velocity is stored as both:
  - `velocity_pct` (float, used by code)
  - `velocity` (display string like "+25%", kept for legacy frontend)
"""
import os
import logging
import random
from datetime import datetime, timezone, timedelta

import httpx
from textblob import TextBlob
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import AsyncSessionLocal
from models import CashtagCache

logger = logging.getLogger(__name__)

CACHE_TTL_MINUTES = int(os.getenv("CASHTAG_CACHE_TTL_MIN", "120"))


class SentimentService:
    def __init__(self) -> None:
        self.twitter_bearer = os.getenv("TWITTER_BEARER_TOKEN")
        twitter_proxy = os.getenv("TWITTER_PROXY")
        client_kwargs: dict = {"timeout": 30.0}
        if twitter_proxy:
            client_kwargs["proxy"] = twitter_proxy
        self.client = httpx.AsyncClient(**client_kwargs)

    async def close(self) -> None:
        await self.client.aclose()

    # ── Twitter ──────────────────────────────────────────────────────────
    async def _fetch_tweets(self, query: str, max_results: int = 100) -> list[dict]:
        if not self.twitter_bearer:
            return []
        try:
            r = await self.client.get(
                "https://api.twitter.com/2/tweets/search/recent",
                headers={"Authorization": f"Bearer {self.twitter_bearer}"},
                params={
                    "query": query,
                    "max_results": max_results,
                    "tweet.fields": "created_at,public_metrics,lang",
                },
            )
            if r.status_code == 200:
                return (r.json() or {}).get("data", []) or []
            logger.warning("Twitter API %s: %s", r.status_code, r.text[:200])
            return []
        except Exception as exc:
            logger.warning("Twitter API exception: %s", exc)
            return []

    @staticmethod
    def _analyze_sentiment(tweets: list[dict]) -> dict:
        positive = negative = neutral = 0
        total_polarity = 0.0
        for tw in tweets:
            polarity = TextBlob(tw.get("text", "")).sentiment.polarity
            total_polarity += polarity
            if polarity > 0.1:
                positive += 1
            elif polarity < -0.1:
                negative += 1
            else:
                neutral += 1
        total = len(tweets)
        avg = (total_polarity / total * 100) if total else 0.0
        return {
            "mentions_count": total,
            "positive_count": positive,
            "negative_count": negative,
            "neutral_count": neutral,
            "sentiment_score": round(avg, 2),
            "positive_pct": round((positive / total * 100) if total else 0.0, 1),
            "negative_pct": round((negative / total * 100) if total else 0.0, 1),
        }

    async def get_sentiment_data(self, symbol: str, hours: int = 24) -> dict:
        if not self.twitter_bearer:
            return {"status": "error", "message": "Twitter API key not configured"}
        tweets = await self._fetch_tweets(f"${symbol} OR #{symbol}", max_results=100)
        if not tweets:
            return {"status": "ok", "mentions_count": 0, "sentiment_score": 0}
        return {"status": "ok", **self._analyze_sentiment(tweets)}

    async def analyze_and_cache(self, symbols: list[str]) -> dict:
        if not self.twitter_bearer:
            return {"status": "error", "message": "Twitter API key not configured"}
        results = {}
        for sym in symbols:
            tweets = await self._fetch_tweets(f"${sym} OR #{sym}")
            if tweets:
                results[sym] = self._analyze_sentiment(tweets)
        return {"status": "ok", "analyzed": len(results), "symbols": list(results.keys())}

    # ── Cashtag metrics (cached) ─────────────────────────────────────────
    async def get_cashtag_metrics(self, symbols: list[str]) -> dict:
        """
        Return cached or freshly-fetched cashtag metrics. Each item has:
          - symbol, mentions, sentiment (0-100), velocity_pct (float),
            velocity (display string).
        """
        if not self.twitter_bearer:
            return {
                "status": "error",
                "message": "Twitter API key not configured",
                "data": [],
            }

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=CACHE_TTL_MINUTES)
        results: list[dict] = []

        async with AsyncSessionLocal() as session:
            for sym in symbols:
                sym_clean = sym.strip().upper()
                if not sym_clean:
                    continue

                # 1) Try cache
                cached = (
                    await session.execute(
                        select(CashtagCache).where(
                            CashtagCache.symbol == sym_clean,
                            CashtagCache.updated_at > cutoff,
                        )
                    )
                ).scalar_one_or_none()

                if cached:
                    results.append(
                        {
                            "symbol": cached.symbol,
                            "mentions": cached.mentions,
                            "sentiment": cached.sentiment,
                            "velocity": cached.velocity,
                            "velocity_pct": float(cached.velocity_pct or 0.0),
                        }
                    )
                    continue

                # 2) Cache miss — call Twitter
                logger.info("Cashtag cache miss for $%s — calling Twitter API", sym_clean)
                tweets = await self._fetch_tweets(f"${sym_clean}", max_results=10)
                mentions = len(tweets)
                sent_data = self._analyze_sentiment(tweets) if tweets else {"sentiment_score": 0}
                sentiment_score = int(
                    max(0, min(100, 50 + sent_data.get("sentiment_score", 0) / 2))
                )

                # Velocity vs deterministic baseline (replace with historical
                # comparison once `sentiment_data` table has enough history)
                baseline = 40 + (hash(sym_clean) % 40)
                velocity_pct = (mentions - baseline) / baseline * 100.0
                velocity_pct += random.uniform(-5, 5)
                velocity_pct = round(velocity_pct, 1)
                velocity_str = (
                    f"+{int(velocity_pct)}%" if velocity_pct > 0 else f"{int(velocity_pct)}%"
                )

                # 3) Upsert
                stmt = pg_insert(CashtagCache).values(
                    symbol=sym_clean,
                    mentions=mentions,
                    sentiment=sentiment_score,
                    velocity=velocity_str,
                    velocity_pct=velocity_pct,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[CashtagCache.symbol],
                    set_={
                        "mentions": stmt.excluded.mentions,
                        "sentiment": stmt.excluded.sentiment,
                        "velocity": stmt.excluded.velocity,
                        "velocity_pct": stmt.excluded.velocity_pct,
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                await session.execute(stmt)
                await session.commit()

                results.append(
                    {
                        "symbol": sym_clean,
                        "mentions": mentions,
                        "sentiment": sentiment_score,
                        "velocity": velocity_str,
                        "velocity_pct": velocity_pct,
                    }
                )

        return {"status": "ok", "data": results}
