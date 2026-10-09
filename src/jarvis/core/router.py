"""Model router (spec section 25).

No part of Jarvis is welded to one model. Every call goes through the router,
which picks a model from a registry based on what the task actually needs --
capability, cost, latency, context size, privacy -- and falls back through the
chain when a provider is unavailable.

The core ships with an offline :class:`NullProvider` so the whole system boots
and answers without any API key configured. That is a deliberate design choice:
graceful degradation over a hard dependency (spec section 36).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from jarvis.errors import ProviderUnavailable

CAP_VISION = "vision"
CAP_REASONING = "reasoning"
CAP_CODE = "code"
CAP_LONG_CONTEXT = "long_context"
CAP_SPEECH = "speech"
CAP_TOOL_USE = "tool_use"
CAP_LOCAL = "local"  # runs on-device; nothing leaves the machine

PRIVACY_LOCAL = 3  # nothing leaves the device
PRIVACY_ZDR = 2  # zero-data-retention enterprise tier
PRIVACY_STANDARD = 1


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    capabilities: frozenset[str] = frozenset()
    context_window: int = 32_000
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    latency_ms: int = 800
    privacy: int = PRIVACY_STANDARD
    #: Relative quality 0..1 for open-ended reasoning; used only to break ties.
    quality: float = 0.7

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def cost_estimate(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1000.0 * self.cost_per_1k_input
            + output_tokens / 1000.0 * self.cost_per_1k_output
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "capabilities": sorted(self.capabilities),
            "context_window": self.context_window,
            "cost_per_1k_input": self.cost_per_1k_input,
            "cost_per_1k_output": self.cost_per_1k_output,
            "latency_ms": self.latency_ms,
            "privacy": self.privacy,
            "quality": self.quality,
        }


@dataclass
class Requirements:
    """What a task needs from a model."""

    task: str = "general"
    capabilities: frozenset[str] = frozenset()
    min_context: int = 0
    max_cost_per_1k: float = float("inf")
    max_latency_ms: int = 100_000
    min_privacy: int = PRIVACY_STANDARD
    #: Weights let a caller say "I care twice as much about latency as cost".
    weights: dict[str, float] = field(
        default_factory=lambda: {"cost": 1.0, "latency": 1.0, "quality": 1.2}
    )


#: Built-in defaults. Real deployments replace this with their own registry;
#: nothing else in Jarvis references a specific model id.
DEFAULT_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="jarvis-offline",
        provider="local",
        capabilities=frozenset({CAP_LOCAL, CAP_REASONING, CAP_CODE}),
        context_window=16_000,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        latency_ms=5,
        privacy=PRIVACY_LOCAL,
        quality=0.25,
    ),
    ModelSpec(
        id="fast-cheap",
        provider="generic-cloud",
        capabilities=frozenset({CAP_CODE, CAP_TOOL_USE}),
        context_window=128_000,
        cost_per_1k_input=0.0002,
        cost_per_1k_output=0.0006,
        latency_ms=350,
        privacy=PRIVACY_STANDARD,
        quality=0.6,
    ),
    ModelSpec(
        id="balanced",
        provider="generic-cloud",
        capabilities=frozenset({CAP_CODE, CAP_REASONING, CAP_TOOL_USE, CAP_LONG_CONTEXT}),
        context_window=200_000,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        latency_ms=900,
        privacy=PRIVACY_STANDARD,
        quality=0.85,
    ),
    ModelSpec(
        id="reasoning-heavy",
        provider="generic-cloud",
        capabilities=frozenset({CAP_CODE, CAP_REASONING, CAP_LONG_CONTEXT, CAP_TOOL_USE}),
        context_window=400_000,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        latency_ms=3000,
        privacy=PRIVACY_STANDARD,
        quality=0.95,
    ),
    ModelSpec(
        id="vision-capable",
        provider="generic-cloud",
        capabilities=frozenset({CAP_VISION, CAP_REASONING, CAP_TOOL_USE}),
        context_window=128_000,
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.015,
        latency_ms=1200,
        privacy=PRIVACY_STANDARD,
        quality=0.85,
    ),
    ModelSpec(
        id="enterprise-zdr",
        provider="generic-cloud",
        capabilities=frozenset({CAP_CODE, CAP_REASONING, CAP_TOOL_USE, CAP_LONG_CONTEXT}),
        context_window=200_000,
        cost_per_1k_input=0.006,
        cost_per_1k_output=0.024,
        latency_ms=1000,
        privacy=PRIVACY_ZDR,
        quality=0.85,
    ),
)


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    #: True when the answer came from the offline fallback rather than a real model.
    degraded: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    """The only interface Jarvis needs from a model backend."""

    name: str

    def complete(self, prompt: str, *, model: ModelSpec, **kwargs: Any) -> Completion:
        ...  # pragma: no cover - protocol


class NullProvider:
    """Offline provider. Honest about what it is instead of making things up."""

    name = "offline"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str, *, model: ModelSpec, **kwargs: Any) -> Completion:
        self.calls += 1
        return Completion(
            text=(
                "No model provider is configured, so this is a placeholder rather than an "
                "answer. Jarvis will not invent a response. Configure a provider in your "
                "environment (see .env.example) and re-run."
            ),
            model=model.id,
            provider=self.name,
            degraded=True,
            meta={"prompt_chars": len(prompt)},
        )


class FunctionProvider:
    """Adapter that turns any ``prompt -> str`` callable into a provider."""

    def __init__(self, name: str, fn: Callable[[str, ModelSpec], str]) -> None:
        self.name = name
        self._fn = fn

    def complete(self, prompt: str, *, model: ModelSpec, **kwargs: Any) -> Completion:
        text = self._fn(prompt, model)
        return Completion(
            text=text,
            model=model.id,
            provider=self.name,
            input_tokens=len(prompt) // 4,
            output_tokens=len(text) // 4,
        )


class ModelRouter:
    """Chooses models, tracks health, and routes calls with fallback."""

    def __init__(
        self,
        models: Sequence[ModelSpec] = DEFAULT_MODELS,
        *,
        providers: dict[str, LLMProvider] | None = None,
        cooldown_s: float = 30.0,
        preferred_provider: str = "auto",
    ) -> None:
        self._models: dict[str, ModelSpec] = {m.id: m for m in models}
        self.providers: dict[str, LLMProvider] = dict(providers or {})
        # Only the offline provider is registered by default. A cloud model with
        # no configured provider must fall through visibly, not be silently
        # replaced by a stub that pretends to be that model.
        self.providers.setdefault("local", NullProvider())
        self.cooldown_s = cooldown_s
        self.preferred_provider = preferred_provider
        self._down_until: dict[str, float] = {}
        self._usage: dict[str, int] = {}
        self._last_choice: str | None = None

    # --- registry ------------------------------------------------------------
    def register(self, model: ModelSpec) -> None:
        self._models[model.id] = model

    def unregister(self, model_id: str) -> None:
        self._models.pop(model_id, None)

    @property
    def models(self) -> list[ModelSpec]:
        return list(self._models.values())

    def available(self) -> list[ModelSpec]:
        """Models that could actually serve a request right now.

        A model whose provider is not registered is *not* available: reporting it
        as a choice the router could make would be a promise Jarvis cannot keep.
        """
        now = time.monotonic()
        return [
            m
            for m in self._models.values()
            if m.provider in self.providers and self._down_until.get(m.provider, 0) <= now
        ]

    def missing_providers(self) -> list[str]:
        """Providers referenced by the model registry but not configured.

        Surfaced in the dashboard so "no model answered" is explained rather than
        mysterious.
        """
        referenced = {m.provider for m in self._models.values()}
        return sorted(referenced - set(self.providers))

    # --- health --------------------------------------------------------------
    def mark_unavailable(self, provider: str, *, for_seconds: float | None = None) -> None:
        self._down_until[provider] = time.monotonic() + (for_seconds or self.cooldown_s)

    def mark_available(self, provider: str) -> None:
        self._down_until.pop(provider, None)

    def is_available(self, provider: str) -> bool:
        return self._down_until.get(provider, 0) <= time.monotonic()

    def health(self) -> dict[str, Any]:
        return {
            "models": {m.id: m.provider for m in self._models.values()},
            "available": [m.id for m in self.available()],
            "down": [p for p in self._down_until if not self.is_available(p)],
            "missing_providers": self.missing_providers(),
            "usage": dict(self._usage),
            "last_choice": self._last_choice,
            "preferred_provider": self.preferred_provider,
        }

    # --- selection -----------------------------------------------------------
    def candidates(self, req: Requirements) -> list[ModelSpec]:
        """Models that satisfy every hard requirement."""
        out = []
        for m in self.available():
            if not req.capabilities.issubset(m.capabilities):
                continue
            if m.context_window < req.min_context:
                continue
            if m.cost_per_1k_output > req.max_cost_per_1k:
                continue
            if m.latency_ms > req.max_latency_ms:
                continue
            if m.privacy < req.min_privacy:
                continue
            out.append(m)
        return out

    #: Fixed reference points for the cost function. Using absolute scales
    #: (rather than "the most expensive model in the registry") means registering
    #: a new model never silently changes the ranking of the existing ones.
    COST_REFERENCE = 0.10  # USD per 1k output tokens counts as "expensive"
    LATENCY_REFERENCE = 5000  # ms counts as "slow"

    def rank(self, req: Requirements, candidates: Iterable[ModelSpec]) -> list[tuple[ModelSpec, float]]:
        """Score candidates; lower is better (it is a cost function)."""
        w = req.weights
        scored: list[tuple[ModelSpec, float]] = []
        for m in candidates:
            cost_norm = min(1.0, m.cost_per_1k_output / self.COST_REFERENCE)
            lat_norm = min(1.0, m.latency_ms / self.LATENCY_REFERENCE)
            quality_gap = 1.0 - m.quality
            preference = 0.0
            if self.preferred_provider != "auto" and m.provider == self.preferred_provider:
                preference = -0.15  # small nudge, never overrides a hard requirement
            score = (
                w.get("cost", 1.0) * cost_norm
                + w.get("latency", 1.0) * lat_norm
                + w.get("quality", 1.2) * quality_gap
                + preference
            )
            scored.append((m, round(score, 4)))
        scored.sort(key=lambda pair: pair[1])
        return scored

    def select(self, req: Requirements) -> ModelSpec:
        cands = self.candidates(req)
        if not cands:
            # Relax nothing silently: fall back to the offline model so the agent
            # degrades visibly instead of quietly picking a model that violates
            # a privacy or capability requirement.
            offline = self._models.get("jarvis-offline")
            if offline is not None:
                return offline
            raise ProviderUnavailable(
                f"No model satisfies {req.capabilities or 'the'} requirements."
            )
        return self.rank(req, cands)[0][0]

    # --- calling -------------------------------------------------------------
    def complete(
        self,
        prompt: str,
        req: Requirements | None = None,
        *,
        max_fallbacks: int = 2,
        **kwargs: Any,
    ) -> Completion:
        """Call the best model, falling back down the ranked list on failure."""
        req = req or Requirements()
        ranked = self.rank(req, self.candidates(req))
        if not ranked:
            provider = self.providers.get("local") or NullProvider()
            spec = self._models.get("jarvis-offline") or ModelSpec(id="jarvis-offline", provider="local")
            return provider.complete(prompt, model=spec)

        tried: list[str] = []
        for model, _score in ranked[: max_fallbacks + 1]:
            provider = self.providers.get(model.provider)
            if provider is None:
                self.mark_unavailable(model.provider, for_seconds=self.cooldown_s)
                tried.append(f"{model.id}(no provider)")
                continue
            try:
                result = provider.complete(prompt, model=model, **kwargs)
            except ProviderUnavailable:
                self.mark_unavailable(model.provider)
                tried.append(f"{model.id}(unavailable)")
                continue
            except Exception as exc:  # noqa: BLE001 - any provider failure triggers fallback
                self.mark_unavailable(model.provider)
                tried.append(f"{model.id}({type(exc).__name__})")
                continue
            self.mark_available(model.provider)
            self._usage[model.id] = self._usage.get(model.id, 0) + 1
            self._last_choice = model.id
            result.meta.setdefault("fallback_tried", tried)
            return result

        provider = self.providers.get("local") or NullProvider()
        spec = self._models.get("jarvis-offline") or ModelSpec(id="jarvis-offline", provider="local")
        result = provider.complete(prompt, model=spec)
        result.meta["fallback_tried"] = tried
        return result

    def render(self) -> str:
        rows = sorted(self._models.values(), key=lambda m: m.provider)
        return "\n".join(
            f"- {m.id:<18} {m.provider:<14} {'UP' if self.is_available(m.provider) else 'DOWN'}"
            f"  ctx={m.context_window:>7}  out=${m.cost_per_1k_output:.4f}/1k  "
            f"{m.latency_ms:>5}ms  {','.join(sorted(m.capabilities)) or '-'}"
            for m in rows
        )
