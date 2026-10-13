"""Deterministic regression tests for the agentic QA harness (no Ollama, no network).

The opt-in suite against a local model is advisory; this module pins the
harness semantics that must hold regardless of the model: the attempt/report
contract, the scenario predicates, the recovery metrics, the message fidelity,
the execution budget's termination reasons and the comparability metadata. The
real scenarios and checks come from :mod:`tests.agentic.suite`; the session and
the chat are injected fakes built from ``mcp.types`` objects, so nothing here
starts a server or calls Ollama.
"""

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from pathlib import Path
from typing import cast

import httpx
import pytest
from mcp import StdioServerParameters
from mcp.types import CallToolResult, GetPromptResult, PromptMessage, TextContent, Tool

from lion.program import adrs
from tests.agentic import agent as agent_module
from tests.agentic import ledger, suite
from tests.agentic.agent import (
    AgentEnvironment,
    AgentError,
    AgentReport,
    AttemptOutcome,
    AttemptRecord,
    ChatFunction,
    ChatReply,
    ChatTimeoutError,
    ChatTransportError,
    OllamaModel,
    OllamaSettings,
    Scenario,
    ScenarioCheck,
    ScenarioRun,
    SessionFactory,
    SessionLike,
    ToolCall,
    run_attempt,
)
from tests.ledger_fixtures import A_REF, B_REF, add_adr

_TOOLS: tuple[Tool, ...] = (
    Tool(name="lion_diff", description="Compare two stored states.", input_schema={"type": "object", "properties": {}}),
    Tool(name="lion_history", description="List stored states.", input_schema={"type": "object", "properties": {}}),
    Tool(name="lion_recordings", description="List recordings.", input_schema={"type": "object", "properties": {}}),
)

Result = CallToolResult | str | Exception
Handler = Callable[[dict[str, object]], Result]


def _constant_handler(result: Result) -> Handler:
    def handle(arguments: dict[str, object]) -> Result:
        return result

    return handle


# --- fakes --------------------------------------------------------------------


def _error_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


class FakeSession:
    """The session surface with scripted results; records every executed call."""

    def __init__(self, handlers: Mapping[str, Handler | Result] | None = None) -> None:
        """Bind the per-tool handlers (or constant results) and start the call log."""
        self.handlers: dict[str, Handler] = {
            name: (value if callable(value) else _constant_handler(value)) for name, value in (handlers or {}).items()
        }
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, name: str, arguments: dict[str, object] | None = None) -> CallToolResult:
        """Record the call and return the scripted handler result."""
        payload = dict(arguments or {})
        self.calls.append((name, payload))
        handler = self.handlers.get(name)
        result = handler(payload) if handler is not None else "{}"
        if isinstance(result, Exception):
            raise result
        if isinstance(result, CallToolResult):
            return result
        return CallToolResult(content=[TextContent(type="text", text=result)], is_error=False)

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
        """Return a rendered prompt body for the requested prompt name."""
        return GetPromptResult(
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=f"rendered {name}"))]
        )


class ScriptedChat:
    """A chat function that replays scripted replies and records every request."""

    def __init__(self, replies: Sequence[ChatReply | Exception]) -> None:
        """Bind the reply script and start the request log."""
        self.replies = list(replies)
        self.requests: list[list[dict[str, object]]] = []

    async def __call__(
        self,
        settings: OllamaSettings,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        timeout: float,
    ) -> ChatReply:
        """Record the request and return (or raise) the next scripted reply."""
        self.requests.append([dict(message) for message in messages])
        if not self.replies:
            raise AssertionError("the model was called more often than scripted")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _tool_call(
    name: str,
    arguments: Mapping[str, object] | None = None,
    *,
    arguments_raw: object | None = None,
    normalized: bool = False,
) -> ToolCall:
    resolved = dict(arguments or {})
    raw = resolved if arguments_raw is None else arguments_raw
    return ToolCall(
        name=name,
        arguments=resolved,
        arguments_raw=raw,
        normalized=normalized,
        raw={"id": f"call_{name}", "function": {"name": name, "arguments": raw}},
    )


def _reply(
    *,
    content: str = "",
    thinking: str = "",
    tool_calls: Sequence[ToolCall] = (),
    done_reason: str = "stop",
) -> ChatReply:
    return ChatReply(
        content=content,
        thinking=thinking,
        tool_calls=tuple(tool_calls),
        prompt_eval_count=1,
        eval_count=1,
        done_reason=done_reason,
    )


def _factory(session: SessionLike) -> SessionFactory:
    async def factory(stack: AsyncExitStack, params: StdioServerParameters) -> tuple[SessionLike, list[Tool]]:
        return session, list(_TOOLS)

    return factory


