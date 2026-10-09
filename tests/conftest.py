"""Shared fixtures. Every test gets an isolated JARVIS_HOME so nothing leaks."""

from __future__ import annotations

import pytest

from jarvis.config import Settings, settings_from_env
from jarvis.core.audit import AuditLog
from jarvis.core.events import EventBus
from jarvis.core.knowledge import KnowledgeBase
from jarvis.core.memory import MemoryStore
from jarvis.core.policy import AutoApprover, PolicyEngine
from jarvis.core.risk import RiskEngine
from jarvis.core.router import ModelRouter
from jarvis.core.supervisor import AgentRegistry, Supervisor
from jarvis.core.tasks import TaskManager
from jarvis.runtime import Runtime, build_runtime


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A throwaway data directory."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis"))
    return tmp_path / "jarvis"


@pytest.fixture()
def settings(home) -> Settings:
    return settings_from_env({"JARVIS_HOME": str(home), "JARVIS_AUTONOMY": "auto"})


@pytest.fixture()
def memory(settings) -> MemoryStore:
    settings.ensure_dirs()
    store = MemoryStore(settings.memory_db)
    yield store
    store.close()


@pytest.fixture()
def knowledge(settings) -> KnowledgeBase:
    settings.ensure_dirs()
    return KnowledgeBase(settings.home / "knowledge.sqlite3")


@pytest.fixture()
def audit(settings) -> AuditLog:
    settings.ensure_dirs()
    return AuditLog(settings.audit_path)


@pytest.fixture()
def risk() -> RiskEngine:
    return RiskEngine()


@pytest.fixture()
def policy(risk) -> PolicyEngine:
    """Policy with an approver that says no, so nothing risky happens by accident."""
    return PolicyEngine(risk, autonomy="auto")


@pytest.fixture()
def permissive_policy(risk) -> PolicyEngine:
    """Approves up to HIGH - used only to test that approvals are actually wired."""
    from jarvis.core.risk import RiskLevel

    return PolicyEngine(risk, autonomy="auto", approver=AutoApprover(RiskLevel.HIGH))


@pytest.fixture()
def tasks(tmp_path) -> TaskManager:
    return TaskManager(events=EventBus(), checkpoint_dir=tmp_path / "checkpoints")


@pytest.fixture()
def router() -> ModelRouter:
    return ModelRouter()


@pytest.fixture()
def runtime(settings) -> Runtime:
    """A full runtime with memory enabled and a no-approving approver."""
    from jarvis.core.policy import CallbackApprover

    rt = build_runtime(settings, approver=CallbackApprover(lambda decision: False))
    yield rt
    rt.shutdown()


@pytest.fixture()
def registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture()
def supervisor(registry, policy, audit) -> Supervisor:
    return Supervisor(registry=registry, policy=policy, audit=audit)
