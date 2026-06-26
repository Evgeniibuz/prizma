# PULSΞ

Crypto market intelligence platform with an AI agent. FastAPI backend,
vanilla JS frontend, PostgreSQL + Redis.

## Quick start

```bash
git clone <repo>
cd PRIZM-main
cp .env.example .env
# Edit .env — at minimum set a real JWT_SECRET
docker compose up --build
```

Then open <http://localhost:8000>. Demo account: `demo` / `demo1234`.

## Architecture

```
┌──────────────┐    ┌──────────────────┐    ┌──────────────┐
│  Frontend    │───▶│  FastAPI         │───▶│  PostgreSQL  │
│  (static)    │    │  (uvicorn)       │───▶│  Redis       │
└──────────────┘    └──────────────────┘    └──────────────┘
                            │
                            └─▶ LLM / CoinGecko / Twitter / Blockchair / DexScreener
```

Everything lives in one container image (FastAPI serves both the JSON API
and the static frontend) for simple deployment. In prod you can put a CDN
or nginx in front and skip the static mounts entirely.

## Project layout

```
PULSΞ/
├── python/
│   ├── main.py              FastAPI entrypoint, lifespan, routers
│   ├── database.py          SQLAlchemy async engine + Base
│   ├── models.py            All ORM models
│   ├── auth.py              JWT helpers, password hashing
│   ├── signals_cache.py     Async signal persistence (Postgres)
│   ├── routes/
│   │   ├── auth_routes.py
│   │   ├── market_routes.py
│   │   ├── agent_routes.py
│   │   ├── radar_routes.py
│   │   ├── logs_routes.py
│   │   ├── dexscreener_routes.py
│   │   └── blockchair_routes.py
│   └── services/
│       └── sentiment.py
├── frontend/
│   ├── index.html           landing + login/register modals
│   ├── dashboard.html       main app
│   ├── radar.html           live radar view
│   ├── agents.html          multi-agent lab
│   ├── api.js               API client (single source of truth for auth)
│   ├── utils.js             shared helpers
│   ├── config.js            API_BASE, branding
│   └── style.css
├── database/
│   └── init.sql             Postgres schema + demo user
├── images/                  favicons (place real assets here)
├── Dockerfile
├── docker-compose.yml       prod stack
├── docker-compose.override.yml  dev overrides (hot reload)
├── requirements.txt
└── .env.example
```

## Development

`docker-compose.override.yml` is picked up automatically and gives you:

- Hot reload (`uvicorn --reload`)
- Bind mounts of `python/`, `frontend/`, `images/`

For local Python work without Docker:

```bash
cd python
python -m venv .venv && source .venv/bin/activate
pip install -r ../requirements.txt
export DATABASE_URL=postgresql://pulse_user:pulse_dev_password@localhost:5432/pulse_db
export JWT_SECRET=dev
uvicorn main:app --reload
```

## Frontend / backend contract

- Auth token: stored under `localStorage['pulse_token']`.
- Username: stored under `localStorage['pulse_username']` after login or
  register. Anywhere the UI shows the user, it reads from here.
- All authenticated requests use `Authorization: Bearer <token>`.

## External service keys

All keys are optional. If a key is missing the corresponding endpoint
returns an empty/error payload but the rest of the app still works.

| Variable                | What it unlocks                         |
| ----------------------- | --------------------------------------- |
| `LLM             `      | AI agent chat, missions, signals        |
| `TWITTER_BEARER_TOKEN`  | Real cashtag mentions + sentiment       |
| `COINGECKO_API_KEY`     | Higher CoinGecko rate limits            |

## $PLSX token (hold-to-access tiers)

The token utility is config-driven and **runs without a minted token**: while
`TOKEN_GATING_ENABLED=false` (the default) or `PULSE_TOKEN_MINT` is empty,
every wallet resolves to the top `pro` tier so the full product is usable for
development and demos. Flip the flag and set the mint to go live.

| Variable                    | Default                              | Purpose                                   |
| --------------------------- | ------------------------------------ | ----------------------------------------- |
| `TOKEN_GATING_ENABLED`      | `false`                              | Master switch for hold-to-access gating   |
| `PULSE_TOKEN_MINT`          | _(empty)_                            | SPL mint address of $PLSX                  |
| `SOLANA_RPC_URL`            | `https://api.mainnet-beta.solana.com`| RPC used to read balances                 |
| `TIER_HOLDER_MIN`           | `1000`                               | $PLSX needed for `holder` tier             |
| `TIER_PRO_MIN`              | `25000`                              | $PLSX needed for `pro` tier                |
| `BALANCE_CACHE_TTL`         | `900`                                | Seconds to cache a wallet's balance       |
| `AGENT_SCHEDULER_ENABLED`   | `true`                               | Run autonomous dedicated-agent missions   |
| `AGENT_SCHEDULER_INTERVAL_SEC` | `300`                             | How often the scheduler checks for due agents |
| `STAKING_RELEASE_LABEL`     | `Release soon`                       | Label shown on the staking page           |

Tiers gate: **Radar** premium depth + AI breakdown (`holder`+), and the
**Dedicated Agent** strategy/autonomous missions (`pro`). Prediction staking is
Phase 3 — the UI ships a "coming soon" page (`/staking`); see `TOKEN.md`.

## Useful endpoints

| Endpoint                        | Auth | Notes                       |
| ------------------------------- | ---- | --------------------------- |
| `POST /api/auth/register`       | no   | username + password         |
| `POST /api/auth/login`          | no   |                             |
| `GET  /api/auth/me`             | yes  |                             |
| `POST /api/auth/logout`         | yes  | logs the event              |
| `GET  /api/market`              | no   | top 8 coins, 30s cache      |
| `GET  /api/market/top/{limit}`  | no   | top N, 2 min cache          |
| `GET  /api/agent/signals/latest`| no   | latest signal batch         |
| `POST /api/agent/signals`       | no   | generate new signals        |
| `GET  /api/radar/signals`       | yes  | rate-limited 30/min         |
| `GET  /api/radar/breakdown/{s}` | yes  | rate-limited 20/min         |
| `GET  /api/logs`                | yes  | activity log for user       |
| `GET  /health`                  | no   | liveness probe              |

## Production checklist

- [ ] Set `PULSE_ENV=production` and a real `JWT_SECRET`
- [ ] Set `PULSE_ALLOWED_ORIGINS` to your real domain(s)
- [ ] Provide secrets via your platform's secret manager, not `.env` in repo
- [ ] Put a TLS terminator (nginx, Caddy, cloud LB) in front
- [ ] Back up the `postgres_data` volume
- [ ] Replace placeholder favicons in `images/`

## Demo credentials

The `init.sql` seeds one user:

- username: `demo`
- password: `demo1234`

Delete that block before going to production.
