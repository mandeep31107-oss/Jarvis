"""Image upload handling: validation, storage, thumbnails and analysis.

Uploads are always optional. Files are validated by declared type *and* by their
actual bytes, size-limited, stored under the server data directory, thumbnailed for
the UI, and stripped of EXIF orientation so previews match what the analysis sees.
Nothing is sent to a third party unless the user picks a hosted vision provider.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageOps

from ..config import get_settings
from ..schemas import ImageRef, new_id

MAGIC: Dict[str, bytes] = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/webp": b"RIFF",
}
EXTENSION = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
THUMB_SIZE = (640, 640)
ANALYSIS_SIZE = (1280, 1280)


class UploadError(ValueError):
    """Rejected upload. The message is shown to the user verbatim."""


@dataclass
class StoredImage:
    ref: ImageRef
    path: str
    thumb_path: str
    analysis_path: str
    size_bytes: int
    width: int
    height: int
    mime_type: str


def sniff_mime(header: bytes) -> Optional[str]:
    if header.startswith(MAGIC["image/jpeg"]):
        return "image/jpeg"
    if header.startswith(MAGIC["image/png"]):
        return "image/png"
    if header[:4] == MAGIC["image/webp"] and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_upload(filename: str, content: bytes) -> str:
    settings = get_settings()
    if not content:
        raise UploadError("The file is empty.")
    if len(content) > settings.max_upload_bytes:
        raise UploadError(
            f"'{filename}' is {len(content) / 1024 / 1024:.1f} MB. The limit is "
            f"{settings.max_upload_mb} MB per image. Export a smaller JPEG/PNG or crop the photo.")
    mime = sniff_mime(content[:16])
    if mime is None:
        raise UploadError(
            f"'{filename}' is not a JPEG, PNG or WebP image. Upload a photo or scan of a plan in "
            f"one of those formats.")
    if mime not in settings.allowed_types:
        raise UploadError(f"{mime} files are not accepted. Allowed: "
                          f"{', '.join(sorted(settings.allowed_types))}.")
    try:
        with Image.open(io.BytesIO(content)) as probe:
            probe.verify()
    except Exception as exc:
        raise UploadError(f"'{filename}' could not be decoded as an image ({exc}).") from exc
    return mime


def store_image(project_id: str, filename: str, content: str | bytes,
                room_type: str = "other", goals: Optional[List[str]] = None,
                note: str = "") -> StoredImage:
    settings = get_settings()
    if isinstance(content, str):
        content = content.encode("latin-1", "ignore")
    mime = validate_upload(filename, content)
    image_id = new_id("img")
    folder = settings.upload_dir / project_id
    folder.mkdir(parents=True, exist_ok=True)
    ext = EXTENSION.get(mime, ".jpg")
    safe_name = "".join(c for c in Path(filename).stem if c.isalnum() or c in "-_ ")[:60] or "image"
    rel_dir = Path("uploads") / project_id
    path = folder / f"{image_id}_{safe_name}{ext}"
    thumb_path = folder / f"{image_id}_thumb.jpg"
    analysis_path = folder / f"{image_id}_analysis.jpg"

    with Image.open(io.BytesIO(content)) as img:
        img = ImageOps.exif_transpose(img)          # bake in the orientation
        width, height = img.size
        rgb = img.convert("RGB")
        rgb.save(path, quality=92)
        thumb = rgb.copy()
        thumb.thumbnail(THUMB_SIZE)
        thumb.save(thumb_path, quality=82, optimize=True)
        prepared = rgb.copy()
        prepared.thumbnail(ANALYSIS_SIZE)
        prepared.save(analysis_path, quality=85, optimize=True)

    ref = ImageRef(id=image_id, filename=Path(filename).name,
                   url=f"/api/images/{image_id}/file", room_type=room_type or "other",
                   goals=list(goals or []), note=note or "", status="uploaded")
    return StoredImage(ref=ref, path=str(path.relative_to(settings.data_dir)),
                       thumb_path=str(thumb_path.relative_to(settings.data_dir)),
                       analysis_path=str(analysis_path.relative_to(settings.data_dir)),
                       size_bytes=len(content), width=width, height=height, mime_type=mime)


def absolute(relative_path: str) -> Path:
    path = Path(relative_path)
    return path if path.is_absolute() else get_settings().data_dir / path


def save_generated(project_id: str, kind: str, payload: bytes, ext: str = ".png") -> str:
    """Store a generated/redesign image and return its data-relative path."""
    settings = get_settings()
    folder = settings.upload_dir / project_id / "generated"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{new_id('gen')}_{kind}{ext}"
    (folder / name).write_bytes(payload)
    return str(Path("uploads") / project_id / "generated" / name)


def delete_project_files(project_id: str) -> int:
    settings = get_settings()
    folder = settings.upload_dir / project_id
    if not folder.exists():
        return 0
    count = 0
    for path in folder.rglob("*"):
        if path.is_file():
            try:
                os.unlink(path)
                count += 1
            except OSError:
                pass
    return count


def room_type_options() -> List[Dict[str, str]]:
    return [
        {"key": "living", "label": "Living room"},
        {"key": "bedroom", "label": "Bedroom"},
        {"key": "kitchen", "label": "Kitchen"},
        {"key": "bathroom", "label": "Bathroom"},
        {"key": "dining", "label": "Dining"},
        {"key": "study", "label": "Study / office"},
        {"key": "balcony", "label": "Balcony"},
        {"key": "terrace", "label": "Terrace"},
        {"key": "facade", "label": "Facade / exterior"},
        {"key": "plan", "label": "Floor plan drawing"},
        {"key": "site", "label": "Site / plot photo"},
        {"key": "other", "label": "Other"},
    ]


def goal_options() -> List[Dict[str, str]]:
    return [
        {"key": "more_light", "label": "More natural light"},
        {"key": "more_storage", "label": "More storage"},
        {"key": "modernise", "label": "Modernise the look"},
        {"key": "better_layout", "label": "Better furniture layout"},
        {"key": "ventilation", "label": "Better ventilation"},
        {"key": "budget_refresh", "label": "Refresh on a budget"},
        {"key": "keep_structure", "label": "Keep the structure as it is"},
        {"key": "accessibility", "label": "Make it step-free / accessible"},
        {"key": "rental", "label": "Prepare it for rent"},
    ]


def privacy_statement() -> Dict[str, Any]:
    return {
        "storage": "Images are stored on this server only, under the project's upload folder. "
                   "They are never published and never used to train a model.",
        "third_party": "An image leaves this server only when you ask for analysis or a redesign "
                       "with a hosted provider (OpenAI, Anthropic, Google). The built-in provider "
                       "analyses images locally and sends nothing anywhere.",
        "delete": "Deleting an image removes the file and its thumbnail from the server. Deleting a "
                  "project removes all of its images.",
        "limits": f"Maximum {get_settings().max_upload_mb} MB per file; JPEG, PNG and WebP only.",
    }
