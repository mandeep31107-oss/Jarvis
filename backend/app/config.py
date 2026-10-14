"""Application configuration.

All third-party AI credentials live here (backend only). They are never serialised
to the frontend -- the settings API only reports *whether* a provider is configured.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "ai_settings.json"
_lock = threading.RLock()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", env_file=".env", extra="ignore")

    app_name: str = "AI Home Designer"
    version: str = "0.1.0"
    environment: str = "development"

    # Storage. SQLite by default so the app runs as a single-user local install;
    # point JARVIS_DATABASE_URL at PostgreSQL for multi-user deployments.
    database_url: str = f"sqlite:///{DATA_DIR / 'app.db'}"

    # Auth. Disabled by default for local single-user installs. When enabled the
    # API requires a bearer token and enforces per-owner access checks.
    auth_enabled: bool = False
    jwt_secret: str = "local-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_ttl_hours: int = 24 * 30

    # Upload limits
    max_upload_mb: int = 12
    allowed_image_types: str = "image/jpeg,image/jpg,image/png,image/webp"

    # CORS (comma separated). Vite dev server + sandbox preview hosts.
    cors_origins: str = "*"

    # AI provider defaults (can be overridden at runtime through the settings UI).
    llm_provider: str = "local"      # local | openai | anthropic | gemini
    vision_provider: str = "local"   # local | openai | gemini | anthropic
    image_provider: str = "none"     # none | openai | gemini | stability

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-4o-mini"
    openai_vision_model: str = "gpt-4o-mini"
    openai_image_model: str = "gpt-image-1"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-latest"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"
    gemini_image_model: str = "gemini-2.0-flash-exp-image-generation"

    stability_api_key: str = ""

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def allowed_types(self) -> set[str]:
        return {t.strip().lower() for t in self.allowed_image_types.split(",") if t.strip()}

    @property
    def data_dir(self) -> Path:
        return DATA_DIR

    @property
    def upload_dir(self) -> Path:
        return UPLOAD_DIR

    @property
    def export_dir(self) -> Path:
        return EXPORT_DIR


settings = Settings()


def get_settings() -> Settings:
    """Single settings object for services (paths + limits + provider defaults)."""
    return settings


# ---------------------------------------------------------------------------
# Runtime settings store (persisted to data/ai_settings.json on the server)
# ---------------------------------------------------------------------------

_RUNTIME_KEYS = {
    "llm_provider",
    "vision_provider",
    "image_provider",
    "openai_api_key",
    "openai_base_url",
    "openai_chat_model",
    "openai_vision_model",
    "openai_image_model",
    "anthropic_api_key",
    "anthropic_model",
    "gemini_api_key",
    "gemini_model",
    "gemini_image_model",
    "stability_api_key",
}


def load_runtime_settings() -> Dict[str, Any]:
    """Read persisted provider settings (file is server-side only)."""
    with _lock:
        if not SETTINGS_FILE.exists():
            return {}
        try:
            raw = json.loads(SETTINGS_FILE.read_text() or "{}")
        except json.JSONDecodeError:
            return {}
        return {k: v for k, v in raw.items() if k in _RUNTIME_KEYS}


def save_runtime_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        current = load_runtime_settings()
        for key, value in patch.items():
            if key not in _RUNTIME_KEYS:
                continue
            if value is None or value == "":
                current.pop(key, None)
            else:
                current[key] = value
        SETTINGS_FILE.write_text(json.dumps(current, indent=2))
        SETTINGS_FILE.chmod(0o600)
        return current


def get_setting(name: str, default: Any = None) -> Any:
    """Runtime value with env fallback."""
    runtime = load_runtime_settings()
    if name in runtime and runtime[name] not in ("", None):
        return runtime[name]
    return getattr(settings, name, default)


def provider_status() -> Dict[str, Any]:
    """Safe, credential-free description of AI capability for the frontend."""
    llm = str(get_setting("llm_provider", "local"))
    vision = str(get_setting("vision_provider", "local"))
    image = str(get_setting("image_provider", "none"))

    def key_present(provider: str) -> bool:
        return bool(get_setting(f"{provider}_api_key", ""))

    return {
        "llm": {
            "provider": llm,
            "configured": llm == "local" or key_present(llm),
            "model": get_setting(f"{llm}_chat_model", None) or get_setting(f"{llm}_model", None),
            "requires_key": llm != "local",
        },
        "vision": {
            "provider": vision,
            "configured": vision == "local" or key_present(vision),
            "model": get_setting(f"{vision}_vision_model", None) or get_setting(f"{vision}_model", None),
            "requires_key": vision != "local",
        },
        "image_generation": {
            "provider": image,
            "configured": image != "none" and key_present(image),
            "model": get_setting(f"{image}_image_model", None),
            "requires_key": True,
        },
        "geometry_engine": {
            "provider": "built-in",
            "configured": True,
            "model": "deterministic layout + geometry engine",
            "requires_key": False,
        },
    }
