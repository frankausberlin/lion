"""CLI tests for ``lion list-prefixes``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from lion.cli import app

runner = CliRunner()


def test_list_prefixes_shows_defaults(tmp_path: Path) -> None:
    """A home without an overlay lists the built-in rules."""
    result = runner.invoke(app, ["list-prefixes", "--home", str(tmp_path)])

    assert result.exit_code == 0
    assert "prefixes: ~/.shlib/secret-prefixes.txt (overlay on builtin)" in result.output
    assert "ghp_" in result.output
    assert "sk-proj-" in result.output
    assert "builtin" in result.output


def test_list_prefixes_shows_user_and_disabled_entries(tmp_path: Path) -> None:
    """An overlay adds active rules and can disable a default."""
    path = tmp_path / ".shlib" / "secret-prefixes.txt"
    path.parent.mkdir()
    path.write_text("!ghp_\nacme_ acme-token 24 -\n", encoding="utf-8")

    result = runner.invoke(app, ["list-prefixes", "--home", str(tmp_path)])

    assert result.exit_code == 0
    assert "disabled" in result.output
    assert "ghp_" in result.output
    assert "active" in result.output
    assert "acme_" in result.output
    assert "acme-token" in result.output
    assert "user" in result.output


def test_list_prefixes_warns_without_failing(tmp_path: Path) -> None:
    """A configuration problem goes to stderr and keeps exit 0."""
    path = tmp_path / ".shlib" / "secret-prefixes.txt"
    path.parent.mkdir()
    path.write_text("acme_ bad/kind\n", encoding="utf-8")

    result = runner.invoke(app, ["list-prefixes", "--home", str(tmp_path)])

    assert result.exit_code == 0
    assert "warning:" in result.stderr
    assert "line 1" in result.stderr
    assert "bad/kind" not in result.stderr


def test_list_prefixes_does_not_create_shlib(tmp_path: Path) -> None:
    """The read-only command never creates the shlib tree."""
    result = runner.invoke(app, ["list-prefixes", "--home", str(tmp_path)])

    assert result.exit_code == 0
    assert not (tmp_path / ".shlib").exists()
