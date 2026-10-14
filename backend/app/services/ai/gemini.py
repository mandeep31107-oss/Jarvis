"""Google Gemini adapter (text, vision and optional image generation)."""

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
API = "https://generativelanguage.googleapis.com/v1beta"


def _key() -> str:
    return str(get_setting("gemini_api_key", "") or "").strip()


async def _generate(payload: Dict[str, Any], model: str) -> Dict[str, Any]:
    if not _key():
        raise AIError("No Google Gemini API key is configured. Add one in Settings -> AI "
                      "providers, or use the built-in local assistant.", provider="gemini",
                      setup_required=True)
    url = f"{API}/models/{model}:generateContent?key={_key()}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise AIError(f"Could not reach Google Gemini: {exc}", provider="gemini", retryable=True)
    if response.status_code >= 400:
        try:
            detail = response.json().get("error", {}).get("message", response.text[:300])
        except Exception:
            detail = response.text[:300]
        raise AIError(f"Gemini could not complete the request (HTTP {response.status_code}): "
                      f"{detail}", provider="gemini",
                      setup_required=response.status_code in (400, 401, 403),
                      retryable=response.status_code in (429, 500, 502, 503))
    return response.json()


def _text(data: Dict[str, Any]) -> str:
    parts = []
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            if part.get("text"):
                parts.append(part["text"])
    return "".join(parts).strip()


class GeminiLLM(LLMAdapter):
    name = "gemini"

    def __init__(self) -> None:
        self.info = ProviderInfo(
            key="gemini", label="Google Gemini",
            capabilities=["explain_design", "answer_questions", "style_advice",
                          "budget_commentary"],
            requires_key=True, configured=bool(_key()),
            model=str(get_setting("gemini_model", "gemini-1.5-flash")),
            setup=SETUP_INSTRUCTIONS["gemini"], privacy=PRIVACY_NOTES["gemini"])

    async def available(self) -> bool:
        return bool(_key())

    async def complete(self, *, task: str, facts: Dict[str, Any], question: str = "",
                       system: str = "", json_mode: bool = False,
                       max_tokens: int = 900) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": system or SYSTEM_PROMPT}]},
            "contents": [{"role": "user",
                          "parts": [{"text": _prompt_for(task, facts, question, json_mode)}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        text = _text(await _generate(payload, str(get_setting("gemini_model", "gemini-1.5-flash"))))
        return text if json_mode else text + "\n\n" + DISCLAIMER


class GeminiVision(VisionAdapter):
    name = "gemini"

    def __init__(self) -> None:
        self.info = ProviderInfo(
            key="gemini", label="Google Gemini vision",
            capabilities=["describe_photo", "detect_openings", "style_assessment"],
            requires_key=True, configured=bool(_key()),
            model=str(get_setting("gemini_model", "gemini-1.5-flash")),
            setup=SETUP_INSTRUCTIONS["gemini"], privacy=PRIVACY_NOTES["gemini"])

    async def available(self) -> bool:
        return bool(_key())

    async def analyse(self, *, path: str, room_type: str, goals: List[str], note: str = "",
                      mime_type: str = "image/jpeg") -> Dict[str, Any]:
        if not os.path.exists(path):
            raise AIError("The uploaded file is no longer on the server.", provider="gemini")
        mime = mime_type or mimetypes.guess_type(path)[0] or "image/jpeg"
        with open(path, "rb") as handle:
            b64 = base64.b64encode(handle.read()).decode()
        prompt = (
            f"Tagged space: {room_type.replace('_', ' ') or 'unspecified'}. Goals: "
            f"{', '.join(goals) or 'not stated'}. Note: {note or 'none'}.\n\n"
            "Describe what is visible: finishes, floor material, doors/windows (position and "
            "proportion only), furniture, lighting, layout. Separate cosmetic from structural "
            "changes. State that exact dimensions cannot be recovered from a photograph. Reply as "
            "JSON: summary, findings[{kind,label,value,detail}], suggestions[], "
            "structural_elements[], cosmetic_options[], uncertainty."
        )
        payload = {
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": mime, "data": b64}},
                {"text": prompt},
            ]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200,
                                 "responseMimeType": "application/json"},
        }
        parsed = _safe_json(_text(await _generate(payload, str(
            get_setting("gemini_model", "gemini-1.5-flash")))))
        parsed["provider"] = "gemini"
        parsed["model"] = str(get_setting("gemini_model", ""))
        parsed.setdefault("findings", [])
        parsed.setdefault("suggestions", [])
        parsed["disclaimer"] = "Vision analysis is an interpretation of a photo, not a survey."
        parsed["dimension_warning"] = "Exact dimensions cannot be recovered from a photograph."
        return parsed


class GeminiImage(ImageAdapter):
    name = "gemini"

    def __init__(self) -> None:
        model = str(get_setting("gemini_image_model", "") or "")
        self.info = ProviderInfo(
            key="gemini", label="Google Gemini image generation",
            capabilities=["mood_image", "redesign_image"], requires_key=True,
            configured=bool(_key()) and bool(model), model=model,
            setup=SETUP_INSTRUCTIONS["gemini"], privacy=PRIVACY_NOTES["gemini"])

    async def available(self) -> bool:
        return bool(_key()) and bool(str(get_setting("gemini_image_model", "") or ""))

    async def generate(self, *, prompt: str, style: str,
                       reference_paths: Optional[List[str]] = None,
                       size: str = "1024x1024") -> Dict[str, Any]:
        model = str(get_setting("gemini_image_model", "") or "")
        if not model:
            raise AIError("No Gemini image model is configured. Set one in Settings -> AI "
                          "providers, or use the interactive 3D viewer instead.",
                          provider="gemini", setup_required=True)
        parts: List[Dict[str, Any]] = [{"text": f"{prompt}. Style: {style}. Photorealistic "
                                                f"interior/exterior visualisation."}]
        for ref in (reference_paths or [])[:2]:
            if os.path.exists(ref):
                mime = mimetypes.guess_type(ref)[0] or "image/jpeg"
                with open(ref, "rb") as handle:
                    parts.insert(0, {"inline_data": {"mime_type": mime,
                                                     "data": base64.b64encode(handle.read()).decode()}})
        payload = {"contents": [{"role": "user", "parts": parts}],
                   "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096}}
        data = await _generate(payload, model)
        image_b64 = None
        for candidate in data.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                inline = part.get("inline_data") or part.get("inlineData")
                if inline and inline.get("data"):
                    image_b64 = inline["data"]
        if not image_b64:
            raise AIError("The image model returned text but no image. Try a different prompt or "
                          "model.", provider="gemini", retryable=True)
        return {"provider": "gemini", "model": model, "b64": image_b64,
                "prompt": prompt,
                "notice": ("AI-generated illustration for inspiration only. It is not your model "
                           "geometry and is not a drawing to build from.")}