def _failing_factory(exc: Exception) -> SessionFactory:
    async def factory(stack: AsyncExitStack, params: StdioServerParameters) -> tuple[SessionLike, list[Tool]]:
        raise exc

    return factory


def _env(
    root: Path,
    *,
    chat: ChatFunction,
    session: SessionLike | None = None,
    session_factory: SessionFactory | None = None,
) -> AgentEnvironment:
    data = root / "data"
    home = root / "home"
    data.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    return AgentEnvironment(
        data_home=data,
        server_env={"HOME": str(home), "XDG_DATA_HOME": str(data)},
        settings=OllamaSettings(),
        artifacts=root / "artifacts",
        system_prompt=agent_module.BUILTIN_SYSTEM_PROMPT,
        session_factory=_factory(session if session is not None else FakeSession())
        if session_factory is None
        else session_factory,
        chat_function=chat,
    )


def _one_tool_env(root: Path, name: str = "lion_history", payload: str = "{}") -> AgentEnvironment:
    """An environment whose scripted model calls one tool once and then finishes."""
    session = FakeSession({name: payload})
    chat = ScriptedChat([_reply(tool_calls=(_tool_call(name),)), _reply(content="done")])
    return _env(root, chat=chat, session=session)


def _report(tmp_path: Path) -> AgentReport:
    report = AgentReport(tmp_path / "report.json", OllamaSettings())
    report.set_model(OllamaModel(name="fake", digest="deadbeef", capabilities=("tools",)))
    return report


def _scenario(
    name: str,
    *,
    required_tools: tuple[str, ...] = (),
    required_any: tuple[str, ...] = (),
    check: ScenarioCheck | None = None,
) -> Scenario:
    return Scenario(
        name=name,
        request=f"local regression scenario {name}",
        required_tools=required_tools,
        required_any=required_any,
        check=check,
    )


def _refuse(run: ScenarioRun) -> None:
    raise AssertionError("deliberate regression failure")


