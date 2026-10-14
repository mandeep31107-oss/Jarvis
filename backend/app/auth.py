"""Password hashing, JWT sessions and dependency helpers.

Auth is OFF by default (single-user local install: every request belongs to the
'local' owner). When ``JARVIS_AUTH_ENABLED=true`` the API requires a bearer token
for anything that touches user data; passwords are stored as PBKDF2-HMAC-SHA256
digests with a per-user salt and never leave the backend.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from .config import get_setting, settings
from .db import get_db
from .models import User

LOCAL_OWNER = "local"
PBKDF2_ROUNDS = 240_000
TOKEN_TTL_HOURS = 12


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _secret() -> str:
    secret = get_setting("jwt_secret", "")
    if not secret:
        secret = secrets.token_hex(32)
        from .config import save_runtime_settings
        save_runtime_settings({"jwt_secret": secret})
    return secret


def create_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def auth_enabled() -> bool:
    return bool(settings.auth_enabled)


def _bearer(authorization: Optional[str], token: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return token or None


def current_owner(authorization: Optional[str] = Header(default=None),
                  token: Optional[str] = Query(default=None),
                  db: Session = Depends(get_db)) -> str:
    """Return the owner id for this request; enforces auth when enabled.

    A ``?token=`` query parameter is accepted as a fallback so that plain browser
    downloads (export files, uploaded images) can be authorised without JS.
    """
    if not auth_enabled():
        return LOCAL_OWNER
    raw = _bearer(authorization, token)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Sign in to continue. Authentication is enabled on this "
                                   "server.")
    payload = decode_token(raw)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Your session expired. Please sign in again.")
    user = db.get(User, payload.get("sub", ""))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown account.")
    return user.id


def optional_owner(authorization: Optional[str] = Header(default=None),
                   token: Optional[str] = Query(default=None),
                   db: Session = Depends(get_db)) -> str:
    """Owner id without forcing a login (used for health/settings pages)."""
    if not auth_enabled():
        return LOCAL_OWNER
    raw = _bearer(authorization, token)
    if raw:
        payload = decode_token(raw)
        if payload:
            user = db.get(User, payload.get("sub", ""))
            if user is not None:
                return user.id
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required.")


def register_user(db: Session, email: str, password: str, display_name: str = "") -> User:
    email = email.strip().lower()
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Passwords need at least 8 characters.")
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="That email is already registered.")
    user = User(id=f"u_{secrets.token_hex(6)}", email=email,
                name=display_name or email.split("@")[0],
                password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if user is None or not user.is_active:
        # spend comparable time so account existence is not leaked by timing
        verify_password(password, hash_password("dummy-timing-pad"))
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def ensure_data_dir_permissions() -> None:
    path = settings.data_dir / "ai_settings.json"
    if path.exists():
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
