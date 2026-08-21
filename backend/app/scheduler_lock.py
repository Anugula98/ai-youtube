"""DB-backed mutex for the scheduler tick (see scheduler.py's docstring).

Uses a single-row table + an atomic conditional UPDATE (not a read-then-write
in application code) so lock acquisition is race-free even across multiple
backend processes/replicas hitting the same database concurrently -- the
WHERE clause and SET happen as one atomic statement at the database level,
which works the same way on both SQLite and Postgres, unlike a
database-specific advisory-lock feature.

This is what actually resolves the "opt-in, single-replica-only" limitation
documented in the previous pass: with this in place, `ENABLE_SCHEDULER=true`
is safe to run on more than one replica -- only one will win the lock and
execute a given tick's actions, the others will see the lock held and return
immediately having done nothing.
"""
from __future__ import annotations
import datetime as dt
import uuid

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models as m


def _ensure_lock_row(db: Session) -> None:
    if db.query(m.SchedulerLock).filter(m.SchedulerLock.id == 1).first() is not None:
        return
    try:
        db.add(m.SchedulerLock(id=1, holder=None, acquired_at=None, expires_at=None))
        db.commit()
    except IntegrityError:
        # Another replica created the singleton row between our check and
        # our insert -- that's fine, it exists now either way.
        db.rollback()


def try_acquire(db: Session, holder_id: str | None = None, lease_seconds: int = 120) -> str | None:
    """Attempts to acquire the lock. Returns the holder_id used if
    successful (so the caller can release with the same id), or None if
    another holder currently has an unexpired lease."""
    _ensure_lock_row(db)
    holder_id = holder_id or uuid.uuid4().hex
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    expiry = now + dt.timedelta(seconds=lease_seconds)

    result = db.execute(
        update(m.SchedulerLock)
        .where(m.SchedulerLock.id == 1)
        .where((m.SchedulerLock.expires_at.is_(None)) | (m.SchedulerLock.expires_at < now))
        .values(holder=holder_id, acquired_at=now, expires_at=expiry)
    )
    db.commit()
    return holder_id if result.rowcount == 1 else None


def release(db: Session, holder_id: str) -> None:
    """Releases the lock only if we're still the current holder (a lease
    that already expired and was re-acquired by someone else must not be
    stomped on by a late release from the previous holder)."""
    db.execute(
        update(m.SchedulerLock)
        .where(m.SchedulerLock.id == 1)
        .where(m.SchedulerLock.holder == holder_id)
        .values(holder=None, acquired_at=None, expires_at=None)
    )
    db.commit()