def _load(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def _section(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    assert isinstance(value, dict), f"{key} is not an object: {value!r}"
    return cast("dict[str, object]", value)


def _entries(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload.get("entries")
    assert isinstance(raw, list), f"entries is not a list: {raw!r}"
    return [cast("dict[str, object]", item) for item in cast("list[object]", raw)]


_DIFF_PAYLOAD = {"from": {"ref": A_REF}, "to": {"ref": B_REF}, "changed": True, "changes": [{"kind": "added"}]}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disposable home, data and config directories; no test touches the real ledger."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("LION_AGENT_SYSTEM_PROMPT_FILE", raising=False)
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)


# --- 1. report contract -------------------------------------------------------


def test_report_records_one_outcome_per_attempt(tmp_path: Path) -> None:
    """Checkpoint per attempt, exactly one outcome each, atomic files, final states."""
    report_path = tmp_path / "report.json"
    report = _report(tmp_path)
    passing = _scenario("report-pass", required_tools=("lion_history",))

    first = run_attempt(passing, _one_tool_env(tmp_path / "one"), report, hard=True)
    assert first.outcome is AttemptOutcome.PASSED
    checkpoint = _load(report_path)
    assert _section(checkpoint, "run")["state"] == "running"
    assert [entry["outcome"] for entry in _entries(checkpoint)] == ["passed"]

    failing = _scenario("report-fail", required_tools=("lion_history",), check=_refuse)
    with pytest.raises(AssertionError, match="deliberate regression failure"):
        run_attempt(failing, _one_tool_env(tmp_path / "two"), report, hard=True)
    after_failure = _load(report_path)
    assert [entry["outcome"] for entry in _entries(after_failure)] == ["passed", "failed"]
    assert _entries(after_failure)[1]["termination"] == "completed"

    broken = _env(
        tmp_path / "three", chat=ScriptedChat([]), session_factory=_failing_factory(RuntimeError("no server"))
    )
    errored = run_attempt(_scenario("report-error", required_tools=("lion_history",)), broken, report, hard=False)
    assert errored.outcome is AttemptOutcome.ERROR
    assert errored.termination == "startup_error"
    after_error = _load(report_path)
    assert [entry["outcome"] for entry in _entries(after_error)] == ["passed", "failed", "error"]
    assert list(tmp_path.glob("**/*.tmp")) == []

    report.finalize()
    final = _load(report_path)
    assert _section(final, "run")["state"] == "complete"
    assert _section(final, "metrics")["attempts"] == {
        "total": 3,
        "passed": 1,
        "failed": 1,
        "error": 1,
        "hard": 2,
        "report_only": 1,
    }


def test_report_registers_the_attempt_before_it_starts(tmp_path: Path) -> None:
    """The checkpoint before the scenario carries the in-flight attempt."""
    report = _report(tmp_path)
    report_path = tmp_path / "report.json"
    during: list[dict[str, object]] = []

    async def chat(
        settings: OllamaSettings,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        timeout: float,
    ) -> ChatReply:
        during.append(_load(report_path))
        return _reply(content="done")

    record = run_attempt(_scenario("in-flight"), _env(tmp_path, chat=chat), report, hard=True)

    assert record.outcome is AttemptOutcome.PASSED
    assert _section(during[0], "run")["state"] == "running"
    pending = _section(during[0], "pending")
    assert pending["name"] == "in-flight"
    assert pending["hard"] is True
    assert pending["started_at"] == record.started_at

    after = _load(report_path)
    assert after["pending"] is None
    assert [entry["outcome"] for entry in _entries(after)] == ["passed"]


def test_interrupted_report_stays_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A KeyboardInterrupt mid-attempt leaves an interrupted report with the attempt."""
    report_path = tmp_path / "report.json"
    report = _report(tmp_path)

    def interrupt(*args: object, **kwargs: object) -> ScenarioRun:
        raise KeyboardInterrupt

    monkeypatch.setattr(agent_module, "run_scenario", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_attempt(
            _scenario("interrupted", required_tools=("lion_history",)), _one_tool_env(tmp_path), report, hard=True
        )
    report.finalize()

    payload = _load(report_path)
    assert _section(payload, "run")["state"] == "interrupted"
    entries = _entries(payload)
    assert len(entries) == 1
    assert entries[0]["outcome"] == "error"
    assert entries[0]["termination"] == "interrupted"
    assert entries[0]["failure"] == "attempt interrupted before completion"


def test_preflight_error_report_has_no_attempts(tmp_path: Path) -> None:
    """A preflight failure produces a finished report without invented attempts."""
    report = _report(tmp_path)
    report.mark_preflight_error("Ollama is not reachable")

    payload = _load(tmp_path / "report.json")
    assert _section(payload, "run")["state"] == "preflight_error"
    assert _section(payload, "run")["finished_at"] is not None
    assert _entries(payload) == []
    assert _section(payload, "extras")["preflight_error"] == "Ollama is not reachable"


# --- 2. recovery --------------------------------------------------------------


def test_recovery_after_failed_target_call(tmp_path: Path) -> None:
    """A failed first target call followed by a successful retry passes as recovered."""
    calls = {"count": 0}

    def diff_handler(arguments: dict[str, object]) -> CallToolResult | str:
        calls["count"] += 1
        if calls["count"] == 1:
            return _error_result("No state stored")
        return json.dumps(_DIFF_PAYLOAD)

    session = FakeSession({"lion_diff": diff_handler})
    chat = ScriptedChat(
        [
            _reply(tool_calls=(_tool_call("lion_diff", {"reference": "1"}),)),
            _reply(tool_calls=(_tool_call("lion_diff", {"reference": "1"}),)),
            _reply(content="compared"),
        ]
    )
    report = _report(tmp_path)
    record = run_attempt(suite.SCENARIO_DIFF, _env(tmp_path, chat=chat, session=session), report, hard=True)

    assert record.outcome is AttemptOutcome.PASSED
    assert record.first_attempt_success is False
    assert record.failed_target_calls == 1
    assert record.retries == 1
    assert record.recovered is True
    assert _section(_load(tmp_path / "report.json"), "metrics")["recovered_success"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }


def test_failed_attempt_records_recovery_metrics(tmp_path: Path) -> None:
    """A scenario that dies after a failed target call carries its recovery fields."""
    report = _report(tmp_path)
    session = FakeSession({"lion_history": _error_result("No state stored")})
    chat = ScriptedChat(
        [
            _reply(tool_calls=(_tool_call("lion_history"),)),
            ChatTimeoutError("the chat timed out"),
        ]
    )
    record = run_attempt(suite.SCENARIO_HISTORY, _env(tmp_path, chat=chat, session=session), report, hard=False)

    assert record.outcome is AttemptOutcome.ERROR
    assert record.termination == "chat_timeout"
    assert record.first_attempt_success is False
    assert record.failed_target_calls == 1
    assert record.retries == 0
    assert record.recovered is False


# --- 3. diff predicate --------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"from": {"ref": B_REF}, "to": {"ref": A_REF}, "changed": True},
        {"from": {"ref": A_REF}, "to": {"ref": "2026-01-01T00-00-00.000000Z"}, "changed": True},
        {"from": {"ref": A_REF}, "to": {"ref": B_REF}, "changed": False},
    ],
    ids=["wrong-direction", "wrong-reference", "changed-false"],
)
def test_diff_check_rejects_wrong_results(tmp_path: Path, payload: dict[str, object]) -> None:
    """A successful diff with the wrong direction, reference or flag fails the task."""
    session = FakeSession({"lion_diff": json.dumps(payload)})
    chat = ScriptedChat([_reply(tool_calls=(_tool_call("lion_diff"),)), _reply(content="compared")])
    report = _report(tmp_path)
    record = run_attempt(suite.SCENARIO_DIFF, _env(tmp_path, chat=chat, session=session), report, hard=False)

    assert record.outcome is AttemptOutcome.FAILED
    assert "lion_diff" in record.failure


# --- 4. history predicate -----------------------------------------------------


def test_history_check_rejects_independent_success(tmp_path: Path) -> None:
    """A failed history call plus an unrelated success never completes the task."""
    session = FakeSession(
        {
            "lion_history": _error_result("No state stored"),
            "lion_recordings": '{"recordings": []}',
        }
    )
    chat = ScriptedChat(
        [
            _reply(tool_calls=(_tool_call("lion_history"),)),
            _reply(tool_calls=(_tool_call("lion_recordings"),)),
            _reply(content="no states"),
        ]
    )
    report = _report(tmp_path)
    record = run_attempt(suite.SCENARIO_HISTORY, _env(tmp_path, chat=chat, session=session), report, hard=False)

    assert record.outcome is AttemptOutcome.FAILED
    assert record.metrics.expected_tool_succeeded is False


def test_history_check_accepts_fixture_payload(tmp_path: Path) -> None:
    """A successful history payload carrying the A/B fixture refs passes."""
    payload = json.dumps({"entries": [{"ref": A_REF, "index": 1}, {"ref": B_REF, "index": 2, "latest": True}]})
    report = _report(tmp_path)
    record = run_attempt(suite.SCENARIO_HISTORY, _one_tool_env(tmp_path, "lion_history", payload), report, hard=False)

    assert record.outcome is AttemptOutcome.PASSED


# --- 5. ADR predicate ---------------------------------------------------------


def _adr_handler(fields: Mapping[str, str]) -> Handler:
    def handle(arguments: dict[str, object]) -> str:
        ref = add_adr(fields)
        return json.dumps({"ref": ref, "path": str(adrs.get_adrs_dir() / f"{ref}.toml")})

    return handle


def _adr_env(root: Path, handler: Handler) -> AgentEnvironment:
    chat = ScriptedChat([_reply(tool_calls=(_tool_call("lion_adr_add"),)), _reply(content="recorded")])
    return _env(root, chat=chat, session=FakeSession({"lion_adr_add": handler}))


def test_adr_check_accepts_one_valid_publication(tmp_path: Path) -> None:
    """Exactly one published ADR with the requested content passes the task."""
    fields = {"title": "MCP transport", "decision": "The lion MCP server speaks stdio only."}
    report = _report(tmp_path)
    record = run_attempt(suite.SCENARIO_ADR_WRITE, _adr_env(tmp_path, _adr_handler(fields)), report, hard=False)

    assert record.outcome is AttemptOutcome.PASSED


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "Missing decision"},
        {"title": "Wrong text", "decision": "Because the reviewer said so."},
    ],
    ids=["missing-decision", "decision-without-stdio"],
)
def test_adr_check_rejects_wrong_content(tmp_path: Path, fields: dict[str, str]) -> None:
    """An ADR without a decision or without the stdio requirement fails the task."""
    report = _report(tmp_path)
    record = run_attempt(suite.SCENARIO_ADR_WRITE, _adr_env(tmp_path, _adr_handler(fields)), report, hard=False)

    assert record.outcome is AttemptOutcome.FAILED


def test_adr_check_rejects_a_second_publication(tmp_path: Path) -> None:
    """A second successful publish in one scenario fails the exactly-one rule."""
    fields = {"title": "MCP transport", "decision": "The lion MCP server speaks stdio only."}
    chat = ScriptedChat(
        [
            _reply(tool_calls=(_tool_call("lion_adr_add"),)),
            _reply(tool_calls=(_tool_call("lion_adr_add"),)),
            _reply(content="recorded"),
        ]
    )
    env = _env(tmp_path, chat=chat, session=FakeSession({"lion_adr_add": _adr_handler(fields)}))
    report = _report(tmp_path)
    record = run_attempt(suite.SCENARIO_ADR_WRITE, env, report, hard=False)

    assert record.outcome is AttemptOutcome.FAILED
    assert "exactly one published ADR" in record.failure


# --- 6. multi-tool fidelity ---------------------------------------------------


def test_multi_tool_response_keeps_name_order(tmp_path: Path) -> None:
    """Two tool calls in one reply execute in order and answer with their tool_name."""
    session = FakeSession({"lion_history": '{"entries": []}', "lion_recordings": '{"recordings": []}'})
    chat = ScriptedChat(
        [
            _reply(tool_calls=(_tool_call("lion_history"), _tool_call("lion_recordings"))),
            _reply(content="done"),
        ]
    )
    report = _report(tmp_path)
    scenario = _scenario("multi-tool", required_tools=("lion_history", "lion_recordings"))
    record = run_attempt(scenario, _env(tmp_path, chat=chat, session=session), report, hard=True)

    assert record.outcome is AttemptOutcome.PASSED
    assert session.calls == [("lion_history", {}), ("lion_recordings", {})]
    follow_up = chat.requests[1]
    tool_messages = [message for message in follow_up if message["role"] == "tool"]
    assert [message["tool_name"] for message in tool_messages] == ["lion_history", "lion_recordings"]
    assert [message["content"] for message in tool_messages] == ['{"entries": []}', '{"recordings": []}']


# --- 7. thinking and done_reason ---------------------------------------------


def test_thinking_stays_in_the_follow_up_request(tmp_path: Path) -> None:
    """A delivered thinking field is replayed in the assistant history."""
    session = FakeSession({"lion_history": '{"entries": []}'})
    chat = ScriptedChat(
        [
            _reply(thinking="check the ledger first", tool_calls=(_tool_call("lion_history"),)),
            _reply(content="done"),
        ]
    )
    report = _report(tmp_path)
    record = run_attempt(
        _scenario("thinking", required_tools=("lion_history",)),
        _env(tmp_path, chat=chat, session=session),
        report,
        hard=True,
    )

    assistant = [message for message in chat.requests[1] if message["role"] == "assistant"]
    assert assistant and assistant[0]["thinking"] == "check the ledger first"
    assert record.metrics.done_reasons == ("stop", "stop")
    assert record.metrics.empty_final_answer is False


# --- 8. budget and termination reasons ----------------------------------------


async def _slow_chat(
    settings: OllamaSettings,
    messages: Sequence[Mapping[str, object]],
    tools: Sequence[Mapping[str, object]],
    timeout: float,
) -> ChatReply:
    await asyncio.sleep(10)
    raise AssertionError("the scenario timeout must cancel this chat")


def test_termination_reasons_and_budgets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every budget breach maps to its closed termination reason with partial data."""
    report = _report(tmp_path)
    scenario = _scenario("budget", required_tools=("lion_history",))
    history = _tool_call("lion_history")

    monkeypatch.setattr(agent_module, "MAX_STEPS", 2)
    stepper = ScriptedChat([_reply(tool_calls=(history,)), _reply(tool_calls=(history,))])
    record = run_attempt(scenario, _env(tmp_path / "steps", chat=stepper), report, hard=False)
    assert record.termination == "step_limit"
    assert record.outcome is AttemptOutcome.ERROR
    assert record.steps == 2
    assert len(record.tool_calls) == 2

    too_many = [_tool_call("lion_history") for _ in range(agent_module.MAX_CALLS_PER_RESPONSE + 1)]
    record = run_attempt(
        scenario, _env(tmp_path / "calls", chat=ScriptedChat([_reply(tool_calls=too_many)])), report, hard=False
    )
    assert record.termination == "call_limit"

    for exc, expected in [
        (ChatTimeoutError("the chat timed out"), "chat_timeout"),
        (ChatTransportError("Ollama is not reachable"), "transport_error"),
        (AgentError("model produced non-object tool arguments"), "malformed_output"),
    ]:
        chat = ScriptedChat([exc])
        record = run_attempt(scenario, _env(tmp_path / expected, chat=chat), report, hard=False)
        assert record.termination == expected, expected
        assert record.outcome is AttemptOutcome.ERROR

    broken = _env(
        tmp_path / "startup", chat=ScriptedChat([]), session_factory=_failing_factory(RuntimeError("no server"))
    )
    record = run_attempt(scenario, broken, report, hard=False)
    assert record.termination == "startup_error"
    assert record.tool_calls == ()

    monkeypatch.setattr(agent_module, "SCENARIO_TIMEOUT_SECONDS", 0.05)
    record = run_attempt(scenario, _env(tmp_path / "timeout", chat=_slow_chat), report, hard=False)
    assert record.termination == "scenario_timeout"
    assert record.steps == 1
    assert record.teardown_seconds < agent_module.CLEANUP_ALLOWANCE_SECONDS
    assert record.duration_seconds >= 0.05


def test_total_call_limit_counts_across_steps(tmp_path: Path) -> None:
    """The cumulative tool-call limit fires when the calls of all steps add up past it."""
    report = _report(tmp_path)
    scenario = _scenario("total-calls", required_tools=("lion_history",))
    per_turn = agent_module.MAX_CALLS_PER_RESPONSE
    replies = [_reply(tool_calls=tuple(_tool_call("lion_history") for _ in range(per_turn))) for _ in range(4)]
    replies.append(_reply(tool_calls=(_tool_call("lion_history"),)))
    record = run_attempt(scenario, _env(tmp_path, chat=ScriptedChat(replies)), report, hard=False)

    assert record.outcome is AttemptOutcome.ERROR
    assert record.termination == "call_limit"
    assert record.steps == 5
    assert len(record.tool_calls) == agent_module.MAX_TOTAL_CALLS
    assert f"more than {agent_module.MAX_TOTAL_CALLS} tool calls" in record.failure


# --- 9. CLI timeout -----------------------------------------------------------


def test_cli_json_timeout_is_a_loud_agent_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hanging CLI read-back fails loudly instead of blocking forever."""
    monkeypatch.setattr(agent_module, "CLI_TIMEOUT_SECONDS", 0.000001)
    env = {"HOME": str(tmp_path / "home"), "XDG_DATA_HOME": str(tmp_path / "data")}
    with pytest.raises(AgentError, match="did not finish within"):
        agent_module.cli_json(env, "history")


# --- 10. matrix metrics -------------------------------------------------------


def test_matrix_metrics_keep_denominators_and_errors(tmp_path: Path) -> None:
    """First-tool hits, called/succeeded tools and harness errors stay separated."""
    report = _report(tmp_path)
    hit_env = _one_tool_env(tmp_path / "hit", "lion_history", '{"entries": []}')
    run_attempt(suite.MATRIX[0], hit_env, report, hard=False)

    miss_session = FakeSession({"lion_recordings": "{}", "lion_history": '{"entries": []}'})
    miss_chat = ScriptedChat(
        [
            _reply(tool_calls=(_tool_call("lion_recordings"), _tool_call("lion_history"))),
            _reply(content="done"),
        ]
    )
    run_attempt(suite.MATRIX[0], _env(tmp_path / "miss", chat=miss_chat, session=miss_session), report, hard=False)

    broken = _env(
        tmp_path / "error", chat=ScriptedChat([]), session_factory=_failing_factory(RuntimeError("no server"))
    )
    run_attempt(suite.MATRIX[0], broken, report, hard=False)

    report.finalize()
    metrics = _section(_load(tmp_path / "report.json"), "metrics")
    assert metrics["attempts"] == {
        "total": 3,
        "passed": 1,
        "failed": 1,
        "error": 1,
        "hard": 0,
        "report_only": 3,
    }
    assert metrics["first_tool_hit"] == {"numerator": 1, "denominator": 3, "value": pytest.approx(1 / 3)}
    assert metrics["expected_tool_called"] == {"numerator": 2, "denominator": 3, "value": pytest.approx(2 / 3)}
    assert metrics["expected_tool_succeeded"] == {"numerator": 2, "denominator": 3, "value": pytest.approx(2 / 3)}
    assert metrics["task_completed"] == {"numerator": 1, "denominator": 3, "value": pytest.approx(1 / 3)}
    reasons = _section(metrics, "done_reasons")
    assert reasons["replies"] == 4
    assert reasons["abnormal"] == 0
    assert _section(reasons, "counts") == {"stop": 4}


# --- 11. determinism and metadata --------------------------------------------


def _fixture_tree(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, dict[str, bytes]]:
    """Seed the agentic fixtures under one isolated data home; return refs plus files."""
    monkeypatch.setenv("XDG_DATA_HOME", str(root / "data"))
    recording_ref = ledger.secret_recording()
    adr_ref = ledger.search_adr()
    files = {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
    return f"{recording_ref}|{adr_ref}", files


def _normalize_segment_headers(files: Mapping[str, bytes]) -> dict[str, bytes]:
    normalized: dict[str, bytes] = {}
    for name, data in files.items():
        if name.endswith(".log"):
            lines = data.decode("utf-8").splitlines()
            fields = lines[0].split("\t")
            fields[1:4] = ["<boot>", "<pid>", "<ticks>"]
            lines[0] = "\t".join(fields)
            data = ("\n".join(lines) + "\n").encode("utf-8")
        normalized[name] = data
    return normalized


def test_fixture_determinism_across_isolated_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two isolated runs produce identical fixture refs and bytes; only the kernel segment header differs."""
    first_refs, first_tree = _fixture_tree(tmp_path / "one", monkeypatch)
    second_refs, second_tree = _fixture_tree(tmp_path / "two", monkeypatch)

    assert first_refs == second_refs
    recording_ref, adr_ref = first_refs.split("|")
    assert recording_ref == "2026-10-05T18-30-00.000000Z"
    assert adr_ref == "adr-2026-10-05T18-50-00.000000Z"
    assert set(first_tree) == set(second_tree)
    assert _normalize_segment_headers(first_tree) == _normalize_segment_headers(second_tree)


def test_comparability_metadata_is_stable(tmp_path: Path) -> None:
    """Suite, prompt and inventory metadata are deterministic and pinned."""
    identity = suite.suite_identity()
    assert identity["sha256"] == "59dc337a0674f8c3262fd33993f9dc8fd415857ee6f2100d59028d9dd7a28052"
    raw_scenarios = identity.get("scenarios")
    assert isinstance(raw_scenarios, list)
    scenario_names = [str(name) for name in cast("list[object]", raw_scenarios)]
    assert scenario_names[0] == "history-overview"
    assert len(scenario_names) == 17

    builtin = agent_module.load_system_prompt({})
    assert builtin.source == "builtin"
    assert builtin.sha256 == hashlib.sha256(agent_module.SYSTEM_PROMPT.encode("utf-8")).hexdigest()

    variant_file = tmp_path / "variant.md"
    variant_file.write_text("You are the campaign variant.\n", encoding="utf-8")
    variant = agent_module.load_system_prompt({"LION_AGENT_SYSTEM_PROMPT_FILE": str(variant_file)})
    assert variant.source == "file"
    assert variant.path == str(variant_file)
    assert variant.sha256 == hashlib.sha256(b"You are the campaign variant.\n").hexdigest()

    empty_file = tmp_path / "empty.md"
    empty_file.write_text("   \n", encoding="utf-8")
    with pytest.raises(AgentError, match="is empty"):
        agent_module.load_system_prompt({"LION_AGENT_SYSTEM_PROMPT_FILE": str(empty_file)})
    with pytest.raises(AgentError, match="cannot read"):
        agent_module.load_system_prompt({"LION_AGENT_SYSTEM_PROMPT_FILE": str(tmp_path / "missing.md")})

    tools = agent_module.to_ollama_tools(list(_TOOLS))
    first = agent_module.inventory_sha256(tools)
    assert first == agent_module.inventory_sha256(agent_module.to_ollama_tools(list(_TOOLS)))
    changed = agent_module.to_ollama_tools(
        [Tool(name="lion_diff", description="A different description.", input_schema={"type": "object"})]
    )
    assert agent_module.inventory_sha256(changed) != first

    report = _report(tmp_path)
    record_one = run_attempt(
        _scenario("stable", required_tools=("lion_history",)), _one_tool_env(tmp_path / "a"), report, hard=True
    )
    record_two = run_attempt(
        _scenario("stable", required_tools=("lion_history",)), _one_tool_env(tmp_path / "b"), report, hard=True
    )
    assert record_one.user_text_sha256 == record_two.user_text_sha256
    assert record_one.tool_inventory_sha256 == record_two.tool_inventory_sha256
    assert record_one.prompt_name == ""
    assert record_one.prompt_name == record_two.prompt_name


# --- 12. chat parser (mock transport) -----------------------------------------


def _responder(response: httpx.Response) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    return handler


def _raising(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def _chat_via(handler: Callable[[httpx.Request], httpx.Response]) -> ChatReply:
    """Run the real chat parser against a mock transport."""
    return asyncio.run(
        agent_module.chat(
            OllamaSettings(),
            [{"role": "user", "content": "list states"}],
            [{"type": "function", "function": {"name": "lion_history"}}],
            transport=httpx.MockTransport(handler),
        )
    )


def test_chat_parses_reply_fidelity() -> None:
    """The chat seam parses the wire shape: content, thinking, tool calls and counters."""
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        bodies.append(cast("dict[str, object]", json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "checking",
                    "thinking": "look at the ledger first",
                    "tool_calls": [{"function": {"name": "lion_history", "arguments": {"limit": 2}}}],
                },
                "done_reason": "stop",
                "prompt_eval_count": 11,
                "eval_count": 7,
            },
        )

    reply = _chat_via(handler)

    assert reply.content == "checking"
    assert reply.thinking == "look at the ledger first"
    assert reply.done_reason == "stop"
    assert reply.prompt_eval_count == 11
    assert reply.eval_count == 7
    (call,) = reply.tool_calls
    assert call.name == "lion_history"
    assert call.arguments == {"limit": 2}
    assert call.arguments_raw == {"limit": 2}
    assert call.normalized is False
    assert call.raw == {"function": {"name": "lion_history", "arguments": {"limit": 2}}}

    settings = OllamaSettings()
    body = bodies[0]
    assert body["model"] == settings.model
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "list states"}]
    assert body["tools"] == [{"type": "function", "function": {"name": "lion_history"}}]
    assert body["options"] == settings.options.as_ollama()
    assert body["keep_alive"] == settings.options.keep_alive


