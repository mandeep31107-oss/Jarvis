"""Project storage: CRUD, versions, autosave, sharing, images, exports, chat.

Single-user local installs run on SQLite with ``owner_id='local'`` and no auth;
hosted installs set ``JARVIS_AUTH_ENABLED=true`` and every query is scoped by the
authenticated owner. Ownership is enforced here, once, rather than in each route.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import (ChatMessageRow, ExportRow, ImageRow, ProjectRow, ProjectVersionRow,
                      User)
from ..schemas import (ImageRef, ProjectData, ProjectRecord, ProjectVersion, utcnow)

LOCAL_OWNER = "local"
MAX_VERSIONS_PER_PROJECT = 60


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return utcnow()


def session_scope():
    """Context manager that commits on success and rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_local_user(session: Session) -> User:
    user = session.get(User, LOCAL_OWNER)
    if user is None:
        user = User(id=LOCAL_OWNER, email="local@localhost", name="Local user",
                    password_hash="", created_at=_now())
        session.add(user)
        session.flush()
    return user


def _record(row: ProjectRow) -> ProjectRecord:
    data = ProjectData(**(row.data or {}))
    return ProjectRecord(id=row.id, name=row.name, owner_id=row.owner_id,
                         created_at=row.created_at, updated_at=row.updated_at,
                         share_token=row.share_token if row.shared else None,
                         shared=bool(row.shared), data=data)


def _sync_summary(row: ProjectRow, data: ProjectData) -> None:
    row.name = data.name or row.name
    row.active_concept = data.active_concept
    row.built_up_m2 = round(data.metrics.total_built_up_m2, 2)
    row.plot_area_m2 = round(data.plot.area, 2)
    row.updated_at = _now()
    row.status = "designed" if data.floors else "draft"


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def create_project(data: ProjectData, owner_id: str = LOCAL_OWNER,
                   session: Optional[Session] = None) -> ProjectRecord:
    own = session is None
    session = session or SessionLocal()
    try:
        ensure_local_user(session)
        row = ProjectRow(id=secrets.token_hex(8), owner_id=owner_id, name=data.name,
                         created_at=_now(), updated_at=_now(), data=data.model_dump(mode="json"))
        _sync_summary(row, data)
        session.add(row)
        session.add(ProjectVersionRow(id=secrets.token_hex(8), project_id=row.id,
                                      label="Initial design", source="generate",
                                      note="First saved state of this project.",
                                      created_at=_now(), data=data.model_dump(mode="json"),
                                      built_up_m2=row.built_up_m2))
        session.commit()
        return _record(row)
    finally:
        if own:
            session.close()


def get_project(project_id: str, owner_id: str = LOCAL_OWNER,
                session: Optional[Session] = None, include_deleted: bool = False) -> Optional[ProjectRecord]:
    own = session is None
    session = session or SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return None
        if row.deleted_at is not None and not include_deleted:
            return None
        return _record(row)
    finally:
        if own:
            session.close()


def get_project_by_share_token(token: str, session: Optional[Session] = None) -> Optional[ProjectRecord]:
    own = session is None
    session = session or SessionLocal()
    try:
        row = session.execute(select(ProjectRow).where(ProjectRow.share_token == token,
                                                       ProjectRow.shared.is_(True),
                                                       ProjectRow.deleted_at.is_(None))).scalar_one_or_none()
        return _record(row) if row else None
    finally:
        if own:
            session.close()


def list_projects(owner_id: str = LOCAL_OWNER, include_deleted: bool = False,
                  session: Optional[Session] = None) -> List[Dict[str, Any]]:
    own = session is None
    session = session or SessionLocal()
    try:
        stmt = select(ProjectRow).where(ProjectRow.owner_id == owner_id)
        if not include_deleted:
            stmt = stmt.where(ProjectRow.deleted_at.is_(None))
        rows = session.execute(stmt.order_by(ProjectRow.updated_at.desc())).scalars().all()
        out = []
        for row in rows:
            data = row.data or {}
            metrics = (data.get("metrics") or {})
            plot = (data.get("plot") or {})
            requirements = (data.get("requirements") or {})
            out.append({
                "id": row.id, "name": row.name, "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(), "status": row.status,
                "active_concept": row.active_concept,
                "built_up_m2": row.built_up_m2, "plot_area_m2": row.plot_area_m2,
                "floors": len(data.get("floors") or []),
                "rooms": metrics.get("room_count", 0),
                "bedrooms": requirements.get("bedrooms", 0),
                "bathrooms": requirements.get("bathrooms", 0),
                "coverage_pct": metrics.get("ground_coverage_pct", 0),
                "open_space_pct": metrics.get("open_space_pct", 0),
                "city": plot.get("city", ""), "state": plot.get("state", ""),
                "shape": plot.get("shape", "rectangular"),
                "road_side": plot.get("road_side", "S"),
                "style": requirements.get("style", "modern"),
                "shared": bool(row.shared), "thumbnail": row.thumbnail,
                "images": len(row.images), "versions": len(row.versions),
                "deleted": row.deleted_at is not None,
            })
        return out
    finally:
        if own:
            session.close()


