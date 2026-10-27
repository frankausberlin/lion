"""Tests for the lion MCP server."""

from __future__ import annotations

import pytest
from mcp import Client
from mcp.types import TextContent, TextResourceContents
from typer.testing import CliRunner

from lion import server
from lion.cli import app
from lion.server import mcp

runner = CliRunner()


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio."""
    return "asyncio"


@pytest.mark.anyio
async def test_add_tool() -> None:
    """Call the add tool through the in-memory MCP client."""
    async with Client(mcp, raise_exceptions=True) as client:
        result = await client.call_tool("add", {"a": 1, "b": 2})
    assert not result.is_error
    assert result.structured_content == {"result": 3}
    assert result.content
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert block.text == "3"


@pytest.mark.anyio
async def test_greeting_resource() -> None:
    """Read the greeting resource through the in-memory MCP client."""
    async with Client(mcp, raise_exceptions=True) as client:
        result = await client.read_resource("greeting://World")
    assert result.contents
    content = result.contents[0]
    assert isinstance(content, TextResourceContents)
    assert content.text == "Hello, World!"


def test_cli_mcp_command_starts_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI mcp command forwards to the stdio server."""
    calls: list[bool] = []
    monkeypatch.setattr(server.mcp, "run", lambda: calls.append(True))
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 0
    assert calls == [True]
