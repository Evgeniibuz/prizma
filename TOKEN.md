# $PLSX Token — Utility & Implementation Plan

> One token. Three ways to use it inside PULSΞ.
> Chain: **Solana** · Gating model: **hold-to-access** · Token: *not minted yet (config-driven)*

This document is the single source of truth for token integration. The token
does not exist yet, so everything is built behind a config flag and a mockable
balance provider — we can develop and demo end-to-end before the mint exists.

---

## The three utilities

| # | Utility | What it gates | On-chain? | Complexity |
|---|---------|---------------|-----------|------------|
| 1 | **Paid intelligence** | Premium Radar score breakdowns, AI summaries, full signal history, higher rate limits | Read balance only | 🟢 Low |
| 2 | **Dedicated Agent** | Personal strategy prompt + autonomous scheduled missions | Read balance only | 🟡 Medium |
| 3 | **Prediction staking** | Stake on a call, earn when right | Yes — escrow + oracle | 🔴 High |

---

## Tiers (hold-to-access)

Balance is read from Solana RPC, cached in Redis 10–15 min. No transaction per
request — the user just needs to *hold* the threshold.

| Tier | Threshold | Unlocks |
|------|-----------|---------|
| `free` | 0 | Radar score (no breakdown), top signals, agent chat (rate-limited) |
| `holder` | ≥ `TIER_HOLDER_MIN` | Full Radar breakdown + AI summary, signal history, higher limits |
| `pro` | ≥ `TIER_PRO_MIN` | Dedicated agent, autonomous missions, staking with reduced fees |

Thresholds live in env so they can be tuned post-launch without redeploy logic
changes.

---

## Config (env)

```
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com   # or Helius/QuickNode
PULSE_TOKEN_MINT=                                     # empty until minted
TIER_HOLDER_MIN=1000
TIER_PRO_MIN=25000
BALANCE_CACHE_TTL=900
TOKEN_GATING_ENABLED=false                            # master switch; false => everyone is 'pro' (dev)
STAKING_PROGRAM_ID=                                   # Anchor program, phase 3
```

When `TOKEN_GATING_ENABLED=false` or `PULSE_TOKEN_MINT` is empty, the balance
provider returns a mock balance and everyone resolves to `pro`. This is how we
build/demo before the mint.

---

## Phase 1 — Foundation: wallet auth + tiers  🟢

**Goal:** a logged-in user can link a Solana wallet, and the backend can resolve
their tier. Radar gets gated.

### Data model (`python/models.py`)

```python
class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chain = Column(String(20), default="solana")
    address = Column(String(64), unique=True, index=True)   # base58 pubkey
    verified_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

### Wallet auth (Sign-In With Solana)

1. `GET  /api/wallet/nonce?address=<pubkey>` → returns a random nonce (Redis, 5 min TTL).
2. Frontend asks Phantom to `signMessage(nonce)`.
3. `POST /api/wallet/verify {address, signature}` → verify ed25519 sig against
   nonce, attach `Wallet` to `current_user`, mark `verified_at`.

Use `solders` / `PyNaCl` for signature verification (no full web3 dependency).

### Balance provider (`python/services/token_balance.py`)

```python
async def get_token_balance(address: str) -> float:
    # if gating disabled or no mint -> return mock large balance
    # else: RPC getTokenAccountsByOwner(owner, mint) -> sum uiAmount
    # cache in Redis BALANCE_CACHE_TTL
```

### Tier dependency (`python/auth.py`)

```python
async def require_tier(min_tier: str):
    # resolve user -> wallet -> balance -> tier; 402/403 if insufficient
```

### Gate Radar (`python/routes/radar_routes.py`)

- `/signals`: free gets score only; `holder+` gets full payload + higher limit.
- `/breakdown` with `include_ai=1`: require `holder`.

**Deliverables:** `wallets` table + migration in `database/init.sql`, wallet
routes, balance service, `require_tier`, Radar gating, Phantom connect in
`frontend/api.js` + UI badge showing tier.

---

## Phase 2 — Dedicated Agent  🟡

**Goal:** `pro` users get a personal agent with a saved strategy that runs
autonomous missions on a schedule.

### Data model

```python
class AgentConfig(Base):       # one per user
    user_id, strategy_prompt, watch_symbols (JSON), schedule_cron, is_active

class AgentRun(Base):          # mission history
    user_id, task, result (Text), created_at
```

### Changes

- Add auth to `/api/agent/*` (currently open) → `require_tier("pro")` for
  personal agent endpoints; keep a limited public/`free` chat.
- `POST /api/agent/config` — save strategy + watchlist.
- Background worker: extend the existing `_periodic_cleanup` pattern in
  `main.py` with an `_agent_scheduler` task that, every N minutes, runs
  `run_mission` for active configs and stores `AgentRun`. Reuses the live
  CoinGecko enrichment already in `_fetch_token_context` — this is the
  "current read, not a training-data guess" selling point, already built.
- `GET /api/agent/runs` — mission feed for the user.

---

## Phase 3 — Prediction staking  🔴 (real money — treat with care)

**Goal:** user stakes $PLSX on a prediction; if resolved correct, earns from
the pool. This is the only part with on-chain money and the highest risk.

### Architecture

```
Frontend (Phantom)  →  Anchor program (Solana)  →  escrow PDA per market
                              ▲
        Backend indexer ──────┘   (listens to program events, mirrors to Postgres)
        Resolver/oracle ──────►   (settles markets from a defined price source)
```

### Smart contract (Anchor / Rust — separate workstream)

- `create_market(question, resolve_at, price_source)` — admin/curated first.
- `stake(market, side, amount)` — transfers $PLSX to escrow PDA.
- `resolve(market, outcome)` — only callable by oracle authority.
- `claim(market)` — winners withdraw pro-rata; protocol fee skimmed.

### Oracle / resolution (the hard, risky part)

- Start with **curated markets** resolved from a single agreed source
  (e.g. CoinGecko close at `resolve_at`), signed by a backend oracle keypair.
- Add a dispute window before payouts.
- Document the price source per market — this is the #1 manipulation surface.

### Backend (Postgres mirror, read model)

```python
class PredictionMarket(Base):
    onchain_id, question, symbol, resolve_at, status, outcome, total_pool
class Stake(Base):
    market_id, user_id, wallet, side, amount, claimed
```

- Indexer task subscribes to program logs → upserts markets/stakes.
- `GET /api/predictions`, `GET /api/predictions/{id}`, `GET /api/predictions/mine`.

### Risk gates before going live
- [ ] Smart-contract **audit** before mainnet money.
- [ ] Legal review — staking on outcomes may be regulated (gambling/derivatives)
      depending on jurisdiction.
- [ ] **Recommended interim:** ship "paper staking" first — stake reputation
      points stored in Postgres (no real funds) to validate the resolution
      mechanics, then swap in the on-chain escrow.

---

## Build order (recommended)

1. **Phase 1** — wallet auth + tiers + Radar gating. Unblocks everything, no money at risk.
2. **Phase 2** — dedicated agent + scheduler. Reuses existing patterns.
3. **Phase 3a** — paper staking (Postgres) to validate resolution UX.
4. **Phase 3b** — Anchor program + oracle + audit + go live.

All of phases 1–3a work **today** with `TOKEN_GATING_ENABLED=false` and mock
balances — we don't need the mint to build or demo.
```