def save_project(project_id: str, data: ProjectData, owner_id: str = LOCAL_OWNER,
                 version_label: Optional[str] = None, version_source: str = "manual",
                 note: str = "") -> Optional[ProjectRecord]:
    """Persist the document. ``version_label`` also snapshots a revision."""
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return None
        row.data = data.model_dump(mode="json")
        _sync_summary(row, data)
        row.autosave_at = _now()
        if version_label:
            _add_version(session, row, version_label, version_source, note, data)
        session.commit()
        return _record(row)
    finally:
        session.close()


def autosave(project_id: str, data: ProjectData, owner_id: str = LOCAL_OWNER) -> bool:
    """Cheap frequent save: no new version row unless the label is supplied."""
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return False
        row.data = data.model_dump(mode="json")
        _sync_summary(row, data)
        row.autosave_at = _now()
        session.commit()
        return True
    finally:
        session.close()


def _add_version(session: Session, row: ProjectRow, label: str, source: str, note: str,
                 data: ProjectData) -> ProjectVersionRow:
    version = ProjectVersionRow(id=secrets.token_hex(8), project_id=row.id, label=label[:200],
                                source=source, note=note[:2000], created_at=_now(),
                                data=data.model_dump(mode="json"),
                                built_up_m2=round(data.metrics.total_built_up_m2, 2))
    session.add(version)
    session.flush()
    # keep the version list bounded so a long editing session cannot grow forever
    versions = session.execute(select(ProjectVersionRow)
                               .where(ProjectVersionRow.project_id == row.id)
                               .order_by(ProjectVersionRow.created_at.desc())).scalars().all()
    for old in versions[MAX_VERSIONS_PER_PROJECT:]:
        session.delete(old)
    return version


def list_versions(project_id: str, owner_id: str = LOCAL_OWNER) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return []
        versions = session.execute(select(ProjectVersionRow)
                                   .where(ProjectVersionRow.project_id == project_id)
                                   .order_by(ProjectVersionRow.created_at.desc())).scalars().all()
        return [{
            "id": v.id, "project_id": project_id, "label": v.label, "source": v.source,
            "note": v.note, "created_at": v.created_at.isoformat(),
            "built_up_m2": v.built_up_m2,
            "floors": len((v.data or {}).get("floors") or []),
            "rooms": ((v.data or {}).get("metrics") or {}).get("room_count", 0),
        } for v in versions]
    finally:
        session.close()


def get_version(project_id: str, version_id: str,
                owner_id: str = LOCAL_OWNER) -> Optional[ProjectVersion]:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return None
        v = session.get(ProjectVersionRow, version_id)
        if v is None or v.project_id != project_id:
            return None
        return ProjectVersion(id=v.id, project_id=project_id, label=v.label, source=v.source,
                              created_at=v.created_at, data=ProjectData(**(v.data or {})))
    finally:
        session.close()


def restore_version(project_id: str, version_id: str,
                    owner_id: str = LOCAL_OWNER) -> Optional[ProjectRecord]:
    """Restore a revision, snapshotting the current state first so nothing is lost."""
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return None
        v = session.get(ProjectVersionRow, version_id)
        if v is None or v.project_id != project_id:
            return None
        current = ProjectData(**(row.data or {}))
        _add_version(session, row, f"Before restoring '{v.label}'", "restore",
                     "Automatic safety snapshot taken just before the restore.", current)
        restored = ProjectData(**(v.data or {}))
        restored.history = list(current.history) + [{
            "at": _now().isoformat(), "kind": "restore",
            "message": f"Restored version '{v.label}' saved at {v.created_at:%Y-%m-%d %H:%M}."}]
        row.data = restored.model_dump(mode="json")
        _sync_summary(row, restored)
        session.commit()
        return _record(row)
    finally:
        session.close()


def delete_project(project_id: str, owner_id: str = LOCAL_OWNER, hard: bool = False) -> bool:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return False
        if hard:
            _delete_project_files(row)
            session.delete(row)
        else:
            row.deleted_at = _now()
            row.shared = False
        session.commit()
        return True
    finally:
        session.close()


def restore_deleted(project_id: str, owner_id: str = LOCAL_OWNER) -> bool:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return False
        row.deleted_at = None
        session.commit()
        return True
    finally:
        session.close()


def rename_project(project_id: str, name: str, owner_id: str = LOCAL_OWNER) -> bool:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return False
        row.name = name.strip()[:200] or row.name
        if row.data:
            row.data["name"] = row.name
            row.updated_at = _now()
        session.commit()
        return True
    finally:
        session.close()


