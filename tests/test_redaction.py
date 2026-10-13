"""Tests for the best-effort redaction filter (:mod:`lion.program.redaction`).

The filter mirrors the zsh watch mask: the keyword gate, the ``NAME=VALUE``
variants and the ``Authorization:`` header. The environment switch is tested
explicitly; no test depends on the developer's environment.
"""

import pytest

from lion.program import redaction


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the switch state explicit for every test."""
    monkeypatch.delenv(redaction.DISABLE_ENV, raising=False)


# --- the keyword gate -------------------------------------------------------


def test_without_keyword_text_is_unchanged() -> None:
    """A command without any secret keyword is returned verbatim."""
    assert redaction.redact("export FOO=bar") == "export FOO=bar"


def test_prose_with_keyword_but_no_assignment_is_unchanged() -> None:
    """The keyword alone triggers the scan, but prose has nothing to mask."""
    assert redaction.redact("echo remember the token") == "echo remember the token"
    assert redaction.redact("grep -i password /etc/passwd") == "grep -i password /etc/passwd"


# --- assignment forms -------------------------------------------------------


def test_bare_assignment_is_masked() -> None:
    """A bare value is replaced with the sentinel."""
    assert redaction.redact("export API_KEY=bare-secret") == "export API_KEY=***"


def test_single_quoted_value_is_masked() -> None:
    """A single-quoted value including its quotes is replaced."""
    assert redaction.redact("export API_KEY='single secret'") == "export API_KEY=***"


def test_double_quoted_value_is_masked() -> None:
    """A double-quoted value with an escaped quote is replaced."""
    assert redaction.redact('export TOKEN="a \\" b"') == "export TOKEN=***"


def test_name_with_keyword_as_substring_is_masked() -> None:
    """The name may carry the keyword anywhere, case-insensitively."""
    assert redaction.redact("MY_TOKEN_X=abc") == "MY_TOKEN_X=***"
    assert redaction.redact("my_api_key=abc") == "my_api_key=***"


def test_bare_value_stops_at_a_separator() -> None:
    """The bare variant mirrors the zsh mask: it ends at ``;``/``&``/``|``."""
    assert redaction.redact("API_KEY=abc;echo done") == "API_KEY=***;echo done"


def test_equals_inside_the_value_stays_masked() -> None:
    """A base64-style value keeps every non-separator character inside the mask."""
    assert redaction.redact("CREDENTIAL=aGk=extra") == "CREDENTIAL=***"


def test_multiple_assignments_are_all_masked() -> None:
    """Every matching assignment on one line is replaced."""
    assert redaction.redact("A_TOKEN=1 B_SECRET=2") == "A_TOKEN=*** B_SECRET=***"


# --- Authorization header ---------------------------------------------------


def test_authorization_header_variants_are_masked() -> None:
    """Bearer, Basic and scheme-less authorization headers lose their value."""
    assert redaction.redact("Authorization: Bearer tok-123") == "Authorization: ***"
    assert redaction.redact("authorization: basic dXNlcg==") == "authorization: ***"
    assert redaction.redact("Authorization: tok-123") == "Authorization: ***"


def test_authorization_value_runs_to_the_next_whitespace() -> None:
    """Like the zsh mask, the header value ends at whitespace, quote included."""
    assert redaction.redact("curl -H 'Authorization: Bearer tok-123'") == "curl -H 'Authorization: ***"


def test_authorization_word_alone_is_unchanged() -> None:
    """The keyword without a header value has nothing to mask."""
    assert redaction.redact("echo authorization") == "echo authorization"


# --- the environment switch -------------------------------------------------


def test_env_switch_disables_the_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LION_SERVE_REDACT=0`` returns the original text."""
    monkeypatch.setenv(redaction.DISABLE_ENV, "0")
    assert redaction.enabled() is False
    assert redaction.redact("export API_KEY=secret") == "export API_KEY=secret"


def test_env_switch_any_other_value_keeps_the_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the exact value ``0`` disables the filter."""
    for value in ("1", "yes", ""):
        monkeypatch.setenv(redaction.DISABLE_ENV, value)
        assert redaction.enabled() is True
        assert redaction.redact("export API_KEY=secret") == "export API_KEY=***"


# --- mapping helper ---------------------------------------------------------


def test_redact_fields_masks_every_value() -> None:
    """The mapping helper redacts each value and keeps the keys."""
    fields = {"title": "Use the API_TOKEN=abc", "component": "shell", "empty": ""}
    assert redaction.redact_fields(fields) == {
        "title": "Use the API_TOKEN=***",
        "component": "shell",
        "empty": "",
    }
