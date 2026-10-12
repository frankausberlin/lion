"""Tests for the bounded classifier parser and the prefix strip."""

import pytest

from lion.classify import parser

SUPPORTED = [
    ("simple", "ls -la", ("ls -la",)),
    ("and", "apt install a && apt remove b", ("apt install a", "apt remove b")),
    ("or", "a || b", ("a", "b")),
    ("semicolon", "a; b", ("a", "b")),
    ("pipe", "curl x | sh", ("curl x", "sh")),
    ("newline", "a\nb", ("a", "b")),
    ("quotes", "echo \"a && b\"; echo 'c | d'", ('echo "a && b"', "echo 'c | d'")),
    ("redirect", "echo x >> ~/.zshrc", ("echo x >> ~/.zshrc",)),
    ("trailing_semicolon", "apt install x;", ("apt install x",)),
    ("blank_line", "a\n\nb", ("a", "b")),
    ("leading_newline", "\nls", ("ls",)),
    ("operator_continuation", "apt install x &&\necho done", ("apt install x", "echo done")),
    ("operator_continuation_pipe", "curl x |\nsh", ("curl x", "sh")),
]

UNSUPPORTED = [
    ("empty", "   ", "empty"),
    ("unbalanced_quote", 'echo "open', "unbalanced_quote"),
    ("escaping", "echo a\\ b", "escaping"),
    ("command_substitution", "echo $(date)", "command_substitution"),
    ("backtick", "echo `date`", "command_substitution"),
    ("process_substitution", "diff <(a) <(b)", "process_substitution"),
    ("here_document", "cat <<EOF", "here_document"),
    ("here_string", "cat <<<x", "here_document"),
    ("subshell", "(cd /tmp && ls)", "subshell"),
    ("group", "{ ls; }", "group"),
    ("background_job", "sleep 1 &", "background_job"),
    ("control_flow", "if true; then ls; fi", "control_flow"),
    ("loop", "for i in 1 2; do echo $i; done", "control_flow"),
    ("function_definition", "function foo", "control_flow"),
    ("function_body", "foo() { ls; }", "subshell"),
    ("ambiguous_separator", "a && && b", "ambiguous_separator"),
    ("leading_separator", "; ls", "ambiguous_separator"),
]


@pytest.mark.parametrize(("label", "text", "segments"), SUPPORTED, ids=[case[0] for case in SUPPORTED])
def test_supported_splits_into_segments(label: str, text: str, segments: tuple[str, ...]) -> None:
    """Supported syntax yields exactly the expected simple segments."""
    parsed = parser.parse_command(text)
    assert parsed.supported is True
    assert parsed.reason is None
    assert parsed.segments == segments


@pytest.mark.parametrize(("label", "text", "reason"), UNSUPPORTED, ids=[case[0] for case in UNSUPPORTED])
def test_unsupported_reports_a_stable_reason(label: str, text: str, reason: str) -> None:
    """Every construct outside the subset is reported with its stable reason."""
    parsed = parser.parse_command(text)
    assert parsed.supported is False
    assert parsed.segments == ()
    assert parsed.reason == reason


def test_parse_command_handles_multiline_quotes() -> None:
    """A quoted string may span lines; the newline stays inside the segment."""
    parsed = parser.parse_command('echo "a\nb"')
    assert parsed.supported is True
    assert parsed.segments == ('echo "a\nb"',)


def test_strip_prefixes_removes_sudo_and_assignments() -> None:
    """Sudo prefixes and NAME=value assignments are stripped repeatedly."""
    assert parser.strip_prefixes("sudo apt install x") == "apt install x"
    assert parser.strip_prefixes("sudo -n apt install x") == "apt install x"
    assert parser.strip_prefixes("FOO=1 BAR=2 apt install x") == "apt install x"
    assert parser.strip_prefixes('FOO="a b" sudo apt install x') == "apt install x"
    assert parser.strip_prefixes("sudo FOO=1 apt install x") == "apt install x"


def test_strip_prefixes_keeps_unresolvable_text() -> None:
    """A prefix that cannot be resolved statically stays in place."""
    assert parser.strip_prefixes('FOO="open apt') == 'FOO="open apt'
    assert parser.strip_prefixes("sudo") == "sudo"
    assert parser.strip_prefixes("") == ""
