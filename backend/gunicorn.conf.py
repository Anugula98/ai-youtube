"""Gunicorn config for production. Run with: gunicorn app.main:app -c gunicorn.conf.py

Uses uvicorn's ASGI worker class since the app is FastAPI/async.
"""
import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
worker_class = "uvicorn.workers.UvicornWorker"

# 2-4 workers per core is the usual guidance for I/O-bound ASGI apps; keep it
# modest by default since each worker holds its own DB connection pool.
workers = int(os.environ.get("WEB_CONCURRENCY", max(2, multiprocessing.cpu_count())))

timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5

accesslog = "-"   # stdout — let the container platform collect logs
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()

# Restart workers periodically to guard against slow memory growth in
# long-running agent/LLM-call workloads.
max_requests = 1000
max_requests_jitter = 100