def _delete_project_files(row: ProjectRow) -> None:
    settings = get_settings()
    for image in row.images:
        for path in (image.path, image.thumb_path):
            if path:
                _safe_unlink(settings.data_dir / path if not os.path.isabs(path) else path)
    for export in row.exports:
        if export.path:
            _safe_unlink(settings.data_dir / export.path if not os.path.isabs(export.path)
                         else export.path)


def _safe_unlink(path: Any) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# sharing
# ---------------------------------------------------------------------------

def set_sharing(project_id: str, enabled: bool, owner_id: str = LOCAL_OWNER) -> Tuple[bool, Optional[str]]:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return False, None
        row.shared = bool(enabled)
        if enabled:
            if not row.share_token:
                row.share_token = secrets.token_urlsafe(16)
        else:
            row.share_token = None
        session.commit()
        return True, row.share_token
    finally:
        session.close()


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------

def add_image(project_id: str, image: ImageRef, path: str, thumb_path: str = "",
              size_bytes: int = 0, width: int = 0, height: int = 0,
              mime_type: str = "image/jpeg", owner_id: str = LOCAL_OWNER) -> Optional[ImageRow]:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return None
        record = ImageRow(id=image.id or secrets.token_hex(8), project_id=project_id,
                          filename=image.filename, path=path, thumb_path=thumb_path,
                          room_type=image.room_type, goals=list(image.goals), note=image.note,
                          status=image.status, mime_type=mime_type, size_bytes=size_bytes,
                          width=width, height=height, analysis=dict(image.analysis),
                          generated=list(image.generated), created_at=_now())
        session.add(record)
        _append_image_ref(row, image, record.id)
        session.commit()
        return record
    finally:
        session.close()


def _append_image_ref(row: ProjectRow, image: ImageRef, image_id: str) -> None:
    data = row.data or {}
    refs = list(data.get("images") or [])
    payload = image.model_dump(mode="json")
    payload["id"] = image_id
    refs.append(payload)
    data["images"] = refs
    row.data = data


def update_image(project_id: str, image_id: str, *, room_type: Optional[str] = None,
                 goals: Optional[List[str]] = None, note: Optional[str] = None,
                 status: Optional[str] = None, analysis: Optional[Dict[str, Any]] = None,
                 generated: Optional[List[Dict[str, Any]]] = None,
                 owner_id: str = LOCAL_OWNER) -> bool:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return False
        record = session.get(ImageRow, image_id)
        if record is None or record.project_id != project_id:
            return False
        if room_type is not None:
            record.room_type = room_type
        if goals is not None:
            record.goals = list(goals)
        if note is not None:
            record.note = note
        if status is not None:
            record.status = status
        if analysis is not None:
            record.analysis = analysis
        if generated is not None:
            record.generated = generated
        _sync_image_refs(row, session, project_id)
        session.commit()
        return True
    finally:
        session.close()


def _sync_image_refs(row: ProjectRow, session: Session, project_id: str) -> None:
    records = session.execute(select(ImageRow).where(ImageRow.project_id == project_id)
                              .order_by(ImageRow.created_at)).scalars().all()
    data = row.data or {}
    data["images"] = [ImageRef(id=r.id, filename=r.filename, url=f"/api/images/{r.id}/file",
                               room_type=r.room_type, goals=list(r.goals or []), note=r.note,
                               analysis=dict(r.analysis or {}), status=r.status,
                               created_at=r.created_at,
                               generated=list(r.generated or [])).model_dump(mode="json")
                      for r in records]
    row.data = data


def list_images(project_id: str, owner_id: str = LOCAL_OWNER) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return []
        records = session.execute(select(ImageRow).where(ImageRow.project_id == project_id)
                                  .order_by(ImageRow.created_at)).scalars().all()
        return [{"id": r.id, "filename": r.filename, "room_type": r.room_type,
                 "goals": r.goals or [], "note": r.note, "status": r.status,
                 "mime_type": r.mime_type, "size_bytes": r.size_bytes, "width": r.width,
                 "height": r.height, "analysis": r.analysis or {}, "generated": r.generated or [],
                 "created_at": r.created_at.isoformat(), "url": f"/api/images/{r.id}/file",
                 "thumbnail_url": f"/api/images/{r.id}/thumbnail" if r.thumb_path else ""}
                for r in records]
    finally:
        session.close()


def get_image_row(image_id: str, owner_id: Optional[str] = None) -> Optional[ImageRow]:
    session = SessionLocal()
    try:
        record = session.get(ImageRow, image_id)
        if record is None:
            return None
        if owner_id is not None:
            project = session.get(ProjectRow, record.project_id)
            if project is None or project.owner_id != owner_id:
                return None
        session.expunge(record)
        return record
    finally:
        session.close()


