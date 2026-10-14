"""Uploads, vision analysis and honest redesign support."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..auth import current_owner
from ..config import settings
from ..services import images as image_service
from ..services import storage
from ..services.ai.base import AIError
from ..services.ai.features import before_after
from ..services.ai.registry import get_image_provider, get_vision
from ..services.events import done, failed, progress
from ..schemas import utcnow


def utcnow_iso() -> str:
    return utcnow().isoformat()

router = APIRouter(prefix="/api", tags=["images"])

MAX_FILES = 8


class ImageMetaBody(BaseModel):
    room_type: Optional[str] = None
    goals: Optional[List[str]] = None
    note: Optional[str] = None


class RedesignBody(BaseModel):
    prompt: str = ""
    style: str = ""


def _owner_project(project_id: str, owner: str):
    record = storage.get_project(project_id, owner_id=owner)
    if record is None:
        raise HTTPException(404, "That project does not exist (or belongs to another account).")
    return record


@router.get("/images/options")
def options() -> Dict[str, Any]:
    return {
        "room_types": image_service.room_type_options(),
        "goals": image_service.goal_options(),
        "privacy": image_service.privacy_statement(),
        "limits": {"max_upload_mb": settings.max_upload_mb,
                   "allowed_types": sorted(settings.allowed_types),
                   "max_files_per_upload": MAX_FILES},
    }


@router.post("/projects/{project_id}/images")
async def upload(project_id: str, files: List[UploadFile] = File(...),
                 room_type: str = Form("other"),
                 goals: str = Form(""),
                 note: str = Form(""),
                 owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _owner_project(project_id, owner)
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Up to {MAX_FILES} images per upload.")
    goal_list = [g for g in goals.split(",") if g.strip()]
    stored: List[Dict[str, Any]] = []
    errors: List[str] = []
    for upload in files:
        content = await upload.read()
        try:
            image = image_service.store_image(project_id, upload.filename or "image.jpg",
                                              content, room_type=room_type, goals=goal_list,
                                              note=note)
        except image_service.UploadError as exc:
            errors.append(f"{upload.filename}: {exc}")
            continue
        storage.add_image(project_id, image.ref, image.path, image.thumb_path,
                          size_bytes=image.size_bytes, width=image.width, height=image.height,
                          mime_type=image.mime_type, owner_id=owner)
        stored.append({"id": image.ref.id, "filename": image.ref.filename,
                       "url": image.ref.url, "thumb": f"/api/images/{image.ref.id}/thumb",
                       "width": image.width, "height": image.height,
                       "size_bytes": image.size_bytes, "room_type": image.ref.room_type,
                       "goals": image.ref.goals})
    if not stored and errors:
        raise HTTPException(400, "No image was accepted: " + "; ".join(errors))
    return {"images": stored, "errors": errors,
            "note": "Images stay on this server. They are only sent to an AI provider if you "
                    "press Analyse or Redesign, and only after you see which provider that is."}


@router.get("/projects/{project_id}/images")
def list_images(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _owner_project(project_id, owner)
    return {"images": storage.list_images(project_id, owner_id=owner)}


@router.patch("/projects/{project_id}/images/{image_id}")
def update_image(project_id: str, image_id: str, body: ImageMetaBody,
                 owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _owner_project(project_id, owner)
    ok = storage.update_image(project_id, image_id, room_type=body.room_type,
                              goals=body.goals, note=body.note, owner_id=owner)
    if not ok:
        raise HTTPException(404, "Image not found.")
    return {"updated": True}


@router.delete("/projects/{project_id}/images/{image_id}")
def delete_image(project_id: str, image_id: str,
                 owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _owner_project(project_id, owner)
    ok = storage.delete_image(project_id, image_id, owner_id=owner)
    if not ok:
        raise HTTPException(404, "Image not found.")
    return {"deleted": True}


def _image_paths(image_id: str, owner: Optional[str]) -> Dict[str, Any]:
    row = storage.get_image_row(image_id, owner_id=owner)
    if row is None:
        raise HTTPException(404, "Image not found.")
    file_path = Path(row.path)
    analysis_rel = str(file_path.parent / f"{row.id}_analysis.jpg")
    thumb_rel = row.thumb_path or row.path
    return {"row": row,
            "file": image_service.absolute(row.path),
            "thumb": image_service.absolute(thumb_rel),
            "analysis": image_service.absolute(analysis_rel)}


@router.get("/images/{image_id}/file")
def image_file(image_id: str, owner: str = Depends(current_owner)):
    paths = _image_paths(image_id, owner)
    return FileResponse(paths["file"], media_type=row_mime(paths["row"]))


@router.get("/images/{image_id}/thumb")
def image_thumb(image_id: str, owner: str = Depends(current_owner)):
    paths = _image_paths(image_id, owner)
    target = paths["thumb"] if paths["thumb"].exists() else paths["file"]
    return FileResponse(target, media_type="image/jpeg")


@router.get("/images/{image_id}/analysis")
def image_analysis(image_id: str, owner: str = Depends(current_owner)):
    paths = _image_paths(image_id, owner)
    target = paths["analysis"] if paths["analysis"].exists() else paths["file"]
    return FileResponse(target, media_type="image/jpeg")


def row_mime(row: Any) -> str:
    return getattr(row, "mime_type", "") or "image/jpeg"


@router.post("/projects/{project_id}/images/{image_id}/analyse")
async def analyse(project_id: str, image_id: str,
                  owner: str = Depends(current_owner)) -> Dict[str, Any]:
    record = _owner_project(project_id, owner)
    row = storage.get_image_row(image_id, owner_id=owner)
    if row is None:
        raise HTTPException(404, "Image not found.")
    paths = _image_paths(image_id, owner)
    adapter, note = get_vision()
    progress(project_id, "analyse", 25, f"Looking at {row.filename} with {adapter.name}")
    try:
        analysis = await adapter.analyse(path=str(paths["analysis"]), room_type=row.room_type,
                                         goals=row.goals or [], note=row.note or "",
                                         mime_type=row_mime(row))
    except AIError as exc:
        failed(project_id, "analyse", str(exc))
        raise HTTPException(503 if exc.setup_required else 502,
                           {"message": str(exc), "setup_required": exc.setup_required,
                            "provider": exc.provider})
    storage.update_image(project_id, image_id, owner_id=owner, analysis=analysis)
    done(project_id, "analyse", "Analysis ready")
    payload = {"analysis": analysis, "adapter": adapter.name}
    if note:
        payload["provider_note"] = note
    return payload


@router.post("/projects/{project_id}/images/{image_id}/redesign")
async def redesign(project_id: str, image_id: str, body: RedesignBody,
                   owner: str = Depends(current_owner)) -> Dict[str, Any]:
    record = _owner_project(project_id, owner)
    row = storage.get_image_row(image_id, owner_id=owner)
    if row is None:
        raise HTTPException(404, "Image not found.")
    adapter, note = get_image_provider()
    progress(project_id, "redesign", 20, f"Asking {adapter.name} for a redesign concept")
    try:
        result = await adapter.generate(
            prompt=body.prompt or f"Redesign this {row.room_type.replace('_', ' ')} in a "
                                  f"{body.style or record.data.requirements.style.replace('_', ' ')} "
                                  f"style while keeping walls, doors and windows where they are",
            style=body.style or record.data.requirements.style,
            reference_paths=[str(_image_paths(image_id, owner)["analysis"])])
    except AIError as exc:
        failed(project_id, "redesign", str(exc))
        raise HTTPException(503 if exc.setup_required else 502,
                           {"message": str(exc), "setup_required": exc.setup_required,
                            "provider": exc.provider})
    payload: Dict[str, Any] = {"provider": result.get("provider", adapter.name),
                               "notice": result.get("notice", "")}
    if result.get("b64"):
        import base64
        blob = base64.b64decode(result["b64"])
        relative = image_service.save_generated(project_id, "redesign", blob)
        name = relative.split("/")[-1]
        entry = {"name": name, "url": f"/api/projects/{project_id}/generated/{name}",
                 "prompt": result.get("prompt", body.prompt), "provider": payload["provider"],
                 "at": utcnow_iso()}
        storage.update_image(project_id, image_id, owner_id=owner,
                             generated=(row.generated or []) + [entry])
        payload.update({"image_url": entry["url"], "entry": entry})
    elif result.get("url"):
        payload.update({"image_url": result["url"],
                        "warning": "The provider returned a remote URL; it may expire."})
    else:
        raise HTTPException(502, "The provider returned no image.")
    done(project_id, "redesign", "Redesign image ready")
    if note:
        payload["provider_note"] = note
    payload["honesty"] = ("AI-generated concept imagery. It is a mood reference, not a "
                          "measurement and not a construction document.")
    return payload


@router.get("/projects/{project_id}/generated/{filename}")
def generated_file(project_id: str, filename: str, owner: str = Depends(current_owner)):
    _owner_project(project_id, owner)
    safe = "".join(c for c in filename if c.isalnum() or c in "-._")
    path = image_service.absolute(str(Path("uploads") / project_id / "generated" / safe))
    if not path.exists():
        raise HTTPException(404, "Generated image not found.")
    return FileResponse(path, media_type="image/png")




@router.get("/projects/{project_id}/images/{image_id}/before-after")
def before_after_view(project_id: str, image_id: str,
                      owner: str = Depends(current_owner)) -> Dict[str, Any]:
    record = _owner_project(project_id, owner)
    row = storage.get_image_row(image_id, owner_id=owner)
    if row is None:
        raise HTTPException(404, "Image not found.")
    analysis = row.analysis or {}
    result = before_after(record.data, analysis)
    result["room_type"] = row.room_type
    result["honesty"] = ("The 'after' side is a structured proposal derived from your project "
                         "materials and the pixel facts above. Exact dimensions cannot be "
                         "recovered from a photograph.")
    return result
