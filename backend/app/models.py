"""SQLAlchemy models.

The design document itself is stored as JSON (a single ``ProjectData`` blob) so the
nested rooms / openings / furniture structure is always written and read as one
consistent unit. Everything around it -- users, versions, images, exports, chat --
gets proper relational rows so it can be queried, listed and deleted
independently. The same models work on SQLite (default, single-user local install)
and PostgreSQL (``JARVIS_DATABASE_URL``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .schemas import utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(400), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    projects: Mapped[List["ProjectRow"]] = relationship(back_populates="owner",
                                                        cascade="all, delete-orphan")


class ProjectRow(Base):
    """One design project. ``data`` holds the full ProjectData document."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True,
                                          default="local")
    name: Mapped[str] = mapped_column(String(200), default="Untitled House Project")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    active_concept: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    built_up_m2: Mapped[float] = mapped_column(Float, default=0.0)
    plot_area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    thumbnail: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(24), default="draft")   # draft|designed|exported
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    share_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    autosave_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    owner: Mapped[User] = relationship(back_populates="projects")
    versions: Mapped[List["ProjectVersionRow"]] = relationship(
        back_populates="project", cascade="all, delete-orphan",
        order_by="ProjectVersionRow.created_at.desc()")
    images: Mapped[List["ImageRow"]] = relationship(back_populates="project",
                                                    cascade="all, delete-orphan")
    exports: Mapped[List["ExportRow"]] = relationship(back_populates="project",
                                                      cascade="all, delete-orphan")
    messages: Mapped[List["ChatMessageRow"]] = relationship(back_populates="project",
                                                            cascade="all, delete-orphan")

    __table_args__ = (Index("ix_projects_owner_updated", "owner_id", "updated_at"),)


class ProjectVersionRow(Base):
    """Saved revision (manual save, autosave milestone, or undo checkpoint)."""

    __tablename__ = "project_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), ForeignKey("projects.id"), index=True)
    label: Mapped[str] = mapped_column(String(200), default="Version")
    source: Mapped[str] = mapped_column(String(24), default="manual")   # manual|autosave|generate|restore
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    built_up_m2: Mapped[float] = mapped_column(Float, default=0.0)

    project: Mapped[ProjectRow] = relationship(back_populates="versions")


class ImageRow(Base):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), ForeignKey("projects.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300), default="")
    path: Mapped[str] = mapped_column(String(500), default="")
    thumb_path: Mapped[str] = mapped_column(String(500), default="")
    room_type: Mapped[str] = mapped_column(String(40), default="other")
    goals: Mapped[List[str]] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    mime_type: Mapped[str] = mapped_column(String(60), default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    analysis: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    generated: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped[ProjectRow] = relationship(back_populates="images")


class ExportRow(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="plan_png")
    filename: Mapped[str] = mapped_column(String(300), default="")
    path: Mapped[str] = mapped_column(String(500), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped[ProjectRow] = relationship(back_populates="exports")


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="user")       # user|assistant|system
    content: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    project: Mapped[ProjectRow] = relationship(back_populates="messages")


class AiSettingRow(Base):
    """Provider configuration that must never reach the frontend."""

    __tablename__ = "ai_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
