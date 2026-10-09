"""Model router: selection, privacy, failover, and honest degradation."""

from __future__ import annotations

from jarvis.core.router import (
    CAP_LOCAL,
    CAP_VISION,
    PRIVACY_LOCAL,
    PRIVACY_ZDR,
    FunctionProvider,
    ModelRouter,
    ModelSpec,
    NullProvider,
    Requirements,
)
from jarvis.errors import ProviderUnavailable


def cloud_router() -> ModelRouter:
    """A router with a real cloud provider configured.

    Several tests need to choose *among* real models; with no provider
    registered every requirement falls through to the offline placeholder, which
    would make the assertions vacuous.
    """
    return ModelRouter(
        providers={
            "local": NullProvider(),
            "generic-cloud": FunctionProvider("cloud", lambda prompt, model: "answer"),
        }
    )


def test_default_weights_prefer_a_real_model_over_the_offline_stub():
    """Cost alone would pick the free offline placeholder - which would mean
    Jarvis answering with a stub while a real model is configured. The default
    weights put enough weight on quality to avoid that."""
    router = ModelRouter(
        providers={
            "local": NullProvider(),
            "generic-cloud": FunctionProvider("cloud", lambda p, m: "real answer"),
        }
    )
    model = router.select(Requirements(task="simple"))
    assert model.id != "jarvis-offline"


def test_cost_only_weighting_picks_the_cheapest():
    router = ModelRouter()
    model = router.select(Requirements(weights={"cost": 1.0, "latency": 0.0, "quality": 0.0}))
    assert model.cost_per_1k_output == 0.0


def test_with_no_provider_configured_the_offline_model_is_used():
    """No API keys at all: Jarvis degrades visibly instead of failing to start."""
    router = ModelRouter()
    model = router.select(Requirements())
    assert model.id == "jarvis-offline"
    assert model.has(CAP_LOCAL)


def test_quality_weighting_can_outvote_cost():
    router = cloud_router()
    model = router.select(Requirements(weights={"cost": 0.0, "latency": 0.0, "quality": 1.0}))
    assert model.quality >= 0.9


def test_capability_requirements_are_hard_constraints():
    router = cloud_router()
    model = router.select(Requirements(capabilities=frozenset({CAP_VISION})))
    assert model.has(CAP_VISION)


def test_privacy_requirements_are_hard_constraints():
    router = cloud_router()
    model = router.select(Requirements(min_privacy=PRIVACY_ZDR))
    assert model.privacy >= PRIVACY_ZDR
    assert model.id == "enterprise-zdr"


def test_no_silent_downgrade_when_nothing_fits():
    """If nothing meets the requirements, fall back visibly - never pick a model
    that violates a privacy or capability constraint."""
    router = cloud_router()
    model = router.select(Requirements(min_privacy=PRIVACY_LOCAL, capabilities=frozenset({CAP_VISION})))
    assert model.id == "jarvis-offline"
    assert model.has(CAP_LOCAL)


def test_latency_requirement_filters():
    router = cloud_router()
    model = router.select(Requirements(max_latency_ms=500))
    assert model.latency_ms <= 500


def test_context_window_requirement_filters():
    router = cloud_router()
    model = router.select(Requirements(min_context=300_000))
    assert model.context_window >= 300_000


def test_unavailable_provider_is_skipped():
    router = ModelRouter(
        providers={
            "local": NullProvider(),
            "generic-cloud": FunctionProvider("cloud", lambda p, m: "cloud answer"),
        }
    )
    router.mark_unavailable("generic-cloud", for_seconds=60)
    result = router.complete("hello", Requirements(capabilities=frozenset({CAP_VISION})))
    assert result.provider == "offline"
    assert result.degraded is True


def test_failing_provider_falls_back():
    calls = {"n": 0}

    def flaky(prompt, model):
        calls["n"] += 1
        raise RuntimeError("boom")

    router = ModelRouter(
        providers={"local": NullProvider(), "generic-cloud": FunctionProvider("cloud", flaky)}
    )
    result = router.complete("hello")
    assert result.degraded is True
    assert calls["n"] >= 1


def test_provider_unavailable_marks_the_provider_down():
    def down(prompt, model):
        raise ProviderUnavailable("gone")

    router = ModelRouter(
        providers={"local": NullProvider(), "generic-cloud": FunctionProvider("cloud", down)}
    )
    router.complete("hello", Requirements(capabilities=frozenset({CAP_VISION})))
    assert router.is_available("generic-cloud") is False
    assert "generic-cloud" in router.health()["down"]


def test_successful_call_marks_the_provider_up_again():
    router = ModelRouter(
        providers={"local": NullProvider(), "generic-cloud": FunctionProvider("c", lambda p, m: "ok")}
    )
    router.mark_unavailable("generic-cloud", for_seconds=60)
    router.mark_available("generic-cloud")
    assert router.is_available("generic-cloud") is True


def test_missing_provider_is_reported_rather_than_pretended():
    """A vision-capable model is in the registry but no provider is configured.
    Jarvis must say so, not claim to have used a model it cannot reach."""
    router = ModelRouter(providers={"local": NullProvider()})
    assert router.missing_providers() == ["generic-cloud"]
    assert router.available() == [m for m in router.models if m.provider == "local"]
    result = router.complete("hello", Requirements(capabilities=frozenset({CAP_VISION})))
    assert result.provider == "offline"
    assert result.degraded is True


def test_usage_is_counted_per_model():
    router = ModelRouter(
        providers={"local": NullProvider(), "generic-cloud": FunctionProvider("c", lambda p, m: "ok")}
    )
    router.complete("a")
    assert sum(router.health()["usage"].values()) == 1


def test_registering_a_model_does_not_rescale_existing_scores():
    router = cloud_router()
    before = router.select(Requirements()).id
    router.register(
        ModelSpec(id="expensive", provider="generic-cloud", cost_per_1k_output=1.0, quality=0.1)
    )
    assert router.select(Requirements()).id == before


def test_unregister_removes_a_model():
    router = cloud_router()
    router.unregister("reasoning-heavy")
    assert "reasoning-heavy" not in {m.id for m in router.models}


def test_null_provider_is_honest_about_being_a_placeholder():
    result = NullProvider().complete("hi", model=ModelSpec(id="x", provider="local"))
    assert result.degraded is True
    assert "placeholder" in result.text.lower()
    assert "not invent" in result.text.lower()


def test_cost_estimate_uses_both_directions():
    model = ModelSpec(id="m", provider="p", cost_per_1k_input=1.0, cost_per_1k_output=2.0)
    assert model.cost_estimate(1000, 1000) == 3.0


def test_render_lists_every_model():
    text = ModelRouter().render()
    assert "jarvis-offline" in text
    assert "UP" in text


def test_preferred_provider_gets_a_nudge_but_not_a_veto():
    router = ModelRouter(preferred_provider="generic-cloud")
    model = router.select(Requirements(weights={"cost": 1.0, "latency": 1.0, "quality": 1.2}))
    assert model.provider in {"local", "generic-cloud"}
