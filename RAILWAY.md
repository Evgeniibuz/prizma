# Deploying PULSΞ to Railway

Step-by-step guide for deploying without Docker, using Railway's Nixpacks builder.

## TL;DR

1. Push this repo to GitHub
2. Create a Railway project, link the repo
3. Add Postgres and Redis plugins
4. Set 5 environment variables (see below)
5. Deploy — schema bootstraps itself on first run

---

## 1. What gets deployed

Three services in one Railway project:

| Service  | Source                 | Purpose                  |
|----------|------------------------|--------------------------|
| Web      | this repo (Nixpacks)   | FastAPI + static frontend |
| Postgres | Railway plugin         | Primary data store        |
| Redis    | Railway plugin         | Distributed rate-limit    |

Railway's build will:
- Pin Python 3.11 (`nixpacks.toml`)
- Install everything from `requirements.txt`
- Pre-download TextBlob corpora
- Start `uvicorn main:app` on `$PORT` (`railway.json`)

On the **first** request to the freshly-deployed app, `database.py`
notices the empty Postgres and runs `database/init.sql` automatically.
Subsequent boots skip this step.

## 2. Create the project

```
railway login
railway init       # name it "pulse" or similar
railway link       # if you already created it via the web UI
```

Or use the web UI: **New Project → Deploy from GitHub repo → pick this repo.**

## 3. Add plugins

In your Railway project dashboard:

1. **+ New → Database → Add PostgreSQL.** Wait for it to provision.
2. **+ New → Database → Add Redis.** Wait for it to provision.

Both plugins expose connection strings under their **Variables** tab.

## 4. Configure the web service

Open your repo-backed service → **Variables**.

### Required

| Key                  | Value                                          | Notes |
|----------------------|------------------------------------------------|-------|
| `PULSE_ENV`          | `production`                                   | Enables strict checks |
| `JWT_SECRET`         | `python -c "import secrets; print(secrets.token_urlsafe(48))"` | One-shot generate |
| `DATABASE_URL`       | `${{Postgres.DATABASE_URL}}`                   | Reference, not literal |
| `REDIS_URL`          | `${{Redis.REDIS_URL}}`                         | Reference, not literal |
| `PULSE_ALLOWED_ORIGINS` | `https://<your-app>.up.railway.app`         | Add custom domain too, comma-separated |

The `${{Postgres.DATABASE_URL}}` syntax is Railway's reference variable —
type it exactly like that, Railway resolves it at deploy time.

### Optional API keys

| Key                    | What it unlocks                          |
|------------------------|------------------------------------------|
| `DEEPSEEK_API_KEY`     | AI agent chat, missions, signals         |
| `COINGECKO_API_KEY`    | Higher CoinGecko rate limits             |
| `TWITTER_BEARER_TOKEN` | Real cashtag mentions + sentiment        |
| `DEEPSEEK_BASE_URL`    | Default: `https://api.deepseek.com/v1`   |
| `DEEPSEEK_MODEL`       | Default: `deepseek-chat`                 |
| `LOG_LEVEL`            | Default: `INFO` (use `DEBUG` to debug)   |

The app starts and runs fine without these — endpoints that need a
specific key return an empty/error payload instead of crashing.

## 5. Deploy

```
git push                # if Railway is wired to GitHub, deploy is auto
# OR
railway up              # CLI push
```

First deploy takes ~2 minutes (Nixpacks builds, installs deps, downloads corpora).

## 6. Verify

After deploy, hit your URL:

- `https://<your-app>.up.railway.app/health` → `{"status":"ok",...}`
- `https://<your-app>.up.railway.app/` → landing page
- `https://<your-app>.up.railway.app/dashboard` → dashboard (login required)

Then register a user via the UI. The first registered account is yours —
there is **no seeded demo user** in production.

Tail logs with `railway logs` (CLI) or the **Deployments** tab in the web UI.
You should see:

```
INFO main: Frontend path: /app/frontend (exists=True)
INFO database: Database connection OK
INFO database: Empty database detected — running init.sql bootstrap from ...
INFO database: Schema bootstrap completed (41 statements executed)
INFO:     Application startup complete.
```

On the second deploy and onwards:

```
INFO database: Schema already present, skipping bootstrap
```

## 7. Custom domain (optional)

Service → **Settings → Networking → Custom Domain.** Add your domain,
point its CNAME at the value Railway shows. Then **append the new origin
to `PULSE_ALLOWED_ORIGINS`** and redeploy.

## 8. Manually inspecting Postgres

```
railway connect Postgres      # opens psql shell
\dt                           # list tables
select count(*) from users;
```

## 9. Resetting the schema

If you ever want to wipe and re-bootstrap:

```
railway connect Postgres
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```

Then redeploy (or restart the service). The bootstrap routine will pick
up the empty DB and re-run `init.sql`.

## Troubleshooting

| Symptom                                  | Cause / fix                                                                 |
|------------------------------------------|------------------------------------------------------------------------------|
| App crashloops at boot                   | `JWT_SECRET` still default with `PULSE_ENV=production` — set a real secret  |
| `Database connection failed`             | `DATABASE_URL` not linked — use `${{Postgres.DATABASE_URL}}` reference      |
| Endpoints return CORS errors in browser  | Origin not in `PULSE_ALLOWED_ORIGINS`. Comma-separated, full `https://` URL |
| Schema bootstrap fails                   | Check Postgres has `uuid-ossp` and `pgcrypto` (Railway's image does)        |
| Rate-limit ignores my IP                 | Behind Railway's proxy — code reads `x-forwarded-for`, already handled       |
| `/health` works but `/` returns 404      | `frontend/` directory wasn't shipped — confirm it's in the repo, not ignored |

## What is NOT used on Railway

These files are kept for local development with Docker Compose and are
ignored by the Nixpacks builder:

- `Dockerfile`
- `docker-compose.yml`
- `docker-compose.override.yml`

Run them locally with `docker compose up --build` if you want a self-contained
dev stack on your machine.