@pytest.mark.parametrize(
    ("arguments", "normalized", "expected"),
    [
        ('{"reference": "1", "note": null}', True, {"reference": "1"}),
        ({"reference": "1"}, False, {"reference": "1"}),
        (None, True, {}),
    ],
    ids=["string-with-null", "clean-object", "missing"],
)
def test_chat_normalizes_tool_arguments(arguments: object, normalized: bool, expected: dict[str, object]) -> None:
    """String or null-laden tool arguments are normalized; clean objects stay untouched."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = {"tool_calls": [{"function": {"name": "lion_history", "arguments": arguments}}]}
        return httpx.Response(200, json={"message": body})

    (call,) = _chat_via(handler).tool_calls
    assert call.arguments == expected
    assert call.arguments_raw == arguments
    assert call.normalized is normalized


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ("{not json", "unparseable tool arguments"),
        (5, "non-object tool arguments"),
        ([1, 2], "non-object tool arguments"),
    ],
    ids=["unparseable", "number", "array"],
)
def test_chat_rejects_malformed_arguments(arguments: object, message: str) -> None:
    """Malformed tool arguments raise a loud AgentError before any call runs."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = {"tool_calls": [{"function": {"name": "lion_history", "arguments": arguments}}]}
        return httpx.Response(200, json={"message": body})

    with pytest.raises(AgentError, match=message):
        _chat_via(handler)


