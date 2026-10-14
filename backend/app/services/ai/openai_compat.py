"""OpenAI-compatible provider (also works with OpenRouter / Azure-style gateways).

The key is read from server-side settings only. Failures are translated into
:class:`AIError` with a message the UI can show, including "needs a key" so the
configuration screen appears instead of a fake success.
"""

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

TIMEOUT = 90.0

SYSTEM_PROMPT = (
    "You are the design assistant inside a house-planning tool. You are given a JSON snapshot of "
    "the user's project (plot, rooms with dimensions in metres, openings, metrics, budget). Rules:\n"
    "1. Never invent geometry, dimensions or rooms that are not in the snapshot. You explain and "
    "advise; a deterministic engine generates the plan.\n"
    "2. Never claim a design is structurally safe, code compliant or ready to build. Say which "
    "professional must confirm it.\n"
    "3. Never invent current local prices. If the snapshot has no rate, say so and suggest the "
    "user enter one.\n"
    "4. Be concrete and brief. Use metric with imperial in brackets. Plain text, short bullet "
    "lists, no markdown tables.\n"
    "5. If information needed for a materially different answer is missing, ask one short "
    "clarifying question instead of assuming."
)


def _key() -> str:
    return str(get_setting("openai_api_key", "") or "").strip()


def _base_url() -> str:
    return str(get_setting("openai_base_url", "https://api.openai.com/v1")).rstrip("/")


