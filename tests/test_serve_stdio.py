"""Real subprocess tests: ``lion serve`` speaks JSON-RPC over stdio.

Three levels. A raw JSON-RPC handshake proves that stdout carries nothing but
JSON-RPC and that the full tool/resource/prompt inventory answers. The official
MCP SDK client calls tools against a temporary ``XDG_DATA_HOME`` and proves the
ledger stays byte-identical. The FastMCP client exercises the modern protocol
era over a real stdio transport. No test touches the developer's home, scans,
or writes to the ledger.
"""

import asyncio
import json
import os
import select
import subprocess
import sys
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, cast

import pytest
import tomli_w
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from lion.program.storage import get_history_dir
from lion.state.model import Snapshot

HOST_STATE: dict[str, dict[str, object]] = {"host": {"status": "ok", "error": "", "hostname": "lion"}}

A_REF = "2026-10-05T18-00-00.000000Z"
A_ISO = "2026-10-05T18:00:00+00:00"

TOOL_NAMES = (
    "lion_adr_add",
    "lion_adr_list",
    "lion_adr_show",
    "lion_adr_update",
    "lion_classify",
    "lion_diff",
    "lion_history",
    "lion_recording",
    "lion_recordings",
    "lion_search",
    "lion_snapshot",
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disposable home and data directories for the subprocess."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("ZDOTDIR", raising=False)


def _server_command() -> list[str]:
    """The installed console script, started exactly like a client would."""
    script = Path(sys.executable).with_name("lion")
    assert script.exists(), f"missing console script: {script}"
    return [str(script), "serve"]


def _server_env() -> dict[str, str]:
    """The two variables the isolated ledger depends on.

    The MCP clients inherit only a small allowlist, so ``XDG_DATA_HOME`` must
    be passed explicitly; ``HOME`` is passed for symmetry.
    """
    return {"HOME": os.environ["HOME"], "XDG_DATA_HOME": os.environ["XDG_DATA_HOME"]}


def _write_state(ref: str, iso: str, collectors: Mapping[str, Mapping[str, object]]) -> None:
    get_history_dir().mkdir(parents=True, exist_ok=True)
    snapshot = Snapshot(created_at=iso, confirmed_at=iso, collectors={k: dict(v) for k, v in collectors.items()})
    (get_history_dir() / f"{ref}.toml").write_text(tomli_w.dumps(snapshot.to_toml_dict()))


def _tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list), value
    return cast("list[object]", value)


@contextmanager
def _server_process() -> Generator[subprocess.Popen[str]]:
    """Run ``lion serve`` with pipes; terminate it on exit."""
    process: subprocess.Popen[str] = subprocess.Popen(
        _server_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _read_lines(stream: "IO[str]", count: int, timeout: float = 60.0) -> list[str]:
    """Read ``count`` lines, failing loudly on a timeout or early EOF."""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while len(lines) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for JSON-RPC responses; got {lines!r}")
        ready, _, _ = select.select([stream], [], [], min(remaining, 1.0))
        if not ready:
            continue
        line = stream.readline()
        if line == "":
            raise AssertionError(f"server closed stdout early; got {lines!r}")
        lines.append(line)
    return lines


def _raw_request(request_id: int, method: str, params: Mapping[str, object] | None = None) -> str:
    payload: dict[str, object] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = dict(params)
    return json.dumps(payload)


def test_raw_stdio_stdout_carries_only_json_rpc() -> None:
    """Every stdout line parses as JSON-RPC and the full inventory answers."""
    requests = [
        _raw_request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "lion-tests", "version": "0"},
            },
        ),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        _raw_request(2, "tools/list"),
        _raw_request(3, "resources/list"),
        _raw_request(4, "resources/templates/list"),
        _raw_request(5, "prompts/list"),
        _raw_request(6, "ping"),
    ]
    with _server_process() as process:
        assert process.stdin is not None
        assert process.stdout is not None
        for request in requests:
            _ = process.stdin.write(request + "\n")
        process.stdin.flush()
        raw_lines = _read_lines(process.stdout, 6)

    responses: dict[int, dict[str, object]] = {}
    for line in raw_lines:
        payload = json.loads(line)
        assert payload["jsonrpc"] == "2.0", payload
        responses[int(payload["id"])] = payload

    initialize = _mapping(responses[1]["result"])
    assert _mapping(initialize["serverInfo"])["name"] == "lion"
    tools = [_mapping(item) for item in _sequence(_mapping(responses[2]["result"])["tools"])]
    assert sorted(str(tool["name"]) for tool in tools) == list(TOOL_NAMES)
    resources = [_mapping(item) for item in _sequence(_mapping(responses[3]["result"])["resources"])]
    assert sorted(str(item["uri"]) for item in resources) == [
        "lion://classifier/rules",
        "lion://collectors",
        "lion://wiki/index",
    ]
    templates = [_mapping(item) for item in _sequence(_mapping(responses[4]["result"])["resourceTemplates"])]
    assert [item["uriTemplate"] for item in templates] == ["lion://wiki/{collector}"]
    prompts = [_mapping(item) for item in _sequence(_mapping(responses[5]["result"])["prompts"])]
    assert sorted(str(item["name"]) for item in prompts) == [
        "document_decision",
        "recall_knowledge",
        "review_latest_changes",
    ]
    assert responses[6]["result"] == {}


def test_sdk_client_calls_tools_and_leaves_the_ledger_untouched(tmp_path: Path) -> None:
    """The official MCP client reads real data over stdio; nothing is written."""
    _write_state(A_REF, A_ISO, HOST_STATE)
    before = _tree(tmp_path)

    async def run() -> tuple[str, dict[str, object], dict[str, object], str, bool]:
        params = StdioServerParameters(command=_server_command()[0], args=["serve"], env=_server_env())
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            initialize = await session.initialize()
            history = await session.call_tool("lion_history", {})
            snapshot = await session.call_tool("lion_snapshot", {})
            missing = await session.call_tool("lion_recording", {"reference": "latest"})
            content = history.content[0]
            assert isinstance(content, TextContent)
            snapshot_content = snapshot.content[0]
            assert isinstance(snapshot_content, TextContent)
            error_content = missing.content[0]
            assert isinstance(error_content, TextContent)
            return (
                initialize.server_info.name,
                json.loads(content.text),
                json.loads(snapshot_content.text),
                error_content.text,
                bool(missing.is_error),
            )

    name, history, snapshot, error_text, is_error = asyncio.run(run())

    assert name == "lion"
    entries = [_mapping(item) for item in _sequence(history["entries"])]
    assert [item["ref"] for item in entries] == [A_REF]
    assert snapshot["ref"] == A_REF
    assert is_error is True
    assert "No recording stored" in error_text
    assert _tree(tmp_path) == before


def test_modern_client_over_stdio_reads_the_same() -> None:
    """The FastMCP client (modern protocol era) gets the same ledger."""
    _write_state(A_REF, A_ISO, HOST_STATE)

    async def run() -> tuple[int, object]:
        transport = StdioTransport(command=_server_command()[0], args=["serve"], env=_server_env())
        async with Client(transport) as client:
            tools = await client.list_tools()
            history = await client.call_tool("lion_history", {})
            return len(tools), history.data

    count, data = asyncio.run(run())

    assert count == 11
    entries = [_mapping(item) for item in _sequence(_mapping(data)["entries"])]
    assert [item["ref"] for item in entries] == [A_REF]