def test_chat_rejects_tool_call_without_name() -> None:
    """A tool call without a name is a loud AgentError, never a guess."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"tool_calls": [{"function": {"arguments": {}}}]}})

    with pytest.raises(AgentError, match="without a name"):
        _chat_via(handler)


@pytest.mark.parametrize(
    ("handler", "error", "message"),
    [
        (_raising(httpx.ReadTimeout("read timed out")), ChatTimeoutError, "timed out after"),
        (_raising(httpx.ConnectError("connection refused")), ChatTransportError, "not reachable"),
        (_responder(httpx.Response(500, text="model failed to load")), ChatTransportError, "HTTP 500"),
        (_responder(httpx.Response(200, text="not json")), ChatTransportError, "is not JSON"),
        (_responder(httpx.Response(200, json=[1, 2])), ChatTransportError, "is not a JSON object"),
    ],
    ids=["timeout", "connect", "http-500", "invalid-json", "non-object-json"],
)
def test_chat_maps_transport_and_parse_failures(
    handler: Callable[[httpx.Request], httpx.Response], error: type[AgentError], message: str
) -> None:
    """Transport errors, HTTP errors and unparseable bodies map to the loud chat errors."""
    with pytest.raises(error, match=message):
        _chat_via(handler)


# --- 13. closed vocabularies --------------------------------------------------


def test_attempt_record_rejects_unknown_termination() -> None:
    """A termination outside the closed vocabulary is a loud programming error."""
    with pytest.raises(AgentError, match="unknown termination reason"):
        AttemptRecord(
            name="bogus",
            request="local regression scenario bogus",
            hard=False,
            started_at="2026-10-05T18:00:00+00:00",
            outcome=AttemptOutcome.ERROR,
            termination="bogus",
        )


def test_report_rejects_unknown_run_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The report never publishes a state outside the closed vocabulary."""
    report = _report(tmp_path)
    monkeypatch.setattr(report, "_state", "bogus")
    with pytest.raises(AgentError, match="unknown run state"):
        report.write()


# --- 14. scenario names -------------------------------------------------------


@pytest.mark.parametrize("name", ["", ".", "..", "sub/scenario"], ids=["empty", "dot", "dotdot", "slash"])
def test_scenario_name_stays_a_file_component(name: str) -> None:
    """A scenario name that could escape the transcript directory is rejected."""
    with pytest.raises(AgentError, match="file name component"):
        Scenario(name=name, request="local regression scenario")