def delete_image(project_id: str, image_id: str, owner_id: str = LOCAL_OWNER) -> bool:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return False
        record = session.get(ImageRow, image_id)
        if record is None or record.project_id != project_id:
            return False
        settings = get_settings()
        for path in (record.path, record.thumb_path):
            if path:
                _safe_unlink(settings.data_dir / path if not os.path.isabs(path) else path)
        session.delete(record)
        session.flush()
        _sync_image_refs(row, session, project_id)
        session.commit()
        return True
    finally:
        session.close()


# ---------------------------------------------------------------------------
# exports + chat
# ---------------------------------------------------------------------------

def record_export(project_id: str, kind: str, filename: str, path: str, size_bytes: int = 0,
                  meta: Optional[Dict[str, Any]] = None, owner_id: str = LOCAL_OWNER) -> Optional[str]:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return None
        export = ExportRow(id=secrets.token_hex(8), project_id=project_id, kind=kind,
                           filename=filename, path=path, size_bytes=size_bytes,
                           meta=meta or {}, created_at=_now())
        session.add(export)
        row.status = "exported"
        row.updated_at = _now()
        session.commit()
        return export.id
    finally:
        session.close()


def list_exports(project_id: str, owner_id: str = LOCAL_OWNER) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return []
        records = session.execute(select(ExportRow).where(ExportRow.project_id == project_id)
                                  .order_by(ExportRow.created_at.desc())).scalars().all()
        return [{"id": r.id, "kind": r.kind, "filename": r.filename,
                 "url": f"/api/exports/{r.id}/download", "size_bytes": r.size_bytes,
                 "meta": r.meta or {}, "created_at": r.created_at.isoformat()} for r in records]
    finally:
        session.close()


def get_export(export_id: str, owner_id: Optional[str] = None) -> Optional[ExportRow]:
    session = SessionLocal()
    try:
        record = session.get(ExportRow, export_id)
        if record is None:
            return None
        if owner_id is not None:
            project = session.get(ProjectRow, record.project_id)
            if project is None or project.owner_id != owner_id:
                return None
        session.expunge(record)
        return record
    finally:
        session.close()


def add_message(project_id: str, role: str, content: str,
                meta: Optional[Dict[str, Any]] = None, owner_id: str = LOCAL_OWNER) -> Optional[str]:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return None
        msg = ChatMessageRow(id=secrets.token_hex(8), project_id=project_id, role=role,
                             content=content, meta=meta or {}, created_at=_now())
        session.add(msg)
        session.commit()
        return msg.id
    finally:
        session.close()


def list_messages(project_id: str, limit: int = 200, owner_id: str = LOCAL_OWNER) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return []
        records = session.execute(select(ChatMessageRow)
                                  .where(ChatMessageRow.project_id == project_id)
                                  .order_by(ChatMessageRow.created_at.desc())
                                  .limit(limit)).scalars().all()
        return [{"id": r.id, "role": r.role, "content": r.content, "meta": r.meta or {},
                 "created_at": r.created_at.isoformat()} for r in reversed(records)]
    finally:
        session.close()


def clear_messages(project_id: str, owner_id: str = LOCAL_OWNER) -> int:
    session = SessionLocal()
    try:
        row = session.get(ProjectRow, project_id)
        if row is None or row.owner_id != owner_id:
            return 0
        records = session.execute(select(ChatMessageRow)
                                  .where(ChatMessageRow.project_id == project_id)).scalars().all()
        for r in records:
            session.delete(r)
        session.commit()
        return len(records)
    finally:
        session.close()


def project_history(project_id: str, owner_id: str = LOCAL_OWNER) -> List[Dict[str, Any]]:
    """Combined timeline: saved versions + the in-document event log."""
    record = get_project(project_id, owner_id)
    if record is None:
        return []
    timeline = [{"at": v["created_at"], "kind": "version", "label": v["label"],
                 "source": v["source"], "note": v["note"], "version_id": v["id"],
                 "built_up_m2": v["built_up_m2"]} for v in list_versions(project_id, owner_id)]
    timeline += [{"at": h.get("at", ""), "kind": h.get("kind", "note"),
                  "label": h.get("message", ""), "message": h.get("message", "")}
                 for h in record.data.history]
    timeline.sort(key=lambda t: t.get("at") or "", reverse=True)
    return timeline


def dump_json(project_id: str, owner_id: str = LOCAL_OWNER) -> Optional[str]:
    record = get_project(project_id, owner_id)
    if record is None:
        return None
    return json.dumps({"project": record.model_dump(mode="json"),
                       "versions": list_versions(project_id, owner_id),
                       "images": list_images(project_id, owner_id),
                       "exports": list_exports(project_id, owner_id),
                       "exported_at": _now().isoformat()}, indent=2, default=str)
