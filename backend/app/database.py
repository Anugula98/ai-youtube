"""Database engine/session setup.

SQLite by default for local dev; set DATABASE_URL (see config.py / .env.example)
to a Postgres DSN for staging/production. Nothing else in the app is
SQLite-specific -- models use only cross-dialect SQLAlchemy types.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
