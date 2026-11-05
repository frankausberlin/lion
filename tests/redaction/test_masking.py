"""Masking tests for the redaction core."""

from __future__ import annotations

import re

from lion.redaction import SecretFinding, find_secrets, mask_text

fingerprint_pattern = re.compile(r"\[REDACTED:[a-z-]+:[0-9a-f]{12}\]")


def test_same_value_gets_same_fingerprint_token() -> None:
    """Identical secrets produce the same token."""
    text = "A_TOKEN=abcdef123456\nB_TOKEN=abcdef123456\n"
    key = b"k" * 32

    masked, fingerprinted = mask_text(text, find_secrets(text), key)

    tokens = fingerprint_pattern.findall(masked)
    assert fingerprinted
    assert len(tokens) == 2
    assert tokens[0] == tokens[1]


def test_different_values_get_different_tokens() -> None:
    """A changed value produces a different token."""
    first, _ = mask_text("TOKEN=abcdef\n", find_secrets("TOKEN=abcdef\n"), b"k" * 32)
    second, _ = mask_text("TOKEN=uvwxyz\n", find_secrets("TOKEN=uvwxyz\n"), b"k" * 32)

    assert first != second


def test_mask_is_deterministic() -> None:
    """Repeated masking with the same key yields the same result."""
    text = "TOKEN=abcdef\n"
    findings = find_secrets(text)

    assert mask_text(text, findings, b"k" * 32) == mask_text(text, findings, b"k" * 32)


def test_no_key_falls_back_to_plain_tokens() -> None:
    """Without a key the mask has no fingerprint and no raw value."""
    text = "TOKEN=abcdef\n"

    masked, fingerprinted = mask_text(text, find_secrets(text), None)

    assert masked == "TOKEN=[REDACTED:token]\n"
    assert not fingerprinted
    assert "abcdef" not in masked


def test_multiline_mask_preserves_line_count() -> None:
    """A PEM block keeps its number of lines and puts the key on the first line."""
    text = "before\n-----BEGIN PRIVATE KEY-----\nAAAA\nBBBB\n-----END PRIVATE KEY-----\nafter\n"

    masked, fingerprinted = mask_text(text, find_secrets(text), b"k" * 32)

    assert len(masked.splitlines()) == len(text.splitlines())
    lines = masked.splitlines()
    assert fingerprint_pattern.fullmatch(lines[1])
    assert lines[2] == "[REDACTED:private-key]"
    assert lines[3] == "[REDACTED:private-key]"
    assert lines[4] == "[REDACTED:private-key]"
    assert lines[0] == "before"
    assert lines[5] == "after"
    assert fingerprinted


def test_mask_accepts_lists() -> None:
    """Masking accepts a list as well as a tuple of findings."""
    finding = SecretFinding(kind="token", rule="test", start=0, end=3, confidence=70)

    masked, fingerprinted = mask_text("abc", [finding], None)

    assert masked == "[REDACTED:token]"
    assert not fingerprinted


def test_invalid_findings_are_ignored() -> None:
    """Empty or reversed spans do not change the text."""
    text = "abc"
    findings = (
        SecretFinding(kind="token", rule="test", start=2, end=2, confidence=70),
        SecretFinding(kind="token", rule="test", start=2, end=1, confidence=70),
    )

    masked, fingerprinted = mask_text(text, findings, b"k" * 32)

    assert masked == text
    assert not fingerprinted
