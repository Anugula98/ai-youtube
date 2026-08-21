# Deployment

## What "production" means in this scaffold — read this first

This hardens the **infrastructure**: config/secrets via env vars, real Alembic
migrations (no `create_all`), Postgres instead of SQLite, Docker images
running as non-root with health checks, gunicorn with multiple workers,
structured request-correlated logging, a shared-secret API key gate, and an
automated test suite (`pytest`, 13 tests covering the pipeline state machine
and the HTTP API — run them with `pytest tests/` from `backend/`).

It does **not** turn the Script/Voice/Visuals/Copyright/Thumbnail/Publish
agents into production-quality content systems — those are still the
interface-compatible stubs described in the main README. Deploying this as-is
gets you a real, working AV → Research → Fact Check pipeline behind a proper
API; publishing an actual finished video is not yet implemented end to end.

Also be aware of the **API_KEY caveat**: it's a single shared secret baked
into the frontend's `env.js` at deploy time. That's enough to keep a scaffold
off the open internet from casual scraping/bots, but it is *not* per-user
authentication or authorization — anyone with that one key can create,
approve, or reject any project. If you need multiple users with different
permissions, put a real auth layer (OAuth/JWT + a users/roles table) in front
of this before it's reachable by more than one trusted operator.

## Option A — Docker Compose (recommended for a single host / staging)

```bash
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD, API_KEY, CORS_ORIGINS, PUBLIC_BACKEND_API_URL
docker compose up -d --build
```

This starts three containers: `db` (Postgres 16), `backend` (gunicorn +
Alembic auto-migrate on start), `frontend` (nginx serving the dashboard,
with `env.js` regenerated from `BACKEND_API_URL`/`FRONTEND_API_KEY` at
container start — see `frontend/docker-entrypoint.sh`).

**Note on `PUBLIC_BACKEND_API_URL`**: this must be the URL the *browser* can
reach — not the Docker-internal `http://backend:8000` service name. In a
real deployment you'd put a reverse proxy / load balancer (e.g. Caddy,
Traefik, or a cloud LB) in front of both the `backend` and `frontend`
containers, terminate TLS there, and point `PUBLIC_BACKEND_API_URL` at that
proxy's public hostname for the backend.

**Note on migrations at scale**: the Dockerfile runs `alembic upgrade head`
on every container start, which is safe for a single backend replica (it's
idempotent — a no-op once the DB is already at head). If you run more than
one backend replica, move the migration to a separate one-shot release step
(a `docker compose run --rm backend alembic upgrade head` before scaling up,
or your platform's release-phase hook) instead of letting every replica race
to migrate concurrently.

**Note on the background scheduler (`ENABLE_SCHEDULER=true`)**: as of this
pass, the scheduler tick is safe to run on more than one backend replica —
it uses a DB-backed lock (`app/scheduler_lock.py`, a single-row mutex with an
atomic conditional UPDATE) so only one replica executes a given tick's
actions; the rest see the lock held and return immediately. Tested for the
lock-contention and lock-expiry-and-reacquisition cases specifically. If you
still prefer not to run it at all, leave `ENABLE_SCHEDULER` off and drive
`POST /api/scheduler/tick` from a single external cron job instead — that
endpoint also goes through the same lock, so it's safe to call from more
than one place too.

## Option B — Bare metal / VM (no Docker)

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export ENV=production
export DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/newsroom
export CORS_ORIGINS=https://newsroom.example.com
export API_KEY=<generate a strong random value>
export ANTHROPIC_API_KEY=<your key, or omit to run agents against the mock>

alembic upgrade head
gunicorn app.main:app -c gunicorn.conf.py
```

Put this behind a real reverse proxy (nginx/Caddy) for TLS termination — the
app itself serves plain HTTP.

Serve `frontend/index.html` + `frontend/env.js` (edited to point at your
backend URL/key) from any static file host — S3+CloudFront, nginx, Netlify,
etc. There's no build step; it's a single static HTML file with vanilla JS.

## Running the test suite

```bash
cd backend
pip install -r requirements-dev.txt
pytest tests/ -v
```

13 tests, isolated per-test SQLite DB, no external services required. Covers:
the fact-check gate correctly blocking and looping back to Research, REVIEW
mode correctly pausing (and approval correctly un-pausing — this test caught
a real bug during development, see `test_approving_a_needs_review_stage_advances_it`),
version history being preserved on re-run, and the HTTP layer (auth-free
health checks, 404s, claim/source persistence over the API).

## What I verified locally vs. what I could not

Verified in this sandbox: Alembic migration applies cleanly and produces the
expected tables; the full pytest suite passes; gunicorn (the exact process
manager the Dockerfile runs) boots multiple workers and serves real
end-to-end requests correctly; the API-key auth gate and the
production-CORS-guard both behave correctly under test.

**Not verified**: this sandbox has no Docker daemon, so the actual `docker
build` / `docker compose up` path has not been run here. The Dockerfiles and
compose file are written correctly to the best of my review, but treat the
first `docker compose up --build` on your end as the real first test of that
specific path, not something already proven working.
