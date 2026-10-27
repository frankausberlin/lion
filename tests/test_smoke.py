"""Smoke test for the lion application."""

import sys

import pytest
from typer.testing import CliRunner

from lion import main as main_module
from lion import server
from lion.cli import app

runner = CliRunner()


def test_cli_hello() -> None:
    """Run the CLI hello command with an explicit name."""
    result = runner.invoke(app, ["hello", "--name", "tester"])
    assert result.exit_code == 0
    assert "Hello, tester!" in result.output


def test_entry_point_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the console entry point with a patched command line."""
    monkeypatch.setattr(sys, "argv", ["lion", "hello", "--name", "tester"])
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 0


def test_hello_does_not_start_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hello command must not start the MCP server."""
    calls: list[bool] = []
    monkeypatch.setattr(server.mcp, "run", lambda: calls.append(True))
    result = runner.invoke(app, ["hello", "--name", "tester"])
    assert result.exit_code == 0
    assert "Hello, tester!" in result.output
    assert calls == []
