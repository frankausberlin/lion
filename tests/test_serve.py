"""Contract tests for the MCP server (:mod:`lion.program.serve`).

Every tool is pinned against the CLI ``--json`` contract it mirrors: the tests
build the same ledger through the real program layer and compare the tool
payload with the CLI output. Calls go through the in-memory FastMCP client, so
registration, schemas, error transport and redaction are exercised too. The
environment is isolated via ``HOME``/``XDG_DATA_HOME``.
"""

import asyncio
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent, TextResourceContents
from typer.testing import CliRunner, Result

from lion.classify.rules import GROUP_FIELDS, load_ruleset
from lion.cli import app
from lion.command import scan as scan_command
from lion.program import adrs, prompts, recording, redaction, serve
from lion.program.storage import get_history_dir
from lion.program.wiki import get_wiki_dir
from tests.ledger_fixtures import (
    A_ISO,
    A_REF,
    B_ISO,
    B_REF,
    HOST_STATE,
    OTHER_STATE,
    add_adr,
    stopped_recording,
    two_states,
    write_state,
)

runner = CliRunner()

C_REF = "2026-10-05T22-00-00.000000Z"
C_ISO = "2026-10-05T22:00:00+00:00"

T0 = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)

COMPLETE_ADR: list[tuple[str, str]] = [
    ("comment", "#+LION: adr.title Antidote for the shell"),
    ("cmd", "echo anchor-me"),
    ("comment", "#+LION: adr.component shell"),
    ("comment", "#-LION: adr.decision Load plugins through Antidote."),
    ("comment", "#.LION: adr.status accepted"),
]

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
    """Disposable home, data and config directories with a default-on redaction switch."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.delenv(redaction.DISABLE_ENV, raising=False)


# --- harness ----------------------------------------------------------------


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def _cli_json(*args: str) -> object:
    result = _invoke(*args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _call(name: str, arguments: Mapping[str, object] | None = None, mode: str = "auto") -> object:
    async def run() -> object:
        async with Client(serve.build_server(), mode=mode) as client:
            result = await client.call_tool(name, dict(arguments or {}))
            return result.data

    return asyncio.run(run())


def _tool_error(name: str, arguments: Mapping[str, object] | None = None) -> str:
    with pytest.raises(ToolError) as info:
        _ = _call(name, arguments)
    return str(info.value)


def _resource(uri: str) -> str:
    async def run() -> str:
        async with Client(serve.build_server()) as client:
            contents = await client.read_resource(uri)
        first = contents[0]
        assert isinstance(first, TextResourceContents)
        return first.text

    return asyncio.run(run())


def _resource_error(uri: str) -> str:
    async def run() -> None:
        async with Client(serve.build_server()) as client:
            _ = await client.read_resource(uri)

    with pytest.raises(MCPError) as info:
        asyncio.run(run())
    return str(info.value)


def _prompt_text(name: str, arguments: Mapping[str, str] | None = None) -> str:
    async def run() -> str:
        async with Client(serve.build_server()) as client:
            result = await client.get_prompt(name, dict(arguments or {}))
        content = result.messages[0].content
        assert isinstance(content, TextContent)
        return content.text

    return asyncio.run(run())


def _prompt_error(name: str, arguments: Mapping[str, str] | None = None) -> str:
    async def run() -> None:
        async with Client(serve.build_server()) as client:
            _ = await client.get_prompt(name, dict(arguments or {}))

    with pytest.raises(MCPError) as info:
        asyncio.run(run())
    return str(info.value)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list), value
    return cast("list[object]", value)


def _mappings(value: object) -> list[dict[str, object]]:
    return [_mapping(item) for item in _sequence(value)]


# --- fixtures for the ledger -------------------------------------------------
#
# The builders live in tests/ledger_fixtures.py, shared with the agentic suite;
# the aliases keep this file's long-standing call sites unchanged.


def _tree(root: Path) -> dict[str, bytes]:
    """Return every file below ``root`` as path -> bytes, for read-only proofs."""
    if not root.exists():
        return {}
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _collect_once(_collectors: object) -> dict[str, dict[str, object]]:
    """A deterministic ``collect_state`` replacement for the scan command."""
    return {name: dict(section) for name, section in HOST_STATE.items()}


# --- inventory --------------------------------------------------------------


def test_tool_inventory_is_exact() -> None:
    """Exactly the planned tools register, each with a description and schema."""

    async def run() -> list[tuple[str, str, dict[str, object]]]:
        async with Client(serve.build_server()) as client:
            tools = await client.list_tools()
        return [(tool.name, tool.description or "", tool.input_schema) for tool in tools]

    tools = asyncio.run(run())

    assert sorted(name for name, _, _ in tools) == list(TOOL_NAMES)
    for name, description, schema in tools:
        assert description, name
        assert schema.get("type") == "object", name
    required = {name: schema.get("required", []) for name, _, schema in tools}
    assert required["lion_adr_add"] == ["fields"]
    assert required["lion_search"] == ["query"]
    assert required["lion_snapshot"] == []


def test_resource_and_prompt_inventory_is_exact() -> None:
    """The four resources (one templated) and the three prompts register."""

    async def run() -> tuple[list[str], list[str], list[str], str]:
        async with Client(serve.build_server(), mode="legacy") as client:
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            prompt_list = await client.list_prompts()
            initialize_result = client.initialize_result
            assert initialize_result is not None
            instructions = initialize_result.instructions or ""
        return (
            sorted(str(item.uri) for item in resources),
            [item.uri_template for item in templates],
            sorted(item.name for item in prompt_list),
            instructions,
        )

    uris, templates, names, instructions = asyncio.run(run())

    assert uris == ["lion://classifier/rules", "lion://collectors", "lion://wiki/index"]
    assert templates == ["lion://wiki/{collector}"]
    assert names == sorted(prompts.EXPECTED_PROMPTS)
    assert instructions == serve.INSTRUCTIONS
    assert "read-only" in instructions


def test_both_protocol_eras_serve_the_same_tools() -> None:
    """The modern discover handshake and the legacy initialize serve alike."""
    two_states()

    modern = _call("lion_history")
    legacy = _call("lion_history", mode="legacy")

    assert modern == legacy == _cli_json("history", "--json")
    assert _call("lion_snapshot", mode="legacy") == _call("lion_snapshot")


# --- history, snapshot, diff -------------------------------------------------


def test_history_matches_cli_json_and_trims_with_limit() -> None:
    """The history tool mirrors ``lion history --json`` and trims like ``--limit``."""
    write_state(A_REF, A_ISO, HOST_STATE)
    write_state(B_REF, B_ISO, OTHER_STATE)
    write_state(C_REF, C_ISO, HOST_STATE)

    assert _call("lion_history") == _cli_json("history", "--json")

    limited = _mapping(_call("lion_history", {"limit": 2}))
    entries = [_mapping(item) for item in _sequence(limited["entries"])]
    assert [entry["index"] for entry in entries] == [2, 3]
    assert [entry["ref"] for entry in entries] == [B_REF, C_REF]
    assert [entry["latest"] for entry in entries] == [False, True]
    assert entries[1]["confirmed_at"] == C_ISO
    assert entries[1]["path"] == str(get_history_dir() / f"{C_REF}.toml")

    assert _call("lion_history", {"limit": 99}) == _call("lion_history")
    assert "positive integer" in _tool_error("lion_history", {"limit": 0})
    assert "positive integer" in _tool_error("lion_history", {"limit": -1})


def test_history_without_entries_is_empty_not_an_error() -> None:
    """An empty history is a valid empty listing, like the CLI."""
    assert _call("lion_history") == {"entries": []}


def test_snapshot_matches_the_stored_document() -> None:
    """The snapshot tool returns the reference and the stored TOML document."""
    two_states()

    payload = _mapping(_call("lion_snapshot"))
    assert payload["ref"] == B_REF
    assert payload["snapshot"] == {
        "schema_version": 2,
        "created_at": B_ISO,
        "confirmed_at": B_ISO,
        "collectors": {"host": {"status": "ok", "error": "", "hostname": "other"}},
    }
    assert _mapping(_call("lion_snapshot", {"reference": "1"}))["ref"] == A_REF
    assert _mapping(_call("lion_snapshot", {"reference": B_REF[:22]}))["ref"] == B_REF


def test_snapshot_without_history_is_a_loud_error() -> None:
    """A missing history names the hint to scan."""
    message = _tool_error("lion_snapshot")

    assert "No state stored" in message
    assert "lion scan" in message


def test_diff_matches_cli_json() -> None:
    """The diff tool mirrors ``lion diff --json`` for both reference styles."""
    two_states()

    assert _call("lion_diff", {"reference": A_REF}) == _cli_json("diff", A_REF, "--json")
    assert _call("lion_diff", {"reference": "1", "second": "2"}) == _cli_json("diff", "1", "2", "--json")


def test_diff_requirestwo_states() -> None:
    """Fewer than two states is a loud error with the scan hint."""
    write_state(A_REF, A_ISO, HOST_STATE)

    message = _tool_error("lion_diff", {"reference": A_REF})

    assert "Fewer than two states" in message
    assert "lion scan" in message


# --- recordings and classify -------------------------------------------------


def test_recordings_matches_cli_json_and_trims_with_limit() -> None:
    """The recordings tool mirrors ``lion watch list --json`` and trims the newest."""
    first = stopped_recording([("cmd", "echo one")], note="first")
    second = stopped_recording([("cmd", "echo two")], note="second")

    assert _call("lion_recordings") == _cli_json("watch", "list", "--json")

    limited = _mapping(_call("lion_recordings", {"limit": 1}))
    items = [_mapping(item) for item in _sequence(limited["recordings"])]
    assert [item["ref"] for item in items] == [second]
    assert first != second
    assert "positive integer" in _tool_error("lion_recordings", {"limit": 0})


def test_recording_matches_cli_json() -> None:
    """The recording tool mirrors ``lion watch show REF --json`` event for event."""
    ref = stopped_recording(
        [
            ("cmd", "echo hello"),
            ("result", "0\t1.5\t2.25"),
            ("comment", "# note"),
            ("exit", "0"),
        ],
        note="a plain note",
    )

    assert _call("lion_recording", {"reference": ref}) == _cli_json("watch", "show", ref, "--json")


def test_recording_unknown_reference_is_a_loud_error() -> None:
    """An unknown recording reference lists the known recordings."""
    _ = stopped_recording([("cmd", "echo one")])

    message = _tool_error("lion_recording", {"reference": "nope"})

    assert "Unknown reference" in message
    assert "2026" in message


def test_recording_without_any_recording_names_the_hint() -> None:
    """Without any recording the error names ``lion watch start``."""
    message = _tool_error("lion_recording", {"reference": "latest"})

    assert "No recording stored" in message
    assert "lion watch start" in message


def test_classify_matches_cli_json() -> None:
    """The classify tool mirrors ``lion classify --json`` for a stored recording."""
    ref = stopped_recording([("cmd", "apt-get install -y ripgrep")])

    assert _call("lion_classify", {"reference": ref}) == _cli_json("classify", ref, "--json")


def test_classify_without_recordings_is_empty() -> None:
    """Without any recording the classification stays empty, like the CLI."""
    assert _call("lion_classify") == _cli_json("classify", "--json")


# --- ADR list and show --------------------------------------------------------


def test_adr_list_combines_both_origins_oldest_first() -> None:
    """Standalone and recording ADRs share one uniform, ordered list."""
    record_ref = stopped_recording(COMPLETE_ADR)
    standalone_ref = add_adr({"title": "Standalone title", "decision": "Because"})

    payload = _mapping(_call("lion_adr_list"))
    items = [_mapping(item) for item in _sequence(payload["adrs"])]
    listed = _mapping(_cli_json("watch", "list", "--json"))
    started_at = _mapping(_sequence(listed["recordings"])[0])["started_at"]

    assert [item["ref"] for item in items] == [record_ref, standalone_ref]
    assert [item["origin"] for item in items] == ["recording", "standalone"]
    assert items[0]["title"] == "Antidote for the shell"
    assert items[0]["status"] == "accepted"
    assert items[0]["complete"] is True
    assert items[0]["edited"] is False
    assert items[0]["at"] == started_at
    assert items[1]["title"] == "Standalone title"

    only_standalone = _mapping(_call("lion_adr_list", {"origin": "standalone"}))
    assert [item["origin"] for item in _mappings(only_standalone["adrs"])] == ["standalone"]
    only_recording = _mapping(_call("lion_adr_list", {"origin": "recording"}))
    assert [item["origin"] for item in _mappings(only_recording["adrs"])] == ["recording"]
    proposed = _mapping(_call("lion_adr_list", {"status": "proposed"}))
    assert [item["ref"] for item in _mappings(proposed["adrs"])] == [standalone_ref]
    accepted = _mapping(_call("lion_adr_list", {"status": "accepted"}))
    assert [item["ref"] for item in _mappings(accepted["adrs"])] == [record_ref]

    assert "Unknown origin" in _tool_error("lion_adr_list", {"origin": "nope"})
    assert "Unknown status" in _tool_error("lion_adr_list", {"status": "nope"})


def test_adr_show_standalone_matches_cli_plus_origin() -> None:
    """A standalone ADR keeps the ``adr show --json`` shape and adds ``origin``."""
    ref = add_adr({"title": "T", "decision": "D"})

    payload = _mapping(_call("lion_adr_show", {"reference": ref}))

    assert payload == {**_mapping(_cli_json("adr", "show", ref, "--json")), "origin": "standalone"}
    assert _mapping(_call("lion_adr_show", {"reference": "latest"}))["ref"] == ref
    assert _mapping(_call("lion_adr_show", {"reference": ref[:12]}))["ref"] == ref


def test_adr_show_recording_matches_cli_plus_origin() -> None:
    """A recording ADR keeps the ``records adrs REF --json`` shape and adds ``origin``."""
    ref = stopped_recording(COMPLETE_ADR)

    payload = _mapping(_call("lion_adr_show", {"reference": ref}))

    assert payload == {**_mapping(_cli_json("records", "adrs", ref, "--json")), "origin": "recording"}
    detail = _mapping(payload["adr"])
    assert detail["ref"] == ref
    assert _mapping(detail["fields"])["title"] == "Antidote for the shell"


def test_adr_show_recording_without_adr_reports_null() -> None:
    """A recording without an ADR yields ``adr: null`` plus its issues."""
    ref = stopped_recording([("cmd", "echo plain")])

    payload = _mapping(_call("lion_adr_show", {"reference": ref}))

    assert payload == {**_mapping(_cli_json("records", "adrs", ref, "--json")), "origin": "recording"}
    assert payload["adr"] is None


def test_adr_show_resolution_and_errors() -> None:
    """ADR ids win over recordings; unknown and empty references fail loudly."""
    record_ref = stopped_recording(COMPLETE_ADR)
    standalone_ref = add_adr({"title": "T"})

    assert _mapping(_call("lion_adr_show", {"reference": record_ref}))["origin"] == "recording"
    assert _mapping(_call("lion_adr_show", {"reference": "adr-2026"}))["ref"] == standalone_ref

    unknown = _tool_error("lion_adr_show", {"reference": "2026-01-01T00-00-00.000000Z"})
    assert "Unknown reference" in unknown
    assert standalone_ref in unknown
    assert record_ref in unknown
    assert "Empty reference" in _tool_error("lion_adr_show", {"reference": "  "})


def test_adr_show_ambiguous_prefix_is_loud() -> None:
    """An ambiguous ADR prefix is an error instead of a guess."""
    first = add_adr({"title": "One"}, now=T0)
    second = add_adr({"title": "Two"}, now=T0.replace(second=1))
    common = os.path.commonprefix([first, second])
    assert common.startswith("adr-2026-10-13")

    message = _tool_error("lion_adr_show", {"reference": common})

    assert "ambiguous" in message
    assert first in message
    assert second in message


# --- ADR writing --------------------------------------------------------------


def test_adr_add_writes_through_the_real_store() -> None:
    """The add tool publishes a standalone ADR the CLI can read back."""
    payload = _mapping(_call("lion_adr_add", {"fields": {"title": "T", "decision": "D", "status": "accepted"}}))
    ref = str(payload["ref"])
    path = Path(str(payload["path"]))

    assert path == adrs.get_adrs_dir() / f"{ref}.toml"
    shown = _mapping(_cli_json("adr", "show", ref, "--json"))
    assert shown["fields"] == {"title": "T", "decision": "D", "status": "accepted"}
    assert shown["complete"] is True

    with_recording = _mapping(
        _call("lion_adr_add", {"fields": {"title": "R"}, "recording": "2026-01-01T00-00-00.000000Z"})
    )
    shown = _mapping(_cli_json("adr", "show", str(with_recording["ref"]), "--json"))
    assert shown["recording"] == "2026-01-01T00-00-00.000000Z"

    assert "at least one field" in _tool_error("lion_adr_add", {"fields": {}})
    assert "non-empty" in _tool_error("lion_adr_add", {"fields": {"title": "   "}})


def test_adr_update_overwrites_adds_and_never_removes() -> None:
    """The update tool merges fields, stamps ``edited_at`` and keeps the rest."""
    ref = add_adr({"title": "Old", "decision": "Keep me"})

    payload = _mapping(_call("lion_adr_update", {"reference": ref, "fields": {"title": "New", "component": "shell"}}))

    assert payload["ref"] == ref
    assert payload["edited_at"]
    shown = _mapping(_cli_json("adr", "show", ref, "--json"))
    assert shown["fields"] == {"title": "New", "decision": "Keep me", "component": "shell"}
    assert shown["edited"] is True

    updated = _mapping(
        _call("lion_adr_update", {"reference": ref, "fields": {"decision": "Changed"}, "recording": A_REF})
    )
    shown = _mapping(_cli_json("adr", "show", str(updated["ref"]), "--json"))
    assert _mapping(shown["fields"])["decision"] == "Changed"
    assert shown["recording"] == A_REF

    assert "at least one field" in _tool_error("lion_adr_update", {"reference": ref, "fields": {}})
    assert "Unknown reference" in _tool_error("lion_adr_update", {"reference": "adr-2000", "fields": {"title": "X"}})


def test_adr_write_rejects_invalid_recording_without_touching_the_store() -> None:
    """A whitespace-only recording is loud and never corrupts or creates a document."""
    assert "recording must be a non-empty string" in _tool_error(
        "lion_adr_add", {"fields": {"title": "T"}, "recording": "   "}
    )
    assert list(adrs.get_adrs_dir().iterdir()) == []

    ref = add_adr({"title": "Old", "decision": "Keep me"})
    path = adrs.get_adrs_dir() / f"{ref}.toml"
    before = path.read_bytes()

    assert "recording must be a non-empty string" in _tool_error(
        "lion_adr_update", {"reference": ref, "fields": {"title": "New"}, "recording": "   "}
    )
    assert path.read_bytes() == before
    shown = _mapping(_cli_json("adr", "show", ref, "--json"))
    assert shown["fields"] == {"title": "Old", "decision": "Keep me"}


# --- search -------------------------------------------------------------------


def test_search_finds_adr_fields_and_recording_texts() -> None:
    """Search covers both ADR origins and the note, command and comment texts."""
    record_ref = stopped_recording(
        [
            ("cmd", "firefox --new-window"),
            ("comment", "# firefox shortcut"),
        ],
        note="the firefox note",
    )
    standalone_ref = add_adr({"title": "Browser", "decision": "Firefox stays the default"})

    payload = _mapping(_call("lion_search", {"query": "firefox"}))
    hits = [_mapping(hit) for hit in _sequence(payload["hits"])]

    assert payload["query"] == "firefox"
    kinds = {(hit["kind"], hit.get("origin"), hit["where"]) for hit in hits}
    assert ("adr", "standalone", "field:decision") in kinds
    assert ("recording", None, "note") in kinds
    assert ("recording", None, "command") in kinds
    assert ("recording", None, "comment") in kinds
    assert {hit["ref"] for hit in hits} == {record_ref, standalone_ref}
    for hit in hits:
        assert "firefox" in str(hit["snippet"]).lower()

    limited = _mapping(_call("lion_search", {"query": "firefox", "limit": 1}))
    assert len(_sequence(limited["hits"])) == 1
    assert _sequence(_mapping(_call("lion_search", {"query": "nothing-matches"}))["hits"]) == []


def test_search_is_case_insensitive_and_newest_first() -> None:
    """Search matches case-insensitively and orders hits newest first."""
    _ = stopped_recording([("cmd", "echo ALPHA")])
    _ = stopped_recording([("cmd", "echo alpha")])

    hits = [_mapping(hit) for hit in _sequence(_mapping(_call("lion_search", {"query": "alpha"}))["hits"])]

    assert len(hits) == 2
    assert str(hits[0]["ref"]) > str(hits[1]["ref"])


def test_search_rejects_empty_query_and_bad_limit() -> None:
    """An empty query and a non-positive limit are loud errors."""
    assert "Empty query" in _tool_error("lion_search", {"query": "   "})
    assert "positive integer" in _tool_error("lion_search", {"query": "x", "limit": 0})


# --- redaction ----------------------------------------------------------------


def test_redaction_masks_recording_adr_and_search_texts() -> None:
    """Recorded secrets disappear from every read surface while bytes stay raw."""
    ref = stopped_recording(
        [
            ("cmd", "export API_KEY=super-secret-value"),
            ("comment", 'curl -H "Authorization: Bearer tok-123"'),
        ],
        note="rotate API_TOKEN=note-secret",
    )
    _ = _call(
        "lion_adr_add",
        {"fields": {"title": "T", "decision": "Set DB_PASSWORD=hunter2", "api_key": "sk-live-abc"}},
    )

    recording_payload = json.dumps(_call("lion_recording", {"reference": ref}))
    assert "super-secret-value" not in recording_payload
    assert "tok-123" not in recording_payload
    assert "note-secret" not in recording_payload
    assert "API_KEY=***" in recording_payload

    assert "note-secret" not in json.dumps(_call("lion_recordings"))

    classify_payload = json.dumps(_call("lion_classify", {"reference": ref}))
    assert "super-secret-value" not in classify_payload

    show_payload = json.dumps(_call("lion_adr_show", {"reference": "latest"}))
    assert "hunter2" not in show_payload
    assert "sk-live-abc" not in show_payload
    assert "DB_PASSWORD=***" in show_payload
    assert '"api_key": "***"' in show_payload

    search_payload = json.dumps(_call("lion_search", {"query": "secret"}))
    assert "super-secret-value" not in search_payload
    assert "note-secret" not in search_payload

    key_hits = [_mapping(hit) for hit in _sequence(_mapping(_call("lion_search", {"query": "sk-live"}))["hits"])]
    assert key_hits
    assert all(hit["snippet"] == "***" for hit in key_hits)
    assert "sk-live-abc" not in json.dumps(key_hits)

    raw = (recording.get_recordings_dir() / ref / "events" / "100-1-1.log").read_text(encoding="utf-8")
    assert "super-secret-value" in raw
    meta = (recording.get_recordings_dir() / ref / "meta.toml").read_text(encoding="utf-8")
    assert "note-secret" in meta


def test_search_masks_multiline_values_with_their_context() -> None:
    """A match inside a value spanning lines keeps the masking context."""
    _ = add_adr({"title": "Keys", "notes": "API_KEY='first line\nSYNTHETIC-SECRET'"})

    shown = _mapping(_call("lion_adr_show", {"reference": "latest"}))
    assert _mapping(shown["fields"])["notes"] == "API_KEY=***"

    hits = [_mapping(hit) for hit in _sequence(_mapping(_call("lion_search", {"query": "SYNTHETIC"}))["hits"])]

    assert hits
    assert all(hit["snippet"] == "API_KEY=***" for hit in hits)
    assert "SYNTHETIC-SECRET" not in json.dumps(hits)


def test_redaction_switch_disables_the_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LION_SERVE_REDACT=0`` returns the recorded text verbatim."""
    ref = stopped_recording([("cmd", "export API_KEY=visible-value")])
    monkeypatch.setenv(redaction.DISABLE_ENV, "0")

    payload = json.dumps(_call("lion_recording", {"reference": ref}))

    assert "visible-value" in payload
    assert "***" not in payload


