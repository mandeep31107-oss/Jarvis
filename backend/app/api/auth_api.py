"""Auth endpoints. When auth is disabled these report status only."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..auth import (LOCAL_OWNER, authenticate, auth_enabled, create_token, current_owner,
                    register_user)
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _check_email(value: str) -> str:
    import re
    value = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) or len(value) > 320:
        raise ValueError("That does not look like an email address.")
    return value


class RegisterBody(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=120)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _check_email(v)


class LoginBody(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _check_email(v)


@router.get("/status")
def status() -> dict:
    return {
        "enabled": auth_enabled(),
        "mode": "accounts" if auth_enabled() else "local-single-user",
        "statement": (
            "Accounts and sign-in are active on this server; projects belong to your account."
            if auth_enabled() else
            "This installation runs in local single-user mode: no account or internet is "
            "needed and every project stays on this machine under your OS user. Enable "
            "JARVIS_AUTH_ENABLED for multi-user cloud use.")
    }


@router.post("/register")
def register(body: RegisterBody, db: Session = Depends(get_db)) -> dict:
    if not auth_enabled():
        raise HTTPException(400, "Accounts are disabled on this server; everything is already "
                               "saved locally for a single user.")
    user = register_user(db, body.email, body.password, body.name)
    return {"token": create_token(user.id, user.email), "user": _public(user)}


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)) -> dict:
    if not auth_enabled():
        raise HTTPException(400, "Accounts are disabled on this server; open the app directly.")
    user = authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(401, "Email or password is incorrect.")
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return {"token": create_token(user.id, user.email), "user": _public(user)}


@router.get("/me")
def me(owner: str = Depends(current_owner), db: Session = Depends(get_db)) -> dict:
    if not auth_enabled():
        return {"owner": LOCAL_OWNER, "email": "", "name": "Local user",
                "mode": "local-single-user"}
    user = db.get(User, owner)
    payload = {"owner": owner, "mode": "accounts"}
    payload.update(_public(user))
    return payload


def _public(user: Optional[User]) -> dict:
    if user is None:
        return {}
    return {"id": user.id, "email": user.email, "name": user.name}
