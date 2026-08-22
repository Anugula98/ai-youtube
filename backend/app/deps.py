"""FastAPI dependencies for authentication. Complements (doesn't replace)
main.py's existing require_api_key, which stays available for
service-to-service calls (e.g. the scheduler tick). get_current_user is
what project-scoped, user-owned endpoints should depend on going forward.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from . import models as m
from .database import get_db
from .security import decode_token, InvalidTokenError


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> m.User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        user_id = decode_token(token, expected_type="access")
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

    user = db.get(m.User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def require_project_owner(project_id: int, current_user: m.User = Depends(get_current_user), db: Session = Depends(get_db)) -> m.Project:
    """Loads the project and 401/404s unless it belongs to current_user.
    Returns 404 rather than 403 for someone else's project -- doesn't leak
    that the project id exists at all to a non-owner."""
    project = db.get(m.Project, project_id)
    if not project or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project