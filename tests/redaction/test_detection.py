"""Detection tests for the redaction core."""

from __future__ import annotations

import pytest

from lion.redaction import find_secrets, mask_text


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
        ("ghp_" + "a" * 36, "github-token"),
        ("glpat-" + "a" * 20, "gitlab-token"),
        ("sk-" + "a" * 24, "openai-token"),
        ("xoxb-" + "1" * 12, "slack-token"),
        ("sk_live_" + "a" * 16, "stripe-token"),
        ("AIza" + "a" * 35, "google-api-key"),
        ("pypi-" + "a" * 20, "pypi-token"),
        ("npm_" + "a" * 36, "npm-token"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature123", "jwt"),
    ],
)
def test_provider_patterns_are_detected(text: str, kind: str) -> None:
    """Each provider pattern yields its stable kind."""
    findings = find_secrets(text)

    assert findings
    assert findings[0].kind == kind
    assert findings[0].confidence >= 90


def test_pem_block_is_detected_as_multiline() -> None:
    """A complete PEM private key is one multiline finding."""
    text = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB\n-----END OPENSSH PRIVATE KEY-----\n"

    findings = find_secrets(text)

    assert len(findings) == 1
    assert findings[0].kind == "private-key"
    assert findings[0].confidence == 99
    assert findings[0].multiline


def test_multiple_pem_blocks_are_separate_findings() -> None:
    """Two complete PEM blocks do not merge into one span."""
    block = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"

    findings = find_secrets(block + block)

    assert len(findings) == 2
    assert all(finding.kind == "private-key" for finding in findings)


def test_unmatched_pem_markers_are_linear() -> None:
    """A large input without END markers does not trigger a retry per BEGIN."""
    text = "-----BEGIN PRIVATE KEY-----" * 40_000

    findings = find_secrets(text)

    assert findings == ()


def test_escaped_quote_is_fully_masked() -> None:
    """An escaped quote does not end the masked span early."""
    text = 'PASSWORD="pa\\"ss"\n'

    masked, _ = mask_text(text, find_secrets(text), None)

    assert masked == 'PASSWORD="[REDACTED:password]"\n'
    assert 'pa\\"ss' not in masked


def test_adjacent_quoted_segments_are_fully_masked() -> None:
    """Adjacent quoted segments are masked as one value."""
    text = 'PASSWORD="abc""def"\n'

    masked, _ = mask_text(text, find_secrets(text), None)

    assert masked == 'PASSWORD="[REDACTED:password]"\n'
    assert "def" not in masked


def test_unquoted_continuation_is_masked_to_end_of_line() -> None:
    """A value that continues after a quoted part never exposes the rest."""
    text = 'PASSWORD="abc"def\n'

    masked, _ = mask_text(text, find_secrets(text), None)

    assert "def" not in masked
    assert masked.startswith('PASSWORD="[REDACTED:password]')


def test_trailing_comment_stays_visible() -> None:
    """A comment after a quoted value is not part of the masked span."""
    text = 'PASSWORD="abc" # note\n'

    masked, _ = mask_text(text, find_secrets(text), None)

    assert masked == 'PASSWORD="[REDACTED:password]" # note\n'


def test_url_password_is_masked_alone() -> None:
    """A URL userinfo password is masked without hiding user or host."""
    text = "url = https://user:s3cr3t@example.test/path"

    findings = find_secrets(text)
    masked, fingerprinted = mask_text(text, findings, None)

    assert not fingerprinted
    assert "user:" in masked
    assert "@example.test/path" in masked
    assert "s3cr3t" not in masked


def test_sensitive_assignment_masks_whole_value() -> None:
    """A sensitive key masks the complete value side."""
    text = "export DB_PASSWORD=hunter2\n"

    findings = find_secrets(text)
    masked, _ = mask_text(text, findings, None)

    assert findings[0].kind == "password"
    assert findings[0].key_name == "DB_PASSWORD"
    assert masked == "export DB_PASSWORD=[REDACTED:password]\n"


def test_sensitive_assignment_keeps_quotes_and_hides_trailing_comment() -> None:
    """Quoted values keep their quotes; unquoted values hide the rest of the line."""
    quoted = 'TOKEN="abc123" # note\n'
    unquoted = "TOKEN=abc123 # note\n"

    quoted_masked, _ = mask_text(quoted, find_secrets(quoted), None)
    unquoted_masked, _ = mask_text(unquoted, find_secrets(unquoted), None)

    assert quoted_masked == 'TOKEN="[REDACTED:token]" # note\n'
    assert "# note" not in unquoted_masked
    assert "abc123" not in quoted_masked
    assert "abc123" not in unquoted_masked


def test_colon_assignment_is_detected() -> None:
    """INI/YAML-like assignments are detected."""
    text = "client_secret: abc123\n"

    findings = find_secrets(text)

    assert findings[0].key_name == "client_secret"


def test_author_is_not_sensitive() -> None:
    """Word fragments do not trigger the denylist."""
    assert not find_secrets("author = John Doe\n")


@pytest.mark.parametrize(
    "value",
    ["changeme", "your_token_here", "${MY_TOKEN}", "{{ token }}", "***", "<password>"],
)
def test_placeholders_are_skipped(value: str) -> None:
    """Documented placeholders never produce findings."""
    assert not find_secrets(f"TOKEN={value}\n")


def test_entropy_only_candidate_is_detected() -> None:
    """A long random assignment without a sensitive key is a low-confidence finding."""
    findings = find_secrets("value = q8Zr2LmN4pQ7sT1vW3xY5zA9\n")

    assert findings[0].kind == "high-entropy"
    assert 40 <= findings[0].confidence <= 80


def test_short_or_plain_values_are_not_entropy_candidates() -> None:
    """Short values and natural-language values are ignored."""
    assert not find_secrets("value = hello world this is a long sentence\n")
    assert not find_secrets("value = abc\n")


def test_overlap_prefers_provider_and_keeps_whole_span() -> None:
    """A provider token inside a sensitive assignment keeps the whole span."""
    token = "ghp_" + "a" * 36
    text = f"GITHUB_TOKEN={token}\n"

    findings = find_secrets(text)

    assert len(findings) == 1
    assert findings[0].kind == "github-token"
    assert findings[0].start == len("GITHUB_TOKEN=")
    assert findings[0].end == len("GITHUB_TOKEN=") + len(token)
    assert findings[0].confidence == 95


def test_findings_do_not_store_the_value() -> None:
    """The finding repr never contains the secret."""
    text = "PASSWORD=very-secret-value\n"

    findings = find_secrets(text)

    assert "very-secret-value" not in repr(findings[0])


def test_confidence_is_bounded() -> None:
    """All confidence values are integers in the documented range."""
    for text in ("PASSWORD=abc\n", "ghp_" + "a" * 36, "value = q8Zr2LmN4pQ7sT1vW3xY5zA9\n"):
        for finding in find_secrets(text):
            assert isinstance(finding.confidence, int)
            assert 0 <= finding.confidence <= 100
