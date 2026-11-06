"""Detection tests for the redaction core."""

from __future__ import annotations

from pathlib import Path

import pytest

from lion.redaction import SecretFinding, find_secrets, mask_text, merge_findings
from lion.secret_prefixes import DEFAULT_PREFIXES

_VARIED = "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5zA"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"TOKEN='AKIA{'ABCDEFGHIJKLMNOP'}'", "aws-access-key"),
        (f'TOKEN="ghp_{_VARIED}"', "github-token"),
        (f'TOKEN="glpat-{_VARIED}"', "gitlab-token"),
        (f'TOKEN="sk-{_VARIED}"', "openai-token"),
        (f'TOKEN="xoxb-{_VARIED[:10]}"', "slack-token"),
        (f'TOKEN="sk_live_{_VARIED[:16]}"', "stripe-token"),
        (f'TOKEN="AIza{_VARIED[:35]}"', "google-api-key"),
        (f'TOKEN="pypi-{_VARIED[:20]}"', "pypi-token"),
        (f'TOKEN="npm_{_VARIED[:36]}"', "npm-token"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature123", "jwt"),
    ],
)
def test_provider_patterns_are_detected(text: str, kind: str) -> None:
    """Each provider pattern yields its stable kind."""
    findings = find_secrets(text)

    assert findings
    assert findings[0].kind == kind
    assert findings[0].confidence >= 85


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


def test_free_entropy_is_not_detected() -> None:
    """A long random value without a sensitive key or prefix is not detected."""
    assert not find_secrets("value = q8Zr2LmN4pQ7sT1vW3xY5zA9\n")


def test_short_or_plain_values_are_not_entropy_candidates() -> None:
    """Short values and natural-language values are ignored."""
    assert not find_secrets("value = hello world this is a long sentence\n")
    assert not find_secrets("value = abc\n")


def test_overlap_prefers_provider_and_keeps_whole_span() -> None:
    """A provider token inside a sensitive assignment keeps the whole span."""
    token = f"ghp_{_VARIED}"
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
    for text in ("PASSWORD=abc\n", f'TOKEN="ghp_{_VARIED}"', "value = q8Zr2LmN4pQ7sT1vW3xY5zA9\n"):
        for finding in find_secrets(text):
            assert isinstance(finding.confidence, int)
            assert 0 <= finding.confidence <= 100


def test_zshrc_false_positive_fixture_has_no_findings() -> None:
    """The twelve false-positive lines from the user report stay clean."""
    fixture = Path(__file__).with_name("corpus") / "zshrc-false-positives.zsh"
    text = fixture.read_text(encoding="utf-8")

    assert find_secrets(text) == ()


@pytest.mark.parametrize(
    ("text", "kind", "rule"),
    [
        ('KEY="ghp_' + "aB3dE5fG7hI9jK1lM3nO5p" + '"\n', "github-token", "provider.github_token"),
        ("KEY='hf_" + "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tUv" + "'\n", "huggingface-token", "provider.huggingface_token"),
        ('AUTH="Bearer ghp_' + "aB3dE5fG7hI9jK1lM3nO5p" + '"\n', "github-token", "provider.github_token"),
        ("KEY=sk-proj-" + "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5zA" + "\n", "openai-token", "provider.openai_token"),
    ],
)
def test_prefix_rules_work_in_assignment_and_quote_values(text: str, kind: str, rule: str) -> None:
    """Prefixes are detected after an assignment or inside quotes."""
    findings = find_secrets(text)

    assert findings
    assert findings[0].kind == kind
    assert findings[0].rule == rule


def test_prefix_inside_a_word_is_not_a_candidate() -> None:
    """A prefix in the middle of a word is not a token."""
    assert not find_secrets('X="task-runner"\n')


def test_naked_prefix_token_is_left_to_gitleaks() -> None:
    """A prefix outside a value region is not a stage-1 finding."""
    assert not find_secrets("cmd ghp_aB3dE5fG7hI9jK1lM3nO5p\n")


