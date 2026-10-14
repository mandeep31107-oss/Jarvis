"""Provider registry.

Resolves the configured adapters and, importantly, never silently pretends a
provider is connected: if the configured provider has no key, callers get the local
adapter *plus* a note explaining what is missing and where to fix it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ...config import get_setting, provider_status
from .anthropic import AnthropicImage, AnthropicLLM, AnthropicVision
from .base import (AIError, ImageAdapter, LLMAdapter, ProviderInfo, VisionAdapter)
from .gemini import GeminiImage, GeminiLLM, GeminiVision
from .local import LocalImage, LocalLLM, LocalVision
from .openai_compat import OpenAIImage, OpenAILLM, OpenAIVision

LLMS: Dict[str, Any] = {"local": LocalLLM, "openai": OpenAILLM, "anthropic": AnthropicLLM,
                        "gemini": GeminiLLM}
VISIONS: Dict[str, Any] = {"local": LocalVision, "openai": OpenAIVision,
                           "anthropic": AnthropicVision, "gemini": GeminiVision}
IMAGES: Dict[str, Any] = {"none": LocalImage, "openai": OpenAIImage, "gemini": GeminiImage,
                          "stability": LocalImage, "anthropic": AnthropicImage,
                          "local": LocalImage}


def _configured(provider: str) -> bool:
    if provider in ("local", "none"):
        return True
    return bool(str(get_setting(f"{provider}_api_key", "") or "").strip())


def get_llm() -> Tuple[LLMAdapter, Optional[str]]:
    """Return the adapter plus a user-visible note when a fallback happened."""
    key = str(get_setting("llm_provider", "local") or "local")
    if key not in LLMS:
        return LocalLLM(), (f"Unknown AI provider '{key}'. Using the built-in local assistant. "
                            f"Fix it in Settings -> AI providers.")
    if not _configured(key):
        return LocalLLM(), (f"The '{key}' provider has no API key yet, so the built-in local "
                            f"assistant answered instead. Add a key in Settings -> AI providers to "
                            f"enable it.")
    return LLMS[key](), None


def get_vision() -> Tuple[VisionAdapter, Optional[str]]:
    key = str(get_setting("vision_provider", "local") or "local")
    if key not in VISIONS:
        return LocalVision(), (f"Unknown vision provider '{key}'. Using built-in local image "
                               f"inspection.")
    if not _configured(key):
        return LocalVision(), (f"The '{key}' vision provider has no API key yet, so the image was "
                               f"inspected locally (palette, brightness, plan-vs-photo, line "
                               f"structure). Add a key for a full scene description.")
    return VISIONS[key](), None


def get_image_provider() -> Tuple[ImageAdapter, Optional[str]]:
    key = str(get_setting("image_provider", "none") or "none")
    adapter_cls = IMAGES.get(key, LocalImage)
    adapter = adapter_cls()
    if not _configured(key) and key not in ("none", "local"):
        return LocalImage(), (f"The '{key}' image provider has no API key, so image generation is "
                              f"unavailable. Add a key in Settings -> AI providers.")
    if key in ("none", "local"):
        return adapter, ("Image generation is switched off. Plans, 3D, budgets and exports all work "
                         "without it.")
    return adapter, None


async def require_image_provider() -> ImageAdapter:
    adapter, note = get_image_provider()
    if not await adapter.available():
        raise AIError(note or "Image generation is not configured.", provider=adapter.name,
                      setup_required=True)
    return adapter


def provider_infos() -> Dict[str, Any]:
    """Credential-free status for the Settings screen."""
    status = provider_status()
    llm, llm_note = get_llm()
    vision, vision_note = get_vision()
    image, image_note = get_image_provider()
    infos: List[Dict[str, Any]] = [llm.info.as_dict(), vision.info.as_dict(),
                                   image.info.as_dict()]
    return {
        "status": status,
        "providers": infos,
        "active": {"llm": llm.name, "vision": vision.name, "image": image.name},
        "notes": [n for n in (llm_note, vision_note, image_note) if n],
        "available_providers": {
            "llm": [{"key": k, "label": getattr(L, "name", k)} for k, L in LLMS.items()],
            "vision": [{"key": k} for k in VISIONS],
            "image": [{"key": k} for k in IMAGES],
        },
        "key_configured": {p: _configured(p) for p in
                           ("openai", "anthropic", "gemini", "stability")},
        "models": {
            "openai_chat_model": get_setting("openai_chat_model", ""),
            "openai_vision_model": get_setting("openai_vision_model", ""),
            "openai_image_model": get_setting("openai_image_model", ""),
            "openai_base_url": get_setting("openai_base_url", ""),
            "anthropic_model": get_setting("anthropic_model", ""),
            "gemini_model": get_setting("gemini_model", ""),
            "gemini_image_model": get_setting("gemini_image_model", ""),
        },
        "editable": ["llm_provider", "vision_provider", "image_provider", "openai_base_url",
                     "openai_chat_model", "openai_vision_model", "openai_image_model",
                     "anthropic_model", "gemini_model", "gemini_image_model"],
        "secrets": ["openai_api_key", "anthropic_api_key", "gemini_api_key", "stability_api_key"],
    }
