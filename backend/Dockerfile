FROM python:3.12-slim AS base

# psycopg2-binary needs libpq at runtime; build tools only needed transiently.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create the uploads dir with correct ownership *before* switching to the
# non-root user. This matters specifically because of how Docker named
# volumes initialize: when docker-compose mounts a fresh named volume at
# /app/uploads, Docker copies whatever's already at that path in the image
# (including ownership) into the new volume on first creation. Without this
# step, that path wouldn't exist in the image, the volume would be created
# root-owned, and every upload would fail with a permission error the first
# time this ships to a real deployment.
RUN mkdir -p /app/uploads

# Run as a non-root user in production.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)" || exit 1

# Apply migrations, then start the production server. Migrations run on every
# container start, which is safe: Alembic upgrades are idempotent (no-op if
# already at head) — this is what lets a rolling deploy self-migrate.
CMD alembic upgrade head && gunicorn app.main:app -c gunicorn.conf.py
