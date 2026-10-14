"""AI provider configuration. Keys are stored server-side only (chmod 600 file);
the frontend only ever sees *whether* something is configured plus setup help."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_owner
from ..config import (get_setting, load_runtime_settings, provider_status,
                      save_runtime_settings)
from ..services.ai.base import AIError
from ..services.ai.registry import get_llm, get_vision

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETUP_HELP: Dict[str, Dict[str, Any]] = {
    "openai": {
        "label": "OpenAI (ChatGPT)",
        "setup": [
            "Create an account at platform.openai.com and open API keys.",
            "Create a key with access to the chat model (and images if you want redesigns).",
            "Paste the key here. It is stored only in this server's settings file.",
        ],
        "fields": ["openai_api_key", "openai_base_url", "openai_chat_model",
                   "openai_vision_model", "openai_image_model"],
        "privacy": "Prompts and (only when you ask) images are sent to OpenAI's API.",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "setup": [
            "Create an account at console.anthropic.com and generate an API key.",
            "Paste the key here; text assistance will use Claude.",
        ],
        "fields": ["anthropic_api_key", "anthropic_model"],
        "privacy": "Prompts are sent to Anthropic's API when you use the assistant.",
    },
    "gemini": {
        "label": "Google Gemini",
        "setup": [
            "Get an API key from Google AI Studio (aistudio.google.com).",
            "Paste it here for chat, vision and image generation.",
        ],
        "fields": ["gemini_api_key", "gemini_model", "gemini_image_model"],
        "privacy": "Prompts and (only when you ask) images are sent to Google's API.",
    },
    "stability": {
        "label": "Stability AI (images only)",
        "setup": ["Create a key at platform.stability.ai and paste it here."],
        "fields": ["stability_api_key"],
        "privacy": "Prompts and reference images are sent to Stability's API.",
    },
    "local": {
        "label": "Built-in local engine (no key)",
        "setup": ["Nothing to configure. Explanations, vision notes and command parsing all "
                  "run on this server with deterministic logic."],
        "fields": [],
        "privacy": "Nothing leaves this machine.",
    },
}


@router.get("/providers")
def providers() -> Dict[str, Any]:
    return {"status": provider_status(), "help": SETUP_HELP,
            "runtime": {k: _mask(k, v) for k, v in load_runtime_settings().items()}}


@router.get("")
def get_settings_view() -> Dict[str, Any]:
    return {
        "llm_provider": get_setting("llm_provider"),
        "vision_provider": get_setting("vision_provider"),
        "image_provider": get_setting("image_provider"),
        "openai_base_url": get_setting("openai_base_url"),
        "openai_chat_model": get_setting("openai_chat_model"),
        "openai_vision_model": get_setting("openai_vision_model"),
        "openai_image_model": get_setting("openai_image_model"),
        "anthropic_model": get_setting("anthropic_model"),
        "gemini_model": get_setting("gemini_model"),
        "gemini_image_model": get_setting("gemini_image_model"),
        "status": provider_status(),
        "help": SETUP_HELP,
    }


class SettingsBody(BaseModel):
    values: Dict[str, Any]


@router.put("")
def put_settings(body: SettingsBody, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    allowed = {"llm_provider", "vision_provider", "image_provider", "openai_api_key",
               "openai_base_url", "openai_chat_model", "openai_vision_model",
               "openai_image_model", "anthropic_api_key", "anthropic_model",
               "gemini_api_key", "gemini_model", "gemini_image_model", "stability_api_key"}
    patch = {k: v for k, v in body.values.items() if k in allowed}
    if not patch:
        raise HTTPException(400, "No recognised settings were provided.")
    save_runtime_settings(patch)
    return {"saved": True, "status": provider_status(),
            "note": "Keys are stored only in this server's settings file (permissions 600) and "
                    "are never sent to the browser."}


@router.post("/test")
async def test_provider(role: str = "llm", owner: str = Depends(current_owner)) -> Dict[str, Any]:
    try:
        if role == "vision":
            adapter, note = get_vision()
            ok = await adapter.available()
        else:
            adapter, note = get_llm()
            ok = await adapter.available()
    except AIError as exc:
        return {"ok": False, "provider": exc.provider, "message": str(exc),
                "setup_required": exc.setup_required}
    return {"ok": bool(ok), "provider": adapter.name,
            "message": ("Connected." if ok else "Not reachable right now."),
            "setup_required": False,
            "note": note or ""}


def _mask(key: str, value: Any) -> Any:
    if key.endswith("_api_key") and value:
        return f"{str(value)[:4]}...{str(value)[-2:]}" if len(str(value)) > 8 else "set"
    return value
