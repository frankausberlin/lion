"""End-to-end coverage for ``lion serve``: the real MCP server over stdio.

The installed CLI scans the container; a real ``lion serve`` subprocess then
answers MCP requests over stdin/stdout. The read scenario checks history and
snapshot against the stored entry, the wiki and registry resources, the
classifier resource and a prompt, and proves with an ``lstat`` manifest that
nothing was written. The write scenario creates an ADR through the tool, reads
it back with the CLI and checks the file mode. The container has no network and
the server must not need one.
"""

import asyncio
import json
import stat
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from e2eutil import Runner, manifest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent, TextResourceContents

pytestmark = pytest.mark.e2e

_TIMEOUT = timedelta(seconds=30)

_TOOL_NAMES = (
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


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list), value
    return cast("list[object]", value)


def _first_text(items: object) -> str:
    """Return the text of the first content item of a tool or resource result."""
    assert isinstance(items, list), items
    first = cast("list[object]", items)[0]
    if isinstance(first, TextResourceContents | TextContent):
        return first.text
    raise AssertionError(first)


def _payload(result: object) -> str:
    contents = getattr(result, "contents", None)
    if contents is None:
        contents = getattr(result, "content", None)
    return _first_text(contents)


def _json(result: object) -> dict[str, object]:
    """Parse the JSON text payload of a tool or resource result."""
    return _mapping(json.loads(_payload(result)))


def _text_resource(result: object) -> str:
    return _payload(result)


def test_serve_read_phase_serves_the_real_ledger_without_writes(runner: Runner, env: dict[str, str]) -> None:
    """Scan, then read everything over MCP; the data directory stays untouched."""
    scanned = _mapping(json.loads(runner("lion", "scan", "--json", env=env)))
    state = _mapping(scanned["state"])
    ref = Path(str(scanned["path"])).stem
    data = Path(env["XDG_DATA_HOME"])
    before = manifest(data)

    async def journey() -> dict[str, object]:
        params = StdioServerParameters(command="lion", args=["serve"], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            initialize = await session.initialize()
            tools = await session.list_tools()
            history = _json(await session.call_tool("lion_history", {}, read_timeout_seconds=_TIMEOUT.total_seconds()))
            snapshot = _json(
                await session.call_tool("lion_snapshot", {}, read_timeout_seconds=_TIMEOUT.total_seconds())
            )
            index = _text_resource(await session.read_resource("lion://wiki/index"))
            host_page = _text_resource(await session.read_resource("lion://wiki/host"))
            collectors = _json(await session.read_resource("lion://collectors"))
            ruleset = _json(await session.read_resource("lion://classifier/rules"))
            prompt = await session.get_prompt("review_latest_changes", {"reference": ref})
            message = prompt.messages[0].content
            assert isinstance(message, TextContent)
            return {
                "server": initialize.server_info.name,
                "tools": sorted(tool.name for tool in tools.tools),
                "history": history,
                "snapshot": snapshot,
                "index": index,
                "host_page": host_page,
                "collectors": collectors,
                "ruleset": ruleset,
                "prompt": message.text,
            }

    journey_result = asyncio.run(asyncio.wait_for(journey(), timeout=120))

    assert journey_result["server"] == "lion"
    assert journey_result["tools"] == list(_TOOL_NAMES)
    entries = [_mapping(item) for item in _sequence(_mapping(journey_result["history"])["entries"])]
    assert [item["ref"] for item in entries] == [ref]
    assert journey_result["snapshot"] == {"ref": ref, "snapshot": state}

    wiki = data / "lion" / "wiki"
    assert journey_result["index"] == (wiki / "index.md").read_text(encoding="utf-8")
    assert journey_result["host_page"] == (wiki / "host.md").read_text(encoding="utf-8")
    assert f"snapshot_ref: {ref}\n" in str(journey_result["index"])
    assert journey_result["collectors"] == {
        "collectors": ["host", "hardware", "network", "packages", "services", "containers", "tools", "dash"]
    }
    ruleset_payload = _mapping(journey_result["ruleset"])
    assert ruleset_payload["hash"]
    assert ruleset_payload["rules"]
    assert ref in str(journey_result["prompt"])

    assert manifest(data) == before


def test_serve_adr_write_path_roundtrips_with_the_cli(runner: Runner, env: dict[str, str]) -> None:
    """``lion_adr_add`` publishes a mode-600 ADR the CLI reads back."""
    data = Path(env["XDG_DATA_HOME"])

    async def create() -> dict[str, object]:
        params = StdioServerParameters(command="lion", args=["serve"], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            _ = await session.initialize()
            created = _json(
                await session.call_tool(
                    "lion_adr_add",
                    {"fields": {"title": "E2E decision", "decision": "Documented over MCP", "component": "serve"}},
                    read_timeout_seconds=_TIMEOUT.total_seconds(),
                )
            )
            listing = _json(await session.call_tool("lion_adr_list", {}, read_timeout_seconds=_TIMEOUT.total_seconds()))
            return {"created": created, "listing": listing}

    result = asyncio.run(asyncio.wait_for(create(), timeout=120))
    created = _mapping(result["created"])
    ref = str(created["ref"])
    path = Path(str(created["path"]))

    assert path == data / "lion" / "adrs" / f"{ref}.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    items = [_mapping(item) for item in _sequence(_mapping(result["listing"])["adrs"])]
    assert [(item["ref"], item["origin"], item["title"]) for item in items] == [(ref, "standalone", "E2E decision")]

    shown = _mapping(json.loads(runner("lion", "adr", "show", ref, "--json", env=env)))
    assert shown["fields"] == {"title": "E2E decision", "decision": "Documented over MCP", "component": "serve"}
    assert shown["complete"] is True
