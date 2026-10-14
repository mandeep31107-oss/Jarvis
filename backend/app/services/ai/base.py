"""AI adapter interfaces.

Ground rule for this whole package: **the language model never generates
geometry**. Rooms, walls, openings and stairs always come from the deterministic
layout engine. A model may only (a) explain what the engine produced, (b) answer
questions about it, (c) describe an uploaded photo, (d) help parse a natural
language edit request into one of the structured operations the editor knows, and
(e) render a mood image where the provider supports it. Every adapter returns
plain text/JSON that the app then validates before acting on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class AIError(RuntimeError):
    """Raised when a provider call fails. Message is safe to show the user."""

    def __init__(self, message: str, *, provider: str = "", retryable: bool = False,
                 setup_required: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.setup_required = setup_required


@dataclass
class ProviderInfo:
    key: str
    label: str
    capabilities: List[str]
    requires_key: bool
    configured: bool
    model: str = ""
    setup: List[str] = field(default_factory=list)
    privacy: str = ""
    offline: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "capabilities": self.capabilities,
            "requires_key": self.requires_key, "configured": self.configured,
            "model": self.model, "setup": self.setup, "privacy": self.privacy,
            "offline": self.offline,
        }


class LLMAdapter:
    """Text completion over a structured project context."""

    name = "base"
    info: ProviderInfo

    async def complete(self, *, task: str, facts: Dict[str, Any], question: str = "",
                       system: str = "", json_mode: bool = False,
                       max_tokens: int = 900) -> str:
        raise NotImplementedError

    async def available(self) -> bool:
        return True


class VisionAdapter:
    """Describe an uploaded photo or drawing."""

    name = "base"
    info: ProviderInfo

    async def analyse(self, *, path: str, room_type: str, goals: List[str],
                      note: str = "", mime_type: str = "image/jpeg") -> Dict[str, Any]:
        raise NotImplementedError

    async def available(self) -> bool:
        return True


class ImageAdapter:
    """Optional mood/render image generation. Always honest when unavailable."""

    name = "base"
    info: ProviderInfo

    async def generate(self, *, prompt: str, style: str, reference_paths: Optional[List[str]] = None,
                       size: str = "1024x1024") -> Dict[str, Any]:
        raise NotImplementedError

    async def available(self) -> bool:
        return False


@dataclass
class ParsedCommand:
    """A natural language request reduced to structured editor operations."""

    intent: str
    operations: List[Dict[str, Any]] = field(default_factory=list)
    explanation: str = ""
    clarification: str = ""
    confidence: float = 0.0
    preview_notes: List[str] = field(default_factory=list)
    structural: bool = False
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent, "operations": self.operations,
            "explanation": self.explanation, "clarification": self.clarification,
            "confidence": round(self.confidence, 2), "preview_notes": self.preview_notes,
            "structural": self.structural, "warnings": self.warnings,
        }


SETUP_INSTRUCTIONS: Dict[str, List[str]] = {
    "openai": [
        "Create an API key at platform.openai.com -> API keys.",
        "Paste the key into Settings -> AI providers -> OpenAI. It is stored only on this "
        "server (data/ai_settings.json, mode 600) and is never sent to your browser.",
        "Optionally set a custom base URL if you use a compatible gateway (Azure OpenAI, "
        "OpenRouter, a local OpenAI-compatible server).",
        "Choose the chat model (default gpt-4o-mini) and, if you want redesign images, an "
        "image model.",
    ],
    "anthropic": [
        "Create an API key at console.anthropic.com.",
        "Paste it into Settings -> AI providers -> Anthropic. It stays on this server.",
        "Pick a model (default claude-3-5-sonnet-latest).",
    ],
    "gemini": [
        "Create an API key at aistudio.google.com.",
        "Paste it into Settings -> AI providers -> Google Gemini. It stays on this server.",
        "Pick a text model and, optionally, an image model.",
    ],
    "stability": [
        "Create an API key at platform.stability.ai.",
        "Paste it into Settings -> AI providers -> Stability. It stays on this server.",
    ],
    "local": [
        "Nothing to configure. The built-in provider runs entirely on this machine and needs "
        "no internet connection and no API key.",
        "It produces rule-based explanations, plan analysis and a real image inspection "
        "(palette, brightness, plan-vs-photo detection). It cannot invent photorealistic "
        "renders -- switch to a hosted provider for that.",
    ],
    "none": [
        "Image generation is disabled. Everything else in the app still works: plans, 3D, "
        "budgets, exports and the design assistant all run locally.",
    ],
}

PRIVACY_NOTES: Dict[str, str] = {
    "local": "No data leaves this machine. Nothing is sent to a third party.",
    "openai": "When you use this provider, the text context (room list, areas, your question) and "
              "any image you ask it to analyse are sent to OpenAI. Do not upload photos you are "
              "not comfortable sharing. Review OpenAI's data retention settings; this app does not "
              "authorise training on your content.",
    "anthropic": "When you use this provider, the text context and any image you ask it to analyse "
                 "are sent to Anthropic. Review their data usage policy before uploading private "
                 "photos.",
    "gemini": "When you use this provider, the text context and any image you ask it to analyse are "
              "sent to Google. Review their data usage policy before uploading private photos.",
    "stability": "Prompts (and reference images where supported) are sent to Stability AI.",
    "none": "No third-party image service is used.",
}

DISCLAIMER = ("Generated by an AI assistant as a preliminary design aid. It is not a construction "
              "drawing, not a structural design and not legal or code advice. Have a licensed "
              "architect and structural engineer review anything you intend to build.")
