from tokenhub.security.redaction import redact_sensitive


def test_redactor_hides_bearer_and_openai_style_keys() -> None:
    """Fails if diagnostics leak common authorization credentials."""
    value = "Authorization: Bearer secret-value OPENAI_API_KEY=sk-abcdef1234567890"

    redacted = redact_sensitive(value)

    assert "secret-value" not in redacted
    assert "sk-abcdef1234567890" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_redactor_preserves_unrelated_diagnostic_text() -> None:
    """Fails if redaction removes useful non-sensitive context."""
    value = "Could not parse source-a at line 12"

    assert redact_sensitive(value) == value