def test_snapshots_are_never_redacted() -> None:
    """Collector facts stay untouched even when they contain keyword patterns."""
    write_state(A_REF, A_ISO, {"host": {"status": "ok", "error": "", "hostname": "API_KEY=abc"}})

    payload = _mapping(_call("lion_snapshot", {"reference": A_REF}))

    assert _mapping(payload["snapshot"])["collectors"] == {
        "host": {"status": "ok", "error": "", "hostname": "API_KEY=abc"}
    }


# --- resources ----------------------------------------------------------------


def test_wiki_resources_read_the_derived_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a scan the wiki resources serve the derived pages byte for byte."""
    monkeypatch.setattr(scan_command, "collect_state", _collect_once)
    assert _invoke("scan").exit_code == 0

    assert _resource("lion://wiki/index") == (get_wiki_dir() / "index.md").read_text(encoding="utf-8")
    assert _resource("lion://wiki/host") == (get_wiki_dir() / "host.md").read_text(encoding="utf-8")


def test_wiki_resources_without_a_scan_fail_with_hint() -> None:
    """A missing wiki page is a resource error naming the scan hint."""
    assert "lion scan" in _resource_error("lion://wiki/index")

    unknown = _resource_error("lion://wiki/nope")
    assert "Unknown collector" in unknown
    assert "host" in unknown


def test_collectors_resource_lists_the_registry_order() -> None:
    """The collectors resource is JSON with the canonical order."""
    payload = json.loads(_resource("lion://collectors"))

    assert payload == {
        "collectors": ["host", "hardware", "network", "packages", "services", "containers", "tools", "dash"]
    }


def test_classifier_rules_resource_serves_the_merged_ruleset() -> None:
    """The classifier resource is JSON with hash, groups and rule provenance."""
    payload = json.loads(_resource("lion://classifier/rules"))
    ruleset = load_ruleset()

    assert payload["hash"] == ruleset.hash
    assert payload["version"] == ruleset.version
    assert payload["user_loaded"] is False
    assert payload["groups"] == sorted(GROUP_FIELDS)
    assert len(payload["rules"]) == len(ruleset.rules)
    for rule, source in zip(payload["rules"], ruleset.rules, strict=True):
        assert rule["id"] == source.id
        assert rule["group"] == source.group
        assert rule["source"] == source.source


# --- prompts ------------------------------------------------------------------


def test_prompts_render_with_defaults_and_arguments() -> None:
    """The three prompts render from the shipped templates."""
    review = _prompt_text("review_latest_changes")
    assert "between the stored state previous and the latest state" in review
    assert "no comparison baseline" in review
    assert "Keep the review concise and actionable." in review

    focused = _prompt_text(
        "review_latest_changes",
        {"reference": "2026-10-05T20-00-00.000000Z", "focus": "Focus on packages."},
    )
    assert "2026-10-05T20-00-00.000000Z" in focused
    assert "Focus on packages." in focused

    document = _prompt_text("document_decision", {"subject": "the default browser"})
    assert "the default browser" in document
    assert "No related recording was named." in document

    related = _prompt_text("document_decision", {"subject": "x", "recording": "2026-10-05T20-00-00.000000Z"})
    assert "Related recording: 2026-10-05T20-00-00.000000Z" in related

    recall = _prompt_text("recall_knowledge", {"query": "Why Firefox?"})
    assert "Why Firefox?" in recall


def test_review_prompt_default_reference_is_a_real_baseline() -> None:
    """The default review reference compares two states instead of the latest with itself."""
    two_states()

    review = _prompt_text("review_latest_changes")
    assert "previous" in review

    payload = _mapping(_call("lion_diff", {"reference": "previous"}))
    assert _mapping(payload["from"])["ref"] == A_REF
    assert _mapping(payload["to"])["ref"] == B_REF
    assert payload["changed"] is True


def test_prompt_metadata_comes_from_the_files() -> None:
    """Descriptions and argument metadata mirror the parsed templates."""
    templates = prompts.load_prompt_templates()

    async def run() -> list[tuple[str, str, list[tuple[str, str | None, bool]]]]:
        async with Client(serve.build_server()) as client:
            prompt_list = await client.list_prompts()
        return [
            (
                item.name,
                item.description or "",
                [(argument.name, argument.description, bool(argument.required)) for argument in item.arguments or []],
            )
            for item in prompt_list
        ]

    for name, description, arguments in asyncio.run(run()):
        template = templates[name]
        assert description == template.description
        assert arguments == [
            (argument.name, argument.description, argument.required) for argument in template.arguments
        ]


def test_prompt_missing_required_argument_is_loud() -> None:
    """A required prompt argument must be provided by the client."""
    message = _prompt_error("recall_knowledge")

    assert "Missing required arguments" in message
    assert "query" in message


# --- read-only proof ----------------------------------------------------------


def test_read_surface_leaves_every_file_untouched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every read tool, resource and prompt leaves home and data byte-identical."""
    monkeypatch.setattr(scan_command, "collect_state", _collect_once)
    assert _invoke("scan").exit_code == 0
    write_state(B_REF, B_ISO, OTHER_STATE)
    record_ref = stopped_recording([("cmd", "apt-get install -y ripgrep")])
    _ = add_adr({"title": "T", "decision": "D"})
    before = _tree(tmp_path)

    assert _call("lion_history")
    assert _call("lion_snapshot")
    assert _call("lion_diff", {"reference": B_REF})
    assert _call("lion_recordings")
    assert _call("lion_recording", {"reference": record_ref})
    assert _call("lion_classify", {"reference": record_ref})
    assert _call("lion_adr_list")
    assert _call("lion_adr_show", {"reference": "latest"})
    assert _call("lion_search", {"query": "ripgrep"})
    _ = _resource("lion://wiki/index")
    _ = _resource("lion://wiki/host")
    _ = _resource("lion://collectors")
    _ = _resource("lion://classifier/rules")
    _ = _prompt_text("review_latest_changes")
    _ = _prompt_text("recall_knowledge", {"query": "x"})

    assert _tree(tmp_path) == before
