"""Project CRUD, versions, autosave, sharing and history."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_owner
from ..schemas import Plot, ProjectData, Requirements
from ..services import storage
from ..services.designer import (DEFAULT_CONCEPTS, generate_design, missing_information,
                                 project_summary)
from ..services.events import done, progress, project_changed
from ..services.validation import validation_result

router = APIRouter(prefix="/api", tags=["projects"])


class CreateBody(BaseModel):
    name: str = Field(default="My House", max_length=120)
    plot: Plot
    requirements: Requirements = Field(default_factory=Requirements)
    concepts: List[str] = Field(default_factory=list)
    active: Optional[str] = None


class SaveBody(BaseModel):
    data: ProjectData
    label: str = ""
    note: str = ""


class RenameBody(BaseModel):
    name: str = Field(max_length=120)


class ShareBody(BaseModel):
    enabled: bool


def _load(project_id: str, owner: str):
    record = storage.get_project(project_id, owner_id=owner)
    if record is None:
        raise HTTPException(404, "That project does not exist (or belongs to another account).")
    return record


def _payload(record: Any) -> Dict[str, Any]:
    data: ProjectData = record.data
    result = validation_result(data)
    return {
        "id": record.id,
        "name": record.name,
        "owner_id": record.owner_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "share_token": record.share_token,
        "sharing_enabled": bool(record.shared),
        "summary": project_summary(data),
        "validation": {"counts": result.counts, "issues": [i.model_dump() for i in result.issues]},
        "missing_information": missing_information(data.plot, data.requirements),
        "data": data.model_dump(mode="json"),
    }


@router.get("/projects")
def list_projects(owner: str = Depends(current_owner)) -> Dict[str, Any]:
    rows = storage.list_projects(owner_id=owner)
    return {"projects": rows, "auth_owner": owner}


@router.post("/projects")
def create_project(body: CreateBody, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    from ..services.designer import new_project
    data = new_project(body.name, body.plot, body.requirements)
    record = storage.create_project(data, owner_id=owner)
    try:
        progress(record.id, "generate", 20, "Working out the room programme")
        generate_design(data, concepts=body.concepts or list(DEFAULT_CONCEPTS),
                        active=body.active)
        storage.save_project(record.id, data, owner_id=owner, version_label="initial design",
                             version_source="generate",
                             note="First concepts generated from the brief")
        done(record.id, "generate", "Concepts ready")
    except Exception as exc:                      # never lose the project on failure
        storage.save_project(record.id, data, owner_id=owner, version_label="brief saved",
                             version_source="create", note=f"Generation failed: {exc}")
        raise HTTPException(500, f"The layout engine could not produce a design: {exc}. "
                               f"Your brief is saved; adjust it and try again.")
    record = storage.get_project(record.id, owner_id=owner)
    return _payload(record)


@router.get("/projects/{project_id}")
def get_project(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    return _payload(_load(project_id, owner))


@router.put("/projects/{project_id}")
def save_project(project_id: str, body: SaveBody, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    record = storage.save_project(project_id, body.data, owner_id=owner,
                                  version_label=body.label or "edit", version_source="save",
                                  note=body.note)
    if record is None:
        raise HTTPException(404, "Project not found.")
    project_changed(project_id, "saved")
    record = storage.get_project(project_id, owner_id=owner)
    return {"saved": True, "updated_at": record.updated_at.isoformat(),
            "versions": storage.list_versions(project_id, owner_id=owner)[:1]}


@router.post("/projects/{project_id}/autosave")
def autosave(project_id: str, body: SaveBody, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    ok = storage.autosave(project_id, body.data, owner_id=owner)
    project_changed(project_id, "autosave")
    return {"saved": ok}


@router.patch("/projects/{project_id}/rename")
def rename(project_id: str, body: RenameBody, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    ok = storage.rename_project(project_id, body.name, owner_id=owner)
    if not ok:
        raise HTTPException(404, "Project not found.")
    return {"renamed": True, "name": body.name}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, hard: bool = False,
                   owner: str = Depends(current_owner)) -> Dict[str, Any]:
    ok = storage.delete_project(project_id, owner_id=owner, hard=hard)
    if not ok:
        raise HTTPException(404, "Project not found.")
    return {"deleted": True, "hard": hard}


@router.post("/projects/{project_id}/restore")
def restore_deleted(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    ok = storage.restore_deleted(project_id, owner_id=owner)
    if not ok:
        raise HTTPException(404, "Nothing to restore.")
    return _payload(storage.get_project(project_id, owner_id=owner))


@router.get("/projects/{project_id}/versions")
def versions(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    return {"versions": storage.list_versions(project_id, owner_id=owner)}


class VersionBody(BaseModel):
    label: str = "checkpoint"
    note: str = ""


@router.post("/projects/{project_id}/versions")
def create_version(project_id: str, body: VersionBody,
                   owner: str = Depends(current_owner)) -> Dict[str, Any]:
    record = _load(project_id, owner)
    from ..models import ProjectRow
    from ..services.storage import _add_version, session_scope
    with session_scope() as session:
        row = session.get(ProjectRow, project_id)
        _add_version(session, row, body.label or "checkpoint", "manual",
                     body.note or "Saved by user", record.data)
    return {"versions": storage.list_versions(project_id, owner_id=owner)}


@router.get("/projects/{project_id}/versions/{version_id}")
def get_version(project_id: str, version_id: str,
                owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    version = storage.get_version(project_id, version_id, owner_id=owner)
    if version is None:
        raise HTTPException(404, "Version not found.")
    return {"id": version.id, "project_id": version.project_id, "label": version.label,
            "source": version.source, "created_at": version.created_at.isoformat(),
            "data": version.data.model_dump(mode="json")}


@router.post("/projects/{project_id}/versions/{version_id}/restore")
def restore_version(project_id: str, version_id: str,
                    owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    record = storage.restore_version(project_id, version_id, owner_id=owner)
    if record is None:
        raise HTTPException(404, "Version not found.")
    project_changed(project_id, "restored")
    return _payload(storage.get_project(project_id, owner_id=owner))


@router.get("/projects/{project_id}/history")
def history(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    return {"events": storage.project_history(project_id, owner_id=owner)}


@router.get("/projects/{project_id}/dump")
def dump(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    raw = storage.dump_json(project_id, owner_id=owner)
    return {"json": raw}


@router.post("/projects/{project_id}/sharing")
def sharing(project_id: str, body: ShareBody, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    ok, token = storage.set_sharing(project_id, body.enabled, owner_id=owner)
    if not ok:
        raise HTTPException(404, "Project not found.")
    return {"sharing_enabled": body.enabled, "share_token": token,
            "url": f"/shared/{token}" if token else None,
            "note": ("Anyone with this link can view the project read-only. Sharing is off by "
                     "default and can be revoked at any time." if body.enabled else
                     "The share link was revoked.")}


@router.get("/shared/{token}")
def shared(token: str) -> Dict[str, Any]:
    record = storage.get_project_by_share_token(token)
    if record is None or not record.shared:
        raise HTTPException(404, "This share link is not active.")
    data: ProjectData = record.data
    return {"id": record.id, "name": record.name, "read_only": True,
            "summary": project_summary(data), "data": data.model_dump(mode="json")}