class OpenAILLM(LLMAdapter):
    name = "openai"

    def __init__(self) -> None:
        configured = bool(_key())
        self.info = ProviderInfo(
            key="openai", label="OpenAI (or compatible)",
            capabilities=["explain_design", "answer_questions", "style_advice",
                          "budget_commentary", "command_parsing"],
            requires_key=True, configured=configured,
            model=str(get_setting("openai_chat_model", "gpt-4o-mini")),
            setup=SETUP_INSTRUCTIONS["openai"], privacy=PRIVACY_NOTES["openai"])

    async def available(self) -> bool:
        return bool(_key())

    async def complete(self, *, task: str, facts: Dict[str, Any], question: str = "",
                       system: str = "", json_mode: bool = False,
                       max_tokens: int = 900) -> str:
        if not _key():
            raise AIError("No OpenAI API key is configured. Add one in Settings -> AI providers, "
                          "or switch back to the built-in local assistant.",
                          provider="openai", setup_required=True)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system or SYSTEM_PROMPT},
            {"role": "user", "content": _prompt_for(task, facts, question, json_mode)},
        ]
        payload: Dict[str, Any] = {
            "model": str(get_setting("openai_chat_model", "gpt-4o-mini")),
            "messages": messages, "max_tokens": max_tokens, "temperature": 0.4,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = await _chat(payload)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError(f"Unexpected response shape from OpenAI: {exc}", provider="openai",
                          retryable=True)
        if json_mode:
            return text
        return text.strip() + "\n\n" + DISCLAIMER


class OpenAIVision(VisionAdapter):
    name = "openai"

    def __init__(self) -> None:
        configured = bool(_key())
        self.info = ProviderInfo(
            key="openai", label="OpenAI vision",
            capabilities=["describe_photo", "detect_openings", "style_assessment"],
            requires_key=True, configured=configured,
            model=str(get_setting("openai_vision_model", "gpt-4o-mini")),
            setup=SETUP_INSTRUCTIONS["openai"], privacy=PRIVACY_NOTES["openai"])

    async def available(self) -> bool:
        return bool(_key())

    async def analyse(self, *, path: str, room_type: str, goals: List[str], note: str = "",
                      mime_type: str = "image/jpeg") -> Dict[str, Any]:
        if not _key():
            raise AIError("No OpenAI API key is configured, so photos cannot be analysed by the "
                          "hosted vision model. The built-in local image inspection still works.",
                          provider="openai", setup_required=True)
        if not os.path.exists(path):
            raise AIError("The uploaded file is no longer on the server.", provider="openai")
        mime = mime_type or mimetypes.guess_type(path)[0] or "image/jpeg"
        with open(path, "rb") as handle:
            b64 = base64.b64encode(handle.read()).decode()
        prompt = (
            f"The user tagged this image as: {room_type.replace('_', ' ') or 'unspecified'}.\n"
            f"Their goals: {', '.join(goals) or 'not stated'}.\n"
            f"Their note: {note or 'none'}.\n\n"
            "Describe what is actually visible: wall finishes, floor material, doors and windows "
            "(position and approximate proportion only), furniture, lighting, and the layout. Then "
            "list cosmetic changes and, separately, changes that would be structural. Be explicit "
            "that exact dimensions cannot be recovered from a photograph. Reply as JSON with keys: "
            "summary, findings (list of {kind,label,value,detail}), suggestions (list of strings), "
            "structural_elements (list of strings), cosmetic_options (list of strings), "
            "uncertainty (string)."
        )
        payload = {
            "model": str(get_setting("openai_vision_model", "gpt-4o-mini")),
            "max_tokens": 1200, "temperature": 0.3,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
        }
        data = await _chat(payload)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError(f"Unexpected response shape from OpenAI vision: {exc}",
                          provider="openai", retryable=True)
        parsed = _safe_json(text)
        parsed["provider"] = "openai"
        parsed["model"] = str(get_setting("openai_vision_model", "gpt-4o-mini"))
        parsed.setdefault("summary", text.strip()[:1200])
        parsed.setdefault("findings", [])
        parsed.setdefault("suggestions", [])
        parsed["disclaimer"] = ("Vision analysis is an interpretation of a photo, not a survey. "
                                "Verify every measurement on site.")
        parsed["dimension_warning"] = ("Exact dimensions, wall thicknesses and heights cannot be "
                                       "recovered from a photograph.")
        return parsed


class OpenAIImage(ImageAdapter):
    name = "openai"

    def __init__(self) -> None:
        configured = bool(_key()) and str(get_setting("openai_image_model", "")) not in ("", "none")
        self.info = ProviderInfo(
            key="openai", label="OpenAI image generation",
            capabilities=["mood_image", "redesign_image"], requires_key=True,
            configured=configured, model=str(get_setting("openai_image_model", "")),
            setup=SETUP_INSTRUCTIONS["openai"], privacy=PRIVACY_NOTES["openai"])

    async def available(self) -> bool:
        return bool(_key()) and bool(str(get_setting("openai_image_model", "")))

    async def generate(self, *, prompt: str, style: str,
                       reference_paths: Optional[List[str]] = None,
                       size: str = "1024x1024") -> Dict[str, Any]:
        if not _key():
            raise AIError("No OpenAI API key configured for image generation.", provider="openai",
                          setup_required=True)
        model = str(get_setting("openai_image_model", ""))
        if not model:
            raise AIError("No image model is configured. Set one in Settings -> AI providers, or "
                          "use the interactive 3D viewer instead.", provider="openai",
                          setup_required=True)
        payload: Dict[str, Any] = {"model": model, "prompt": f"{prompt}. Style: {style}.",
                                   "n": 1, "size": size}
        if reference_paths:
            images = []
            for ref in reference_paths[:3]:
                if os.path.exists(ref):
                    with open(ref, "rb") as handle:
                        images.append(base64.b64encode(handle.read()).decode())
            if images:
                payload["image"] = images[0] if len(images) == 1 else images
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(f"{_base_url()}/images/generations",
                                             headers=_headers(), json=payload)
        except httpx.HTTPError as exc:
            raise AIError(f"Could not reach the image API: {exc}", provider="openai",
                          retryable=True)
        if response.status_code >= 400:
            raise AIError(_error_message(response, "image generation"), provider="openai",
                          setup_required=response.status_code in (401, 403))
        data = response.json()
        item = (data.get("data") or [{}])[0]
        out: Dict[str, Any] = {"provider": "openai", "model": model, "prompt": payload["prompt"]}
        if item.get("b64_json"):
            out["b64"] = item["b64_json"]
        if item.get("url"):
            out["url"] = item["url"]
        out["notice"] = ("This is an AI-generated illustration for inspiration. It is NOT your "
                         "model geometry and must not be used as a drawing. The 3D viewer shows "
                         "the actual design.")
        return out


async def _chat(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(f"{_base_url()}/chat/completions", headers=_headers(),
                                         json=payload)
    except httpx.HTTPError as exc:
        raise AIError(f"Could not reach the AI provider: {exc}. Check your internet connection or "
                      f"switch to the built-in local assistant.", provider="openai",
                      retryable=True)
    if response.status_code >= 400:
        raise AIError(_error_message(response, "the request"), provider="openai",
                      setup_required=response.status_code in (401, 403),
                      retryable=response.status_code in (429, 500, 502, 503, 504))
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise AIError(f"The provider returned a non-JSON response: {exc}", provider="openai",
                      retryable=True)


def _headers() -> Dict[str, str]:
    organisation = str(get_setting("openai_organization", "") or "")
    headers = {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}
    if organisation:
        headers["OpenAI-Organization"] = organisation
    return headers


def _error_message(response: httpx.Response, what: str) -> str:
    try:
        body = response.json()
        detail = body.get("error", {}).get("message") or json.dumps(body)[:300]
    except Exception:
        detail = response.text[:300]
    if response.status_code in (401, 403):
        return (f"The AI provider rejected the API key for {what} (HTTP {response.status_code}). "
                f"Check it in Settings -> AI providers. Detail: {detail}")
    if response.status_code == 429:
        return f"The AI provider rate-limited {what}. Try again in a moment. Detail: {detail}"
    return f"The AI provider could not complete {what} (HTTP {response.status_code}). {detail}"


def _safe_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"summary": str(data)}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                return data if isinstance(data, dict) else {"summary": text}
            except json.JSONDecodeError:
                pass
    return {"summary": text}


def _prompt_for(task: str, facts: Dict[str, Any], question: str, json_mode: bool) -> str:
    brief = {
        "explain_design": "Explain this design: layout logic, room sizes, circulation, open space, "
                          "trade-offs and what a professional still needs to check.",
        "explain_concept": "Compare the generated concepts for this brief and say who each one "
                           "suits.",
        "answer": f"Answer the user's question using only the project snapshot: {question}",
        "style_advice": "Give practical interior/exterior style advice for this project's style, "
                        "climate and room sizes.",
        "budget_commentary": "Comment on this budget estimate: what drives the range, what the user "
                             "should verify, and what changes would move the number most.",
        "compare": "Compare these alternatives on cost and space utilisation, and recommend one with "
                   "reasons.",
        "summary": "Summarise this project in five sentences or fewer.",
        "assumptions": "Restate the assumptions and unknowns in this design and what each one "
                       "depends on.",
    }.get(task, f"Help with this project. Task: {task}. Question: {question}")
    snapshot = json.dumps(facts, default=str)
    if len(snapshot) > 24000:
        snapshot = snapshot[:24000] + "...(truncated)"
    return f"{brief}\n\nProject snapshot (JSON):\n{snapshot}"
