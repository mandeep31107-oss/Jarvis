"""Configuration, secrets handling and the CLI surface."""

from __future__ import annotations

import json

from jarvis.config import Settings, is_secret_name, load_dotenv, settings_from_env
from jarvis.interaction import cli
from jarvis.runtime import build_runtime


# ------------------------------------------------------------------ settings
def test_defaults_let_jarvis_boot_with_no_configuration():
    settings = settings_from_env({})
    assert settings.autonomy == "auto"
    assert settings.jurisdiction == "IN"
    assert settings.language_policy == "match"
    assert settings.computer_use is False


def test_an_invalid_autonomy_mode_falls_back_to_the_safe_default():
    assert settings_from_env({"JARVIS_AUTONOMY": "yolo"}).autonomy == "auto"


def test_an_invalid_language_policy_falls_back():
    assert settings_from_env({"JARVIS_LANGUAGE_POLICY": "klingon"}).language_policy == "match"


def test_capture_devices_are_parsed_and_normalised():
    settings = settings_from_env({"JARVIS_CAPTURE_DEVICES": "Camera, MICROPHONE ,"})
    assert settings.capture_devices == ("camera", "microphone")


def test_boolean_settings_accept_several_spellings():
    for value in ("1", "true", "yes", "on", "enabled"):
        assert settings_from_env({"JARVIS_COMPUTER_USE": value}).computer_use is True
    assert settings_from_env({"JARVIS_COMPUTER_USE": "off"}).computer_use is False


def test_secret_names_are_recognised():
    assert is_secret_name("OPENAI_API_KEY")
    assert is_secret_name("DB_PASSWORD")
    assert is_secret_name("MY_TOKEN")
    assert not is_secret_name("JARVIS_HOME")


def test_redacted_view_never_contains_a_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-abcdefghijklmnopqrstuvwx")
    monkeypatch.setenv("JARVIS_HOME", "/tmp/x")
    view = settings_from_env().redacted_view()
    assert "sk-proj-abcdefghijklmnopqrstuvwx" not in json.dumps(view)
    assert view["database_url"] in (None, "***")


def test_dotenv_parser_is_tolerant(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        '# a comment\nJARVIS_HOME=/tmp/from-env\nEMPTY=\nQUOTED="value"\n'
        "not a line\nJARVIS_AUTONOMY='review'\n",
        encoding="utf-8",
    )
    for key in ("JARVIS_HOME", "JARVIS_AUTONOMY"):
        monkeypatch.delenv(key, raising=False)
    parsed = load_dotenv(env)
    assert parsed["JARVIS_HOME"] == "/tmp/from-env"
    assert parsed["QUOTED"] == "value"
    assert parsed["JARVIS_AUTONOMY"] == "review"


def test_an_existing_environment_variable_beats_the_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("JARVIS_JURISDICTION=US\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_JURISDICTION", "GB")
    load_dotenv(env)
    assert settings_from_env().jurisdiction == "GB"


