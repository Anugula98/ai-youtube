import os

# Force an isolated in-memory-per-connection SQLite DB for the whole test
# session, before any app module (which reads settings at import time) loads.
os.environ["DATABASE_URL"] = "sqlite:///./test_newsroom.db"
os.environ["ENV"] = "development"
os.environ["CORS_ORIGINS"] = "*"
os.environ.pop("API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine, SessionLocal
from app.main import app


@pytest.fixture(autouse=True)
def _fresh_db():
    """Recreate all tables before every test — cheap enough at this scale
    and keeps tests fully isolated from each other."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client():
    return TestClient(app)