def test_too_short_and_repeated_tails_are_rejected() -> None:
    """Minimum length, repetition and placeholders prevent weak candidates."""
    assert not find_secrets('X="ghp_short"\n')
    assert not find_secrets('X="ghp_' + "x" * 40 + '"\n')
    assert not find_secrets('X="ghp_' + "a" * 40 + '"\n')
    assert not find_secrets('X="ghp_A"\n')


def test_all_default_prefixes_match_with_synthetic_values() -> None:
    """Every built-in prefix yields its configured kind and rule."""
    varied = "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5zA" * 3
    for rule in DEFAULT_PREFIXES:
        length = rule.max_tail if rule.max_tail is not None else max(rule.min_tail, 24)
        text = f'VALUE="{rule.prefix}{varied[:length]}"\n'

        findings = find_secrets(text)

        assert len(findings) == 1, rule
        assert findings[0].kind == rule.kind, rule
        assert findings[0].rule == rule.rule, rule


@pytest.mark.parametrize(
    "value",
    [
        "$VAR",
        "${VAR}",
        "$(command)",
        "`command`",
        "/run/secrets/pw",
        "./relative",
        "../parent",
        "~/path",
        "https://example.test/x",
    ],
)
def test_sensitive_assignment_skips_references_paths_and_urls(value: str) -> None:
    """References, paths and URLs are not literal secret values."""
    assert not find_secrets(f"PASSWORD={value}\n")


def test_sensitive_assignment_keeps_a_phrase_value() -> None:
    """Spaces remain allowed for a sensitive key."""
    findings = find_secrets("PASSWORD=correct horse battery staple\n")

    assert findings
    assert findings[0].kind == "password"


@pytest.mark.parametrize("value", ["example", "dummy", "fake", "redacted", "none", "null", "xxxx", "0000", "****"])
def test_extended_placeholders_are_skipped(value: str) -> None:
    """Placeholder words and repeated filler never produce findings."""
    assert not find_secrets(f"TOKEN={value}\n")


def test_url_password_is_the_only_finding_for_a_database_url() -> None:
    """A URL value is not reported as a whole assignment."""
    text = "DATABASE_URL=postgres://user:pass@host/db\n"

    findings = find_secrets(text)

    assert len(findings) == 1
    assert findings[0].kind == "password"
    assert findings[0].rule == "url.userinfo_password"
    assert text[findings[0].start : findings[0].end] == "pass"


def test_merge_priority_prefers_prefix_over_gitleaks_over_assignment() -> None:
    """Rule priority is deterministic at equal positions and lengths."""
    prefix = SecretFinding(kind="github-token", rule="prefix.ghp", start=0, end=10, confidence=95)
    gitleaks = SecretFinding(kind="generic", rule="gitleaks.generic", start=0, end=10, confidence=95)
    assignment = SecretFinding(kind="token", rule="assignment.sensitive_key", start=0, end=10, confidence=95)

    assert merge_findings([assignment, gitleaks, prefix])[0].rule == "prefix.ghp"
    assert merge_findings([assignment, gitleaks])[0].rule == "gitleaks.generic"


def test_unterminated_quote_is_scanned_to_the_end() -> None:
    """An open string is not skipped silently."""
    text = 'KEY="Bearer ghp_aB3dE5fG7hI9jK1lM3nO5p\n'

    findings = find_secrets(text)

    assert findings
    assert findings[0].kind == "github-token"


def test_multiline_quoted_value_is_scanned() -> None:
    """A quoted value may span multiple lines."""
    text = 'KEY="first line\nghp_aB3dE5fG7hI9jK1lM3nO5p"\n'

    findings = find_secrets(text)

    assert findings
    assert findings[0].kind == "github-token"


def test_commit_hash_and_plain_path_are_not_findings() -> None:
    """A hex commit and a PATH assignment are not secrets."""
    assert not find_secrets("COMMIT=8f14e45fceea167a5a36dedd4bea2543\n")
    assert not find_secrets('export PATH="$HOME/.local/bin:$PATH"\n')
