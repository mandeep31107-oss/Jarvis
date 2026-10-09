"""Redaction: nothing credential-shaped may reach a persisted artifact."""

from __future__ import annotations

from jarvis.util import redaction
from jarvis.util.redaction import Finding, redact, scan, scan_mapping


def test_detects_aws_key():
    findings = scan("my key is AKIAIOSFODNN7EXAMPLE in the config")
    assert any(f.label == "aws-access-key-id" for f in findings)


def test_detects_private_key_block():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJ\n-----END RSA PRIVATE KEY-----"
    assert any(f.label == "pem-private-key" for f in scan(text))


def test_detects_password_assignment():
    findings = scan('DATABASE_PASSWORD = "hunter2secret"')
    assert any(f.label == "assignment-secret" for f in findings)


def test_detects_url_credentials():
    findings = scan("postgres://app:s3cr3tpassword@db.internal:5432/app")
    assert any(f.label == "url-credentials" for f in findings)


def test_detects_bearer_token():
    assert scan("Authorization: Bearer abcdef0123456789abcdef")


def test_redact_replaces_value_but_keeps_context():
    text = 'API_KEY = "sk-proj-abcdefghijklmnopqrstuvwx" and more text'
    out = redact(text)
    assert "sk-proj-abcdefghijklmnopqrstuvwx" not in out
    assert "[REDACTED]" in out
    assert "and more text" in out


def test_placeholder_values_are_not_flagged():
    assert not scan("API_KEY=your-key-here")
    assert not scan("password: changeme")


def test_scan_mapping_finds_nested_secret():
    data = {"outer": {"inner": ["fine", {"token": "ghp_abcdefghijklmnopqrstuvwxyz0123"}]}}
    findings = scan_mapping(data)
    assert findings
    assert findings[0].label == "github-token"


def test_redact_mapping_preserves_shape():
    data = {"a": 1, "b": ["x", "password=supersecret123"], "c": {"d": None}}
    out = redaction.redact_mapping(data)
    assert set(out) == {"a", "b", "c"}
    assert out["a"] == 1
    assert "supersecret123" not in str(out)


def test_empty_and_plain_text_pass_through():
    assert scan("") == []
    assert redact("") == ""
    assert redact("nothing to see here") == "nothing to see here"


def test_preview_does_not_leak_the_secret():
    findings = scan("token = 'abcdef1234567890'")
    assert findings
    assert "abcdef1234567890" not in findings[0].preview


def test_finding_str_is_safe():
    f = Finding("test", 0, 4, "ab****")
    assert "test" in str(f)
