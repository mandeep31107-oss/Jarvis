"""Website/application builder (spec section 11)."""

from __future__ import annotations

import json

import pytest

from jarvis.agents.base import AgentRequest
from jarvis.agents.webbuilder import CATEGORIES, SECURITY_CHECKLIST, WebBuilderAgent
from jarvis.interaction.intents import SITE_CATEGORIES


@pytest.fixture()
def agent() -> WebBuilderAgent:
    return WebBuilderAgent()


# --------------------------------------------------------------------- plans
@pytest.mark.parametrize("category", sorted(CATEGORIES))
def test_every_category_produces_a_complete_plan(agent, category):
    plan = agent.plan(category, f"{category}-site")
    assert plan.pages
    assert plan.tables
    assert plan.stack
    assert plan.security == list(SECURITY_CHECKLIST)
    assert plan.approvals_needed, "a build always needs at least one human decision"


def test_every_category_the_parser_can_produce_is_known_to_the_builder():
    """A regression guard: the parser used to emit 'e-commerce' while the builder
    only knew 'ecommerce', so /build would reject its own suggestion."""
    assert set(SITE_CATEGORIES) <= set(CATEGORIES)


def test_payment_categories_flag_the_credential_decision(agent):
    plan = agent.plan("ecommerce", "shop")
    assert plan.needs_payments is True
    assert any("payment provider" in a.lower() for a in plan.approvals_needed)


def test_non_payment_categories_do_not_ask_for_payments(agent):
    assert agent.plan("portfolio", "me").needs_payments is False


def test_extra_features_are_merged_without_duplicates(agent):
    plan = agent.plan("ecommerce", "shop", features=["search", "wishlist"])
    assert plan.features.count("search") == 1
    assert "wishlist" in plan.features


def test_plan_is_serialisable(agent):
    json.dumps(agent.plan("saas", "app").as_dict())


# ------------------------------------------------------------------ scaffold
def test_scaffold_writes_a_runnable_skeleton(agent, tmp_path):
    plan = agent.plan("ecommerce", "shop")
    written, problems = agent.scaffold(plan, tmp_path / "shop")
    assert problems == [], problems
    assert len(written) >= 12
    for relative in ("README.md", ".gitignore", ".env.example", "docker-compose.yml",
                     "db/schema.sql", "backend/app/main.py", "backend/app/auth.py",
                     "frontend/src/App.tsx", "frontend/package.json",
                     "docs/SECURITY.md", "docs/ARCHITECTURE.md",
                     ".github/workflows/ci.yml"):
        assert (tmp_path / "shop" / relative).is_file(), relative


def test_scaffold_validation_passes_on_its_own_output(agent, tmp_path):
    plan = agent.plan("saas", "app")
    written, _ = agent.scaffold(plan, tmp_path / "app")
    assert agent.validate(tmp_path / "app", written) == []


def test_env_example_contains_no_real_values(agent, tmp_path):
    """Spec sections 11 and 32: never expose secrets in source code."""
    agent.scaffold(agent.plan("ecommerce", "shop"), tmp_path / "shop")
    from jarvis.util import redaction

    body = (tmp_path / "shop" / ".env.example").read_text(encoding="utf-8")
    assert "PAYMENT_SECRET_KEY=" in body
    # No secret-shaped content anywhere in the committed example file.
    assert redaction.scan(body) == [], redaction.scan(body)
    # Every credential variable is left empty for the user to fill in.
    for line in body.splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            if any(hint in key.upper() for hint in ("SECRET", "PASSWORD", "TOKEN", "KEY")):
                assert value.strip() == "", line


def test_gitignore_excludes_the_env_file(agent, tmp_path):
    agent.scaffold(agent.plan("blog", "blog"), tmp_path / "blog")
    ignore = (tmp_path / "blog" / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore
    assert "!.env.example" in ignore


def test_schema_matches_the_planned_tables(agent, tmp_path):
    plan = agent.plan("ecommerce", "shop")
    agent.scaffold(plan, tmp_path / "shop")
    schema = (tmp_path / "shop" / "db" / "schema.sql").read_text(encoding="utf-8")
    for table in plan.tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema


def test_security_checklist_is_written_to_the_scaffold(agent, tmp_path):
    agent.scaffold(agent.plan("saas", "app"), tmp_path / "app")
    body = (tmp_path / "app" / "docs" / "SECURITY.md").read_text(encoding="utf-8")
    assert "argon2" in body.lower() or "hash" in body.lower()
    assert ".env" in body


def test_password_hashing_refuses_a_weak_fallback(agent, tmp_path):
    """The generated auth module must fail loudly rather than hash weakly."""
    agent.scaffold(agent.plan("saas", "app"), tmp_path / "app")
    source = (tmp_path / "app" / "backend" / "app" / "auth.py").read_text(encoding="utf-8")
    assert "refusing to hash with a weak algorithm" in source
    assert "md5" not in source.lower()


def test_payments_are_not_wired_automatically(agent, tmp_path):
    agent.scaffold(agent.plan("ecommerce", "shop"), tmp_path / "shop")
    backend = (tmp_path / "shop" / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert "stripe" not in backend.lower()
    assert "payment" not in backend.lower() or "NOT" in (
        tmp_path / "shop" / "README.md"
    ).read_text(encoding="utf-8")


def test_validation_detects_a_missing_file(agent, tmp_path):
    plan = agent.plan("blog", "blog")
    written, _ = agent.scaffold(plan, tmp_path / "blog")
    (tmp_path / "blog" / "db" / "schema.sql").unlink()
    problems = agent.validate(tmp_path / "blog", written)
    assert any("missing after write" in p or "expected" in p for p in problems)


def test_frontend_package_json_is_valid(agent, tmp_path):
    agent.scaffold(agent.plan("dashboard", "dash"), tmp_path / "dash")
    payload = json.loads((tmp_path / "dash" / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert payload["dependencies"]["react"]


# --------------------------------------------------------------------- agent
def test_agent_scaffolds_when_given_a_destination(agent, tmp_path):
    result = agent.run(
        AgentRequest(
            intent="build",
            text="build me an e-commerce website",
            params={"category": "ecommerce", "name": "shop", "path": str(tmp_path / "shop")},
        )
    )
    assert result.ok, result.summary
    assert result.data["scaffold"]["files"] >= 12
    assert result.data["scaffold"]["problems"] == []


def test_agent_plans_without_writing_when_no_destination(agent):
    result = agent.run(AgentRequest(intent="build", text="a portfolio",
                                    params={"category": "portfolio"}))
    assert result.ok
    assert "scaffold" not in result.data
    assert any("target directory" in f for f in result.follow_ups)


def test_agent_rejects_an_unknown_category(agent):
    result = agent.run(AgentRequest(intent="build", text="x", params={"category": "cryptoponzi"}))
    assert not result.ok
    assert "Unknown site category" in result.summary


def test_agent_flags_that_payments_need_approval(agent, tmp_path):
    result = agent.run(
        AgentRequest(intent="build", text="store",
                     params={"category": "ecommerce", "path": str(tmp_path / "s")})
    )
    assert any("credentials must never be committed" in c.statement for c in result.claims)
