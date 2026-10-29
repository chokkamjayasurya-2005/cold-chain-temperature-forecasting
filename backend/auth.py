from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash

from backend import database

password_hash = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hash.hash(secrets.token_urlsafe(32))


def validate_password_strength(value: str) -> str:
    classes = (
        any(character.islower() for character in value),
        any(character.isupper() for character in value),
        any(character.isdigit() for character in value),
        any(character in string.punctuation for character in value),
    )
    if len(value) < 6 or sum(classes) < 3:
        raise ValueError(
            "Password must be at least 6 characters and include at least three "
            "of lowercase, uppercase, number, and symbol."
        )
    return value


def hash_password(value: str) -> str:
    validate_password_strength(value)
    return password_hash.hash(value)


def verify_password(value: str, hashed: str) -> bool:
    return password_hash.verify(value, hashed)


def current_user(request: Request) -> dict:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        raise HTTPException(status_code=401, detail="Authentication required.")
    user = database.get_user_by_id(user_id)
    if user is None or not user["is_active"]:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Authentication required.")
    expires_at = request.session.get("session_expires_at")
    try:
        session_valid = isinstance(expires_at, str) and datetime.fromisoformat(expires_at) > datetime.now(timezone.utc)
    except ValueError:
        session_valid = False
    if not session_valid:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
    if request.session.get("password_changed_at") != user["password_changed_at"]:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
    if user["must_change_password"]:
        allowed_paths = {"/auth/me", "/auth/change-password", "/auth/logout", "/auth/csrf"}
        if request.url.path not in allowed_paths:
            raise HTTPException(status_code=403, detail="Change your password before continuing.")
    return user


def admin_user(user: Annotated[dict, Depends(current_user)], request: Request) -> dict:
    if user["role"] != "ADMIN":
        database.log_activity(
            actor_user_id=user["id"],
            action="access_denied",
            target_type="route",
            target_id=request.url.path,
            outcome="failure",
            safe_metadata={"method": request.method},
        )
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user


def csrf_protected(request: Request) -> None:
    expected = request.session.get("csrf_token")
    supplied = request.headers.get("x-csrf-token")
    if not isinstance(expected, str) or not isinstance(supplied, str):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
    if not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


CurrentUser = Annotated[dict, Depends(current_user)]
AdminUser = Annotated[dict, Depends(admin_user)]
CsrfProtected = Annotated[None, Depends(csrf_protected)]