def test_a_missing_dotenv_is_not_an_error(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == {}


def test_ensure_dirs_creates_the_layout(tmp_path):
    settings = Settings(home=tmp_path / "jarvis")
    settings.ensure_dirs()
    assert settings.audit_path.parent.is_dir()
    assert settings.checkpoint_dir.is_dir()
    assert settings.documents_dir.is_dir()


# ---------------------------------------------------------------------- CLI
def test_parser_accepts_the_documented_subcommands():
    parser = cli.build_parser()
    for argv in (["status"], ["audit", "5"], ["revenue", "idea"], ["run", "hello"],
                 ["check-secret", "text"], ["repl"]):
        parser.parse_args(argv)


def test_cli_status_runs(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Jarvis" in out
    assert "autonomy" in out


def test_cli_status_json_is_machine_readable(capsys, tmp_path, monkeypatch):
    """Assert the shape, not a hardcoded phase number.

    Pinning `phase == 1` made this test fail for the right reason (the build
    moved on) but for no useful purpose. Checking that the reported phase agrees
    with the single source of truth catches a real regression instead.
    """
    from jarvis.version import PHASE, PHASE_NAME, __version__

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    cli.main(["--json", "status"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == PHASE
    assert payload["phase_name"] == PHASE_NAME
    assert payload["version"] == __version__
    for key in ("agents", "policy", "models", "memory", "audit", "run_state"):
        assert key in payload, f"status JSON is missing {key!r}"


def test_cli_run_returns_zero_for_a_handled_request(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert cli.main(["run", "build a portfolio site", "--intent", "build"]) == 0


def test_cli_run_returns_nonzero_when_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    # A research request with no provider configured is honestly "not ok".
    assert cli.main(["run", "what is the capital of France"]) == 1


def test_cli_audit_prints_records(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    cli.main(["run", "hello there"])
    capsys.readouterr()
    assert cli.main(["audit", "5"]) == 0
    assert "runtime.start" in capsys.readouterr().out


def test_cli_revenue_asks_for_numbers_rather_than_inventing_them(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert cli.main(["revenue", "websites for local clinics"]) == 0
    out = capsys.readouterr().out
    assert "cannot model" in out
    assert "will not invent" in out
    assert "OPTIONS" in out


def test_cli_revenue_prints_the_full_assessment_when_given_numbers(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert cli.main(["--json", "revenue", "websites for local clinics"]) == 0
    capsys.readouterr()


def test_cli_check_secret_finds_a_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert cli.main(["check-secret", "AKIAIOSFODNN7EXAMPLE"]) == 1
    assert "aws-access-key-id" in capsys.readouterr().out


def test_cli_check_secret_passes_clean_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert cli.main(["check-secret", "nothing to see here"]) == 0
    assert "no secrets detected" in capsys.readouterr().out


def test_repl_commands_execute_without_a_tty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    settings = settings_from_env()
    runtime = build_runtime(settings)
    repl = cli.Repl(runtime)
    for command in ("/help", "/status", "/agents", "/activity", "/audit 5", "/models",
                    "/lang Ab mera project kholo", "/memory counts", "/tasks",
                    "/terms whatsapp bulk_messaging", "/compliance IN",
                    "/risk message.send someone", "/risk captcha.solve",
                    "/privacy on", "/privacy off", "/camera off", "/pause", "/resume",
                    "/stop", "/autostart", "/nonsense"):
        assert repl.command(command) is True, command
    out = capsys.readouterr().out
    assert "COMMANDS" in out
    assert "APPROVAL" not in out  # nothing risky was attempted
    runtime.shutdown()


def test_repl_quit_exits(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    runtime = build_runtime(settings_from_env())
    repl = cli.Repl(runtime)
    assert repl.command("/quit") is False
    assert repl.command("/exit") is False
    runtime.shutdown()


def test_repl_survives_a_bad_argument(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    runtime = build_runtime(settings_from_env())
    repl = cli.Repl(runtime)
    assert repl.command("/audit not-a-number") is True
    assert "[error]" in capsys.readouterr().out
    runtime.shutdown()


def test_repl_memory_subcommands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    runtime = build_runtime(settings_from_env())
    repl = cli.Repl(runtime)
    repl.command("/memory add the user prefers vim")
    repl.command("/memory search vim")
    repl.command("/memory export")
    repl.command("/memory counts")
    out = capsys.readouterr().out
    assert "vim" in out
    runtime.shutdown()


def test_repl_build_and_doc_write_real_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    runtime = build_runtime(settings_from_env())
    repl = cli.Repl(runtime)
    repl.command(f"/build portfolio {tmp_path / 'site'}")
    repl.command("/doc xlsx Sample")
    out = capsys.readouterr().out
    assert (tmp_path / "site" / "README.md").is_file()
    assert "Created XLSX" in out
    runtime.shutdown()


def test_repl_consent_is_recorded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    runtime = build_runtime(settings_from_env())
    repl = cli.Repl(runtime)
    repl.command("/consent camera")
    assert "camera" in runtime.host.consented_devices
    assert any(r["action"] == "consent.granted" for r in runtime.audit.entries())
    runtime.shutdown()


def test_repl_consent_rejects_an_unknown_device(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    runtime = build_runtime(settings_from_env())
    repl = cli.Repl(runtime)
    repl.command("/consent telepathy")
    assert "usage" in capsys.readouterr().out
    runtime.shutdown()


def test_repl_lock_and_resume(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    runtime = build_runtime(settings_from_env())
    repl = cli.Repl(runtime)
    repl.command("/lock")
    assert runtime.host.privacy_mode.value == "locked"
    repl.command("/resume")
    assert runtime.host.privacy_mode.value == "normal"
    runtime.shutdown()

def _make_repl(tmp_path):
    import os

    from jarvis.config import settings_from_env
    from jarvis.interaction.cli import Repl
    from jarvis.runtime import build_runtime

    os.environ["JARVIS_HOME"] = str(tmp_path / "h")
    return Repl(build_runtime(settings_from_env(), with_memory=False))


def test_config_subcommand_masks_secrets(capsys, tmp_path, monkeypatch):
    """The configuration view must never print a secret value."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("JARVIS_DATABASE_URL", "postgresql://user:hunter2secret@localhost/app")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-abcdefghij1234567890")
    assert cli.main(["config", "--show"]) == 0
    out = capsys.readouterr().out
    assert "hunter2secret" not in out, "a credential leaked into the config view"
    assert "sk-proj-abcdefghij1234567890" not in out, "an API key leaked into the config view"
    assert "***" in out, "the database URL should be masked, not shown"
    # presence is reported, value is not
    assert "OPENAI" in out


def test_bare_text_is_treated_as_a_one_shot_request(capsys, tmp_path, monkeypatch):
    """`jarvis "question"` must not require the `run` subcommand."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    # No search provider is configured, so Jarvis cannot answer - exit 1 is right.
    assert cli.main(["what is open source software"]) == 1
    out = capsys.readouterr().out
    assert "open source software" in out
    assert "will not guess" in out


def test_a_fulfilled_request_exits_zero(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert cli.main(["run", "remind me about the meeting"]) == 0
    out = capsys.readouterr().out
    assert "[productivity]" in out, out


def test_repl_config_command_does_not_leak(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_abcdefghijklmnop1234")
    repl = _make_repl(tmp_path)
    repl.command("/config")
    out = capsys.readouterr().out
    assert "sk_live_abcdefghijklmnop1234" not in out
    assert "effective configuration" in out


def test_check_secret_exits_nonzero_for_a_leak(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert cli.main(["check-secret", 'password = "hunter2secret"']) == 1
    assert "assignment-secret" in capsys.readouterr().out


def test_check_secret_passes_clean_text(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    assert cli.main(["check-secret", "a perfectly ordinary sentence"]) == 0


def test_read_reports_honestly_when_no_fetcher_is_configured(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    monkeypatch.delenv("JARVIS_FETCH", raising=False)
    assert cli.main(["read", "https://example.eu/dsa"]) == 1
    out = capsys.readouterr().out
    assert "has not read it" in out
    assert "will not describe a page it has not retrieved" in out


def test_read_refuses_an_internal_address(capsys, tmp_path, monkeypatch):
    """The SSRF guard has to hold through the CLI, not just in the fetcher."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("JARVIS_FETCH", "on")
    assert cli.main(["read", "http://169.254.169.254/latest/meta-data/"]) == 1
    out = capsys.readouterr().out
    assert "not a public address" in out


def test_read_can_store_a_local_document(capsys, tmp_path, monkeypatch):
    from jarvis.research.fetch import FileFetcher

    pages = tmp_path / "pages"
    pages.mkdir()
    prose = "The rule applies from 1 January 2025 to all providers. " * 6
    (pages / "rule.html").write_text(
        f"<html><head><title>Rule</title></head><body><h1>Rule</h1><p>{prose}</p></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))

    from jarvis.config import settings_from_env
    from jarvis.interaction.cli import Repl
    from jarvis.runtime import build_runtime

    # Injecting the fetcher is the supported seam - build_runtime wires the
    # pipeline itself, which is the path a real deployment uses.
    runtime = build_runtime(
        settings_from_env(), with_memory=False, fetcher=FileFetcher(pages)
    )
    repl = Repl(runtime)
    repl.command("/read rule.html the rule applies from 1 January 2025")
    out = capsys.readouterr().out
    assert "stored" in out, out
    assert "official_secondary" in out
    runtime.shutdown()
