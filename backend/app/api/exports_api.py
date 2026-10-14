"""Export endpoints: PNG/SVG/PDF plans, design report PDF, GLB, JSON, CSVs.

Every export is written to the server export folder, recorded in the database and
served back through an owner-checked download route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..auth import current_owner
from ..config import EXPORT_DIR
from ..schemas import ProjectData
from ..services import storage
from ..services.events import done, progress
from ..services.export import plan2d, report
from ..services.export.gltf import build_glb

router = APIRouter(prefix="/api", tags=["exports"])

KINDS = {
    "plan_png": ("image/png", ".png"),
    "plan_svg": ("image/svg+xml", ".svg"),
    "plans_pdf": ("application/pdf", ".pdf"),
    "report_pdf": ("application/pdf", ".pdf"),
    "glb": ("model/gltf-binary", ".glb"),
    "json": ("application/json", ".json"),
    "csv_rooms": ("text/csv", ".csv"),
    "csv_materials": ("text/csv", ".csv"),
    "csv_furniture": ("text/csv", ".csv"),
    "csv_budget": ("text/csv", ".csv"),
}


class ExportBody(BaseModel):
    kind: str = Field(default="plan_png")
    floor_id: Optional[str] = None
    all_floors: bool = True
    scale: float = 40.0
    inline: bool = False


def _load(project_id: str, owner: str) -> ProjectData:
    record = storage.get_project(project_id, owner_id=owner)
    if record is None:
        raise HTTPException(404, "That project does not exist (or belongs to another account).")
    return record.data


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_ ")[:60].strip() or "project"


@router.post("/projects/{project_id}/exports")
def create_export(project_id: str, body: ExportBody,
                  owner: str = Depends(current_owner)) -> Any:
    if body.kind not in KINDS:
        raise HTTPException(400, f"Unknown export kind '{body.kind}'. Available: "
                               f"{', '.join(sorted(KINDS))}.")
    data = _load(project_id, owner)
    progress(project_id, "export", 30, f"Rendering {body.kind}")
    media, ext = KINDS[body.kind]
    slug = _safe(data.name)
    if body.kind == "plan_png":
        payload = plan2d.plan_png(data, floor_id=body.floor_id, all_floors=body.all_floors,
                                  scale=body.scale)
        label = "floor plan PNG"
    elif body.kind == "plan_svg":
        payload = plan2d.plan_svg(data, floor_id=body.floor_id,
                                  all_floors=body.all_floors).encode("utf-8")
        label = "floor plan SVG"
    elif body.kind == "plans_pdf":
        payload = report.build_plans_pdf(data)
        label = "floor plans PDF"
    elif body.kind == "report_pdf":
        payload = report.build_report_pdf(data)
        label = "design report PDF"
    elif body.kind == "glb":
        payload = build_glb(data)
        label = "3D model GLB"
    elif body.kind == "json":
        payload = report.documented_json(data).encode("utf-8")
        label = "documented JSON"
    elif body.kind == "csv_rooms":
        payload = report.room_schedule_csv(data).encode("utf-8")
        label = "room schedule CSV"
    elif body.kind == "csv_materials":
        payload = report.material_list_csv(data).encode("utf-8")
        label = "material list CSV"
    elif body.kind == "csv_furniture":
        payload = report.furniture_list_csv(data).encode("utf-8")
        label = "furniture list CSV"
    else:
        payload = report.budget_csv(data).encode("utf-8")
        label = "budget CSV"

    folder = EXPORT_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}-{body.kind}{ext}"
    path = folder / filename
    path.write_bytes(payload)
    export_id = storage.record_export(project_id, body.kind, filename,
                                      str(path.relative_to(EXPORT_DIR.parent)),
                                      size_bytes=len(payload),
                                      meta={"label": label, "floors": [f.id for f in data.floors]},
                                      owner_id=owner)
    done(project_id, "export", f"{label} ready")
    if body.inline:
        return Response(content=payload, media_type=media,
                        headers={"Content-Disposition": f'inline; filename="{filename}"'})
    return {"id": export_id, "kind": body.kind, "filename": filename, "label": label,
            "size_bytes": len(payload), "url": f"/api/exports/{export_id}/download",
            "media_type": media}


@router.get("/projects/{project_id}/exports")
def list_exports(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    return {"exports": storage.list_exports(project_id, owner_id=owner)}


@router.get("/exports/{export_id}/download")
def download(export_id: str, owner: str = Depends(current_owner)) -> FileResponse:
    row = storage.get_export(export_id, owner_id=owner)
    if row is None:
        raise HTTPException(404, "Export not found (or it belongs to another account).")
    path = Path(row.path)
    if not path.is_absolute():
        path = EXPORT_DIR.parent / path
    if not path.exists():
        raise HTTPException(410, "The file for this export is no longer on disk.")
    media = KINDS.get(row.kind, ("application/octet-stream", ""))[0]
    return FileResponse(path, media_type=media, filename=row.filename)


@router.delete("/exports/{export_id}")
def delete_export(export_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    row = storage.get_export(export_id, owner_id=owner)
    if row is None:
        raise HTTPException(404, "Export not found.")
    path = Path(row.path)
    if not path.is_absolute():
        path = EXPORT_DIR.parent / path
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    from ..db import SessionLocal
    from ..models import ExportRow
    session = SessionLocal()
    try:
        session.delete(session.get(ExportRow, export_id))
        session.commit()
    finally:
        session.close()
    return {"deleted": True}
