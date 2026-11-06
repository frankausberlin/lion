"""Prefix overlay parsing and loading tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import lion.secret_prefixes as prefix_module
from lion.secret_prefixes import (
    DEFAULT_PREFIXES,
    MAX_PREFIX_FILE_BYTES,
    SOURCE_BUILTIN,
    SOURCE_USER,
    PrefixConfig,
    PrefixRule,
    effective_display,
    effective_rules,
    load_prefixes,
    parse_prefixes,
)


def _parse(text: str, *, initial: tuple[PrefixRule, ...] = DEFAULT_PREFIXES) -> PrefixConfig:
    """Parse a user overlay over the defaults."""
    return parse_prefixes(text, source=SOURCE_USER, initial=initial)


def test_defaults_are_available_without_a_file(tmp_path: Path) -> None:
    """A missing overlay yields the built-in rules and no problem."""
    config = load_prefixes(tmp_path / "secret-prefixes.txt")

    assert config.rules == DEFAULT_PREFIXES
    assert config.problems == ()


def test_overlay_adds_a_user_rule() -> None:
    """A user line adds a rule with the documented defaults."""
    config = _parse("acme_ acme-token\n")

    assert config.problems == ()
    rule = next(rule for rule in config.rules if rule.prefix == "acme_")
    assert rule.kind == "acme-token"
    assert rule.min_tail == 20
    assert rule.max_tail is None
    assert rule.source == SOURCE_USER
    assert rule.rule == "prefix.acme"
    assert rule.line == 1


def test_rule_id_slug_and_defaults() -> None:
    """A line without fields uses the generic defaults and slug."""
    config = _parse("dp.st-\n")

    rule = next(rule for rule in config.rules if rule.prefix == "dp.st-")
    assert rule.kind == "prefixed-secret"
    assert rule.rule == "prefix.dpst"
    assert rule.min_tail == 20
    assert rule.max_tail is None


def test_empty_slug_becomes_custom() -> None:
    """A prefix without usable characters gets the custom slug."""
    config = _parse("___\n")

    assert config.problems == ()
    assert next(rule for rule in config.rules if rule.prefix == "___").rule == "prefix.custom"


def test_user_rule_overrides_a_default_without_a_warning() -> None:
    """A user entry replaces the same built-in prefix silently."""
    config = _parse("ghp_ my-github 24 40\n")

    matches = [rule for rule in config.rules if rule.prefix == "ghp_"]
    assert config.problems == ()
    assert len(matches) == 1
    assert matches[0].source == SOURCE_USER
    assert matches[0].kind == "my-github"
    assert matches[0].min_tail == 24
    assert matches[0].max_tail == 40


def test_disable_entry_deactivates_a_default() -> None:
    """A leading ! disables a prefix and keeps it for display."""
    config = _parse("!ghp_\n")
    active = effective_rules(config)
    disabled = [rule for rule in config.rules if rule.prefix == "ghp_"]

    assert config.problems == ()
    assert all(rule.prefix != "ghp_" for rule in active)
    assert disabled[0].active is False
    assert disabled[0].source == SOURCE_BUILTIN


def test_disable_unknown_prefix_is_a_problem() -> None:
    """Disabling a prefix that does not exist warns without a value."""
    config = _parse("!does-not-exist\n")

    assert len(config.problems) == 1
    assert config.problems[0].line == 1
    assert "unknown" in config.problems[0].message


def test_duplicate_user_prefix_keeps_the_last_one() -> None:
    """The last duplicate wins and the later line reports the problem."""
    config = _parse("acme_ first 20\nacme_ second 24\n")

    rule = next(rule for rule in config.rules if rule.prefix == "acme_")
    assert rule.kind == "second"
    assert rule.min_tail == 24
    assert len(config.problems) == 1
    assert config.problems[0].line == 2
    assert "duplicate" in config.problems[0].message


@pytest.mark.parametrize(
    "line",
    [
        "acme_ kind 20 10",
        "acme_ kind zero",
        "acme_ kind 0",
        "acme_ bad/kind",
        "acme_ kind 20 10 extra",
        "!",
    ],
)
def test_invalid_lines_are_reported_and_ignored(line: str) -> None:
    """Invalid lines produce one line problem and are skipped."""
    config = _parse(f"{line}\n")

    assert config.problems
    assert config.problems[0].line == 1
    assert all(rule.prefix not in {"acme_"} for rule in config.rules)


def test_comments_blank_lines_and_case_are_preserved() -> None:
    """Comments are ignored and prefixes stay case-sensitive."""
    config = _parse("# comment\n\nACME_ token 24\nacme_ token 24\n")

    prefixes = [rule.prefix for rule in config.rules if rule.source == SOURCE_USER]
    assert prefixes == ["ACME_", "acme_"]
    assert config.problems == ()


def test_longest_prefix_comes_first() -> None:
    """Effective rules are ordered by descending prefix length."""
    prefixes = [rule.prefix for rule in effective_rules(_parse(""))]

    assert prefixes.index("sk-proj-") < prefixes.index("sk-")
    assert prefixes.index("dckr_pat_") < len(prefixes)


def test_display_lists_active_and_disabled_entries(tmp_path: Path) -> None:
    """Display output names status, prefix, kind, lengths and source."""
    path = tmp_path / ".shlib" / "secret-prefixes.txt"
    path.parent.mkdir()
    path.write_text("!ghp_\nacme_ acme-token 24 -\n", encoding="utf-8")

    lines = effective_display(load_prefixes(path), home=tmp_path)
    text = "\n".join(lines)

    assert "prefixes: ~/.shlib/secret-prefixes.txt (overlay on builtin)" in text
    assert "active    acme_" in text
    assert "disabled  ghp_" in text
    assert "user" in text
    assert "builtin" in text


def test_problem_messages_never_include_the_line_content() -> None:
    """Invalid data and secret-looking tokens do not leak into problems."""
    secret = "ghp_aB3dE5fG7hI9jK1lM3nO5p"
    config = _parse(f"acme_ bad/kind {secret}\n")

    assert config.problems
    assert all(secret not in problem.message for problem in config.problems)


def test_symlinked_file_falls_back_to_defaults(tmp_path: Path) -> None:
    """A symlink is refused and the defaults stay active."""
    target = tmp_path / "target"
    target.write_text("acme_ acme 20\n", encoding="utf-8")
    path = tmp_path / "secret-prefixes.txt"
    path.symlink_to(target)

    config = load_prefixes(path)

    assert config.rules == DEFAULT_PREFIXES
    assert config.problems[0].line is None
    assert "symlink" in config.problems[0].message


def test_directory_at_the_path_falls_back_to_defaults(tmp_path: Path) -> None:
    """A directory is not a valid overlay."""
    path = tmp_path / "secret-prefixes.txt"
    path.mkdir()

    config = load_prefixes(path)

    assert config.rules == DEFAULT_PREFIXES
    assert "regular file" in config.problems[0].message


def test_oversized_file_falls_back_to_defaults(tmp_path: Path) -> None:
    """Files above the cap are never parsed."""
    path = tmp_path / "secret-prefixes.txt"
    path.write_bytes(b"a" * (MAX_PREFIX_FILE_BYTES + 1))

    config = load_prefixes(path)

    assert config.rules == DEFAULT_PREFIXES
    assert "64 KiB" in config.problems[0].message


def test_nul_and_invalid_utf8_fall_back_to_defaults(tmp_path: Path) -> None:
    """NUL bytes and invalid UTF-8 are rejected as invalid files."""
    nul = tmp_path / "nul"
    nul.write_bytes(b"acme_\x00\n")
    invalid = tmp_path / "invalid"
    invalid.write_bytes(b"\xff")

    assert "NUL" in load_prefixes(nul).problems[0].message
    assert "UTF-8" in load_prefixes(invalid).problems[0].message


def test_read_error_falls_back_to_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read error is a problem, not an exception."""
    path = tmp_path / "secret-prefixes.txt"
    path.write_text("acme_ acme 20\n", encoding="utf-8")

    def fail_fstat(descriptor: int) -> os.stat_result:
        raise OSError("denied")

    monkeypatch.setattr(prefix_module.os, "fstat", fail_fstat)

    config = load_prefixes(path)

    assert config.rules == DEFAULT_PREFIXES
    assert "cannot read" in config.problems[0].message


def test_prefix_module_file_limit_is_stable() -> None:
    """The documented file limit is 64 KiB."""
    assert prefix_module.MAX_PREFIX_FILE_BYTES == 64 * 1024
