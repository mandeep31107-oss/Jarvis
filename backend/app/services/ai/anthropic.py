"""Anthropic (Claude) adapter."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from typing import Any, Dict, List, Optional

import httpx

from ...config import get_setting
from .base import (DISCLAIMER, AIError, ImageAdapter, LLMAdapter, PRIVACY_NOTES,
                   SETUP_INSTRUCTIONS, ProviderInfo, VisionAdapter)
from .openai_compat import SYSTEM_PROMPT, _prompt_for, _safe_json

TIMEOUT = 90.0
API = "https://api.anthropic.com/v1"


def _key() -> str:
    return str(get_setting("anthropic_api_key", "") or "").strip()


def _headers() -> Dict[str, str]:
    return {"x-api-key": _key(), "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"}


async def _messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not _key():
        raise AIError("No Anthropic API key is configured. Add one in Settings -> AI providers, or "
                      "use the built-in local assistant.", provider="anthropic",
                      setup_required=True)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(f"{API}/messages", headers=_headers(), json=payload)
    except httpx.HTTPError as exc:
        raise AIError(f"Could not reach Anthropic: {exc}", provider="anthropic", retryable=True)
    if response.status_code >= 400:
        try:
            detail = response.json().get("error", {}).get("message", response.text[:300])
        except Exception:
            detail = response.text[:300]
        raise AIError(f"Anthropic could not complete the request (HTTP {response.status_code}): "
                      f"{detail}", provider="anthropic",
                      setup_required=response.status_code in (401, 403),
                      retryable=response.status_code in (429, 500, 502, 503, 529))
    return response.json()


def _text(data: Dict[str, Any]) -> str:
    blocks = data.get("content") or []
    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict)).strip()


class AnthropicLLM(LLMAdapter):
    name = "anthropic"

    def __init__(self) -> None:
        self.info = ProviderInfo(
            key="anthropic", label="Anthropic Claude",
            capabilities=["explain_design", "answer_questions", "style_advice",
                          "budget_commentary"],
            requires_key=True, configured=bool(_key()),
            model=str(get_setting("anthropic_model", "claude-3-5-sonnet-latest")),
            setup=SETUP_INSTRUCTIONS["anthropic"], privacy=PRIVACY_NOTES["anthropic"])

    async def available(self) -> bool:
        return bool(_key())

    async def complete(self, *, task: str, facts: Dict[str, Any], question: str = "",
                       system: str = "", json_mode: bool = False,
                       max_tokens: int = 900) -> str:
        payload = {
            "model": str(get_setting("anthropic_model", "claude-3-5-sonnet-latest")),
            "max_tokens": max_tokens, "temperature": 0.4,
            "system": system or SYSTEM_PROMPT,
            "messages": [{"role": "user",
                          "content": _prompt_for(task, facts, question, json_mode)}],
        }
        text = _text(await _messages(payload))
        return text if json_mode else text + "\n\n" + DISCLAIMER


class AnthropicVision(VisionAdapter):
    name = "anthropic"

    def __init__(self) -> None:
        self.info = ProviderInfo(
            key="anthropic", label="Anthropic Claude vision",
            capabilities=["describe_photo", "detect_openings", "style_assessment"],
            requires_key=True, configured=bool(_key()),
            model=str(get_setting("anthropic_model", "claude-3-5-sonnet-latest")),
            setup=SETUP_INSTRUCTIONS["anthropic"], privacy=PRIVACY_NOTES["anthropic"])

    async def available(self) -> bool:
        return bool(_key())

    async def analyse(self, *, path: str, room_type: str, goals: List[str], note: str = "",
                      mime_type: str = "image/jpeg") -> Dict[str, Any]:
        if not os.path.exists(path):
            raise AIError("The uploaded file is no longer on the server.", provider="anthropic")
        mime = mime_type or mimetypes.guess_type(path)[0] or "image/jpeg"
        if mime == "image/webp":
            mime = "image/webp"
        with open(path, "rb") as handle:
            b64 = base64.b64encode(handle.read()).decode()
        prompt = (
            f"Tagged space: {room_type.replace('_', ' ') or 'unspecified'}. Goals: "
            f"{', '.join(goals) or 'not stated'}. Note: {note or 'none'}.\n\n"
            "Describe what is visible: finishes, floor material, doors/windows (position and "
            "proportion only), furniture, lighting, layout. Separate cosmetic changes from "
            "structural ones. State clearly that exact dimensions cannot be recovered from a "
            "photograph. Reply as JSON: summary, findings[{kind,label,value,detail}], "
            "suggestions[], structural_elements[], cosmetic_options[], uncertainty."
        )
        payload = {
            "model": str(get_setting("anthropic_model", "claude-3-5-sonnet-latest")),
            "max_tokens": 1200, "temperature": 0.3,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": prompt},
            ]}],
        }
        parsed = _safe_json(_text(await _messages(payload)))
        parsed["provider"] = "anthropic"
        parsed["model"] = str(get_setting("anthropic_model", ""))
        parsed.setdefault("findings", [])
        parsed.setdefault("suggestions", [])
        parsed["disclaimer"] = ("Vision analysis is an interpretation of a photo, not a survey.")
        parsed["dimension_warning"] = ("Exact dimensions cannot be recovered from a photograph.")
        return parsed


class AnthropicImage(ImageAdapter):
    name = "anthropic"

    def __init__(self) -> None:
        self.info = ProviderInfo(
            key="anthropic", label="Anthropic (no image generation)", capabilities=[],
            requires_key=True, configured=False, model="",
            setup=["Anthropic's API does not generate images. Choose OpenAI, Google Gemini or "
                   "Stability in Settings -> AI providers if you want generated mood images."],
            privacy=PRIVACY_NOTES["anthropic"])

    async def available(self) -> bool:
        return False

    async def generate(self, *, prompt: str, style: str,
                       reference_paths: Optional[List[str]] = None,
                       size: str = "1024x1024") -> Dict[str, Any]:
        raise AIError("The selected provider (Anthropic) cannot generate images. Choose OpenAI, "
                      "Google Gemini or Stability in Settings -> AI providers, or use the "
                      "interactive 3D viewer, which renders your real model.",
                      provider="anthropic", setup_required=True)
