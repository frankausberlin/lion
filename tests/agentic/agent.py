"""Agentic QA harness: drive ``lion serve`` over stdio with a local Ollama model.

The opt-in suite in :mod:`tests.agentic.test_mcp_agentic` uses this module as
its engine. A scenario is a user request (or a rendered MCP prompt) that a
local Ollama model answers by calling the ``lion serve`` MCP tools; the model
talks to the real server over stdio through the official MCP SDK client, and
every model-visible message is written to a JSONL transcript.

Design rules:

- The endpoint, model and sampling options are one source of truth
  (:class:`OllamaSettings`, :class:`OllamaOptions`); environment variables only
  override them for experiments. ``temperature 0`` and a fixed seed reduce,
  but never remove, variation — the suite is advisory.
- Environment problems fail loudly with an action hint (start Ollama, pull the
  model); nothing is skipped.
- Hard assertions stay model-independent: a required tool was called, a custom
  scenario check passed, no recorded secret reached the model-visible text.
  Answer quality and tool-selection ratios are reported, not asserted.
- Every attempt goes through :func:`run_attempt`: it is registered in the
  ``report.json`` before the scenario starts, exactly one outcome is recorded
  (passed/failed/error), and the report is checkpointed atomically after every
  attempt, so an interrupted run stays readable. Hard scenarios re-raise their
  failure after the recording; report-only variants never do.
- The execution budget is one deadline for the whole scenario
  (:data:`SCENARIO_TIMEOUT_SECONDS`), a per-chat cap
  (:data:`CHAT_TIMEOUT_SECONDS`), and hard step and tool-call limits; the
  server teardown after a timeout is measured separately and stays within
  :data:`CLEANUP_ALLOWANCE_SECONDS` (the MCP SDK's shielded shutdown bounds
  every internal wait, so no ``lion serve`` process is left behind).

Nothing here imports Typer or the ``lion`` package: the server runs as a real
subprocess, exactly like a client would start it.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import cast

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent, Tool

DEFAULT_MODEL = "JetBrains/mellum2-instruct-q4_k_m:latest"
DEFAULT_HOST = "http://127.0.0.1:11434"
MODEL_ENV = "LION_AGENT_MODEL"
HOST_ENV = "LION_AGENT_OLLAMA_HOST"
SEED_ENV = "LION_AGENT_SEED"
NUM_CTX_ENV = "LION_AGENT_NUM_CTX"
MAX_STEPS = 8
MAX_CALLS_PER_RESPONSE = 4
MAX_TOTAL_CALLS = 16
CHAT_TIMEOUT_SECONDS = 120.0
SCENARIO_TIMEOUT_SECONDS = 300.0
CLEANUP_ALLOWANCE_SECONDS = 15.0
CLI_TIMEOUT_SECONDS = 60.0
TRANSCRIPTS_DIR = "transcripts"
REPORT_SCHEMA_VERSION = 1
RUN_STATES: tuple[str, ...] = ("running", "complete", "interrupted", "preflight_error")
TERMINATIONS: tuple[str, ...] = (
    "completed",
    "step_limit",
    "scenario_timeout",
    "chat_timeout",
    "call_limit",
    "transport_error",
    "malformed_output",
    "startup_error",
    "interrupted",
    "harness_error",
)

SYSTEM_PROMPT = (
    "You are a Linux operations assistant with access to the lion MCP tools. "
    "Answer only from tool results; never invent data. "
    "Call a tool whenever it can answer the request, then reply briefly."
)


class AgentError(RuntimeError):
    """A loud harness failure: endpoint, model, or a broken model interaction."""


class ChatTransportError(AgentError):
    """The chat request failed before a reply was parsed (transport or HTTP)."""


class ChatTimeoutError(AgentError):
    """The chat request exceeded its own timeout inside the scenario budget."""


# --- environment -------------------------------------------------------------


def _int_env(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise AgentError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class OllamaOptions:
    """The per-request sampling options (one source of truth, no modelfile)."""

    temperature: float = 0.0
    seed: int = 42
    top_k: int = 1
    top_p: float = 1.0
    repeat_penalty: float = 1.0
    num_ctx: int = 16384
    num_predict: int = 1024
    keep_alive: str = "30m"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OllamaOptions":
        """Build the options, honouring the experiment overrides."""
        source = os.environ if env is None else env
        return cls(
            seed=_int_env(source, SEED_ENV, cls.seed),
            num_ctx=_int_env(source, NUM_CTX_ENV, cls.num_ctx),
        )

    def as_ollama(self) -> dict[str, object]:
        """The ``options`` object of an Ollama request."""
        return {
            "temperature": self.temperature,
            "seed": self.seed,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "repeat_penalty": self.repeat_penalty,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }

    def as_json(self) -> dict[str, object]:
        """The options plus ``keep_alive``, for the report."""
        return {**self.as_ollama(), "keep_alive": self.keep_alive}


@dataclass(frozen=True)
class OllamaSettings:
    """Resolved endpoint, model and options of the local Ollama instance."""

    host: str = DEFAULT_HOST
    model: str = DEFAULT_MODEL
    options: OllamaOptions = OllamaOptions()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OllamaSettings":
        """Build the settings, honouring ``LION_AGENT_*`` overrides."""
        source = os.environ if env is None else env
        return cls(
            host=source.get(HOST_ENV, DEFAULT_HOST),
            model=source.get(MODEL_ENV, DEFAULT_MODEL),
            options=OllamaOptions.from_env(source),
        )


@dataclass(frozen=True)
class OllamaModel:
    """The verified model: name, digest and reported capabilities."""

    name: str
    digest: str
    capabilities: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        """The model facts for the report."""
        return {"name": self.name, "digest": self.digest, "capabilities": list(self.capabilities)}


# --- HTTP --------------------------------------------------------------------


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AgentError(f"expected a JSON object, got {type(value).__name__}: {value!r}")
    return cast("dict[str, object]", value)


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise AgentError(f"expected a JSON array, got {type(value).__name__}: {value!r}")
    return cast("list[object]", value)


def _loads_object(text: str, what: str) -> dict[str, object]:
    """Parse one JSON object; a syntax error or a non-object is loud."""
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentError(f"{what} is not JSON: {text[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise AgentError(f"{what} is not a JSON object: {text[:200]!r}")
    return cast("dict[str, object]", parsed)


def _unreachable_message(url: str, exc: Exception) -> str:
    """The loud action-hint message for a transport-level failure."""
    return (
        f"Ollama is not reachable at {url} ({exc}); start it with 'ollama serve' "
        f"or point {HOST_ENV} at a running instance."
    )


def _http_failure_message(url: str, response: httpx.Response) -> str:
    """The loud message for an HTTP error, including a body excerpt."""
    return f"Ollama request to {url} failed with HTTP {response.status_code}: {response.text[:400]}"


def _http_json(url: str, payload: Mapping[str, object] | None = None, timeout: float = 30.0) -> dict[str, object]:
    """Call Ollama synchronously and parse a JSON object; every failure is loud."""
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url) if payload is None else client.post(url, json=payload)
    except httpx.TransportError as exc:
        raise AgentError(_unreachable_message(url, exc)) from exc
    if response.status_code >= 400:
        raise AgentError(_http_failure_message(url, response))
    return _loads_object(response.text, f"Ollama response from {url}")


def check_ollama(settings: OllamaSettings | None = None) -> OllamaModel:
    """Verify endpoint, model and tool support; never skip.

    Raises:
        AgentError: If Ollama is unreachable, the model is missing, or the
            model does not advertise tool calls.
    """
    resolved = OllamaSettings.from_env() if settings is None else settings
    tags = _http_json(f"{resolved.host}/api/tags")
    entries = [_mapping(item) for item in _sequence(tags.get("models", []))]
    entry = next((item for item in entries if item.get("name") == resolved.model), None)
    if entry is None:
        known = ", ".join(sorted(str(item.get("name")) for item in entries)) or "none"
        raise AgentError(
            f"Ollama model {resolved.model!r} is not installed at {resolved.host} (available: {known}). "
            f"Run 'ollama pull {resolved.model}' or set {MODEL_ENV}."
        )
    show = _http_json(f"{resolved.host}/api/show", {"model": resolved.model})
    capabilities = tuple(str(item) for item in _sequence(show.get("capabilities", [])))
    if "tools" not in capabilities:
        raise AgentError(
            f"Ollama model {resolved.model!r} does not support tool calls (capabilities: {list(capabilities)}); "
            f"pick a tool-capable model via {MODEL_ENV}."
        )
    return OllamaModel(name=resolved.model, digest=str(entry.get("digest", "")), capabilities=capabilities)


# --- chat --------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """One tool call the model asked for: raw value, normalized arguments, raw entry."""

    name: str
    arguments: dict[str, object]
    arguments_raw: object
    normalized: bool
    raw: Mapping[str, object]


@dataclass(frozen=True)
class ChatReply:
    """One non-streamed assistant reply."""

    content: str
    thinking: str
    tool_calls: tuple[ToolCall, ...]
    prompt_eval_count: int
    eval_count: int
    done_reason: str


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0


def _normalize_arguments(raw: object) -> tuple[dict[str, object], bool]:
    """Accept object or JSON-string arguments, drop ``null`` values, and report changes.

    ``normalized`` is ``False`` exactly when the value already was a JSON
    object without ``null`` values — the shape the API expects.
    """
    if raw is None:
        return {}, True
    changed = False
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentError(f"model produced unparseable tool arguments: {raw[:200]!r}") from exc
        changed = True
    if not isinstance(raw, dict):
        raise AgentError(f"model produced non-object tool arguments: {raw!r}")
    arguments: dict[str, object] = {}
    for key, value in cast("dict[object, object]", raw).items():
        if value is None:
            changed = True
            continue
        arguments[str(key)] = value
    return arguments, changed


def to_ollama_tools(tools: Sequence[Tool]) -> list[dict[str, object]]:
    """Convert MCP tool definitions to the Ollama/OpenAI function format."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": dict(tool.input_schema),
            },
        }
        for tool in tools
    ]


def inventory_sha256(tools: Sequence[Mapping[str, object]]) -> str:
    """The canonical SHA-256 of the converted tool inventory (report and transcript)."""
    canonical = json.dumps(list(tools), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def chat(
    settings: OllamaSettings,
    messages: Sequence[Mapping[str, object]],
    tools: Sequence[Mapping[str, object]],
    timeout: float = CHAT_TIMEOUT_SECONDS,
) -> ChatReply:
    """Send one non-streamed ``/api/chat`` request.

    Raises:
        ChatTimeoutError: If the request exceeds ``timeout``.
        ChatTransportError: On transport errors, HTTP errors (with a body
            excerpt) and unparseable responses.
        AgentError: On malformed tool calls in an otherwise valid reply.
    """
    payload: dict[str, object] = {
        "model": settings.model,
        "messages": [dict(message) for message in messages],
        "tools": [dict(tool) for tool in tools],
        "stream": False,
        "options": settings.options.as_ollama(),
        "keep_alive": settings.options.keep_alive,
    }
    url = f"{settings.host}/api/chat"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        raise ChatTimeoutError(f"Ollama chat request to {url} timed out after {timeout:.0f}s") from exc
    except httpx.TransportError as exc:
        raise ChatTransportError(_unreachable_message(url, exc)) from exc
    if response.status_code >= 400:
        raise ChatTransportError(_http_failure_message(url, response))
    try:
        reply = _loads_object(response.text, f"Ollama response from {url}")
    except AgentError as exc:
        raise ChatTransportError(str(exc)) from exc
    message = _mapping(reply.get("message", {}))
    calls: list[ToolCall] = []
    for item in _sequence(message.get("tool_calls", []) or []):
        entry = _mapping(item)
        function = _mapping(entry.get("function", {}))
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise AgentError(f"model produced a tool call without a name: {item!r}")
        arguments_raw = function.get("arguments")
        arguments, normalized = _normalize_arguments(arguments_raw)
        calls.append(
            ToolCall(name=name, arguments=arguments, arguments_raw=arguments_raw, normalized=normalized, raw=entry)
        )
    content = message.get("content", "")
    thinking = message.get("thinking", "")
    done_reason = reply.get("done_reason", "")
    return ChatReply(
        content=content if isinstance(content, str) else str(content),
        thinking=thinking if isinstance(thinking, str) else str(thinking),
        tool_calls=tuple(calls),
        prompt_eval_count=_int_value(reply.get("prompt_eval_count")),
        eval_count=_int_value(reply.get("eval_count")),
        done_reason=done_reason if isinstance(done_reason, str) else str(done_reason),
    )


# --- scenarios ---------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioContext:
    """The ledger state around one scenario run, for custom checks."""

    data_home: Path
    server_env: Mapping[str, str]
    before: Mapping[str, bytes]
    after: Mapping[str, bytes]

    def changed_files(self) -> set[str]:
        """Paths whose content differs between the before/after snapshots."""
        return {path for path in set(self.before) | set(self.after) if self.before.get(path) != self.after.get(path)}


ScenarioCheck = Callable[["ScenarioRun"], None]


@dataclass(frozen=True)
class Scenario:
    """One scripted agentic request and its hard checks.

    ``request`` is the user turn; when ``prompt_name`` is set, the harness
    renders that MCP prompt on the live server and uses it as the user turn
    instead (the request then only documents the scenario in the report).
    ``required_tools`` must all be called; ``required_any`` needs at least one
    hit when it is not empty. Together they are the scenario's target tools,
    the basis of the recovery metrics.
    """

    name: str
    request: str
    required_tools: tuple[str, ...] = ()
    required_any: tuple[str, ...] = ()
    prompt_name: str = ""
    prompt_arguments: Mapping[str, str] = field(default_factory=dict[str, str])
    check: ScenarioCheck | None = None


@dataclass(frozen=True)
class ToolCallRecord:
    """One executed tool call with its result and duration."""

    name: str
    arguments: Mapping[str, object]
    is_error: bool
    result_text: str
    duration_seconds: float

    def result_json(self) -> dict[str, object] | None:
        """The parsed tool result, or ``None`` when it is not a JSON object."""
        try:
            parsed: object = json.loads(self.result_text)
        except json.JSONDecodeError:
            return None
        return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else None


@dataclass(frozen=True)
class RecoveryMetrics:
    """How the model handled failures of the scenario's target tools.

    The definitions are deliberately narrow (target tools only) so they stay
    comparable across models and prompt variants:

    - ``first_attempt_success``: the first target-tool call succeeded and the
      task checks passed.
    - ``failed_target_calls``: target-tool calls that came back as errors.
    - ``retries``: target-tool calls that followed a failed target-tool call.
    - ``recovered``: the task checks passed even though a target-tool call had
      failed before.
    """

    first_attempt_success: bool
    failed_target_calls: int
    retries: int
    recovered: bool


def _recovery_metrics(calls: Sequence[ToolCallRecord], target: frozenset[str], *, task_passed: bool) -> RecoveryMetrics:
    """Compute the recovery metrics over all calls of the target tools."""
    target_calls = [call for call in calls if call.name in target]
    failed = sum(1 for call in target_calls if call.is_error)
    first = target_calls[0] if target_calls else None
    retries = 0
    failed_seen = False
    for call in target_calls:
        if failed_seen:
            retries += 1
        if call.is_error:
            failed_seen = True
    return RecoveryMetrics(
        first_attempt_success=task_passed and first is not None and not first.is_error,
        failed_target_calls=failed,
        retries=retries,
        recovered=task_passed and failed > 0,
    )


@dataclass(frozen=True)
class ScenarioRun:
    """Everything one scenario produced: transcript, calls and outcomes."""

    scenario: Scenario
    final_text: str
    calls: tuple[ToolCallRecord, ...]
    duration_seconds: float
    teardown_seconds: float
    prompt_eval_count: int
    eval_count: int
    steps: int
    transcript: Path
    context: ScenarioContext

    def tool_names(self) -> list[str]:
        """The called tool names in order."""
        return [call.name for call in self.calls]

    def calls_of(self, name: str) -> list[ToolCallRecord]:
        """Every executed call of one tool, in order."""
        return [call for call in self.calls if call.name == name]

    def successful_calls_of(self, name: str) -> list[ToolCallRecord]:
        """Every successful executed call of one tool, in order."""
        return [call for call in self.calls if call.name == name and not call.is_error]

    def first_call(self, name: str) -> ToolCallRecord | None:
        """The first executed call of one tool, or ``None``."""
        return next((call for call in self.calls if call.name == name), None)

    def target_tool_names(self) -> tuple[str, ...]:
        """The scenario's target tools: every required plus every alternative."""
        return tuple(dict.fromkeys((*self.scenario.required_tools, *self.scenario.required_any)))

    def recovery_metrics(self, *, task_passed: bool) -> RecoveryMetrics:
        """The recovery metrics of this run."""
        return _recovery_metrics(self.calls, frozenset(self.target_tool_names()), task_passed=task_passed)

    def assert_required_tools(self) -> None:
        """Assert that every required tool was called at least once."""
        called = self.tool_names()
        missing = [name for name in self.scenario.required_tools if name not in called]
        assert not missing, f"scenario {self.scenario.name!r}: required tool(s) not called: {missing}; called: {called}"
        if self.scenario.required_any:
            assert any(name in called for name in self.scenario.required_any), (
                f"scenario {self.scenario.name!r}: none of {list(self.scenario.required_any)} was called; "
                f"called: {called}"
            )

    def run_check(self) -> None:
        """Run the scenario's custom check, if any."""
        if self.scenario.check is not None:
            self.scenario.check(self)


class ScenarioRunError(AgentError):
    """A failed scenario run with the partial data collected before the failure.

    Raises with the termination reason and whatever the loop had already
    observed (calls, steps, token counts), so :func:`run_attempt` can record a
    truthful report entry instead of zero values.
    """

    def __init__(
        self,
        message: str,
        *,
        termination: str,
        calls: Sequence[ToolCallRecord] = (),
        steps: int = 0,
        prompt_eval_count: int = 0,
        eval_count: int = 0,
        transcript: Path | None = None,
    ) -> None:
        """Record the termination reason and the partial run data."""
        super().__init__(message)
        self.termination = termination
        self.calls = tuple(calls)
        self.steps = steps
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count
        self.transcript = transcript
        self.teardown_seconds = 0.0


# --- agent loop --------------------------------------------------------------


def server_command() -> list[str]:
    """The installed console script, started exactly like a client would."""
    script = Path(sys.executable).with_name("lion")
    if not script.exists():
        raise AgentError(f"missing console script: {script}; run 'uv sync' first")
    return [str(script), "serve"]


def _tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class Transcript:
    """Append-only JSONL transcript of everything the model saw and produced."""

    def __init__(self, path: Path) -> None:
        """Open the transcript file below ``path``, creating parents."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("w", encoding="utf-8")

    def event(self, kind: str, **fields: object) -> None:
        """Append one JSON line."""
        self._handle.write(json.dumps({"kind": kind, **fields}, ensure_ascii=False) + "\n")
        self._handle.flush()

    def __enter__(self) -> "Transcript":
        """Enter the transcript context."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the transcript file."""
        self._handle.close()


async def _call_tool(session: ClientSession, call: ToolCall) -> tuple[bool, str]:
    """Execute one tool call; a failing call becomes an error result for the model."""
    try:
        result = await session.call_tool(call.name, dict(call.arguments))
    except Exception as exc:  # the model must see the error text and may correct itself
        return True, f"Tool call failed: {exc}"
    text = "\n".join(item.text if isinstance(item, TextContent) else str(item) for item in result.content)
    return bool(result.is_error), text


@dataclass
class _LoopState:
    """Mutable partial data of the model loop, so failures can carry it."""

    calls: list[ToolCallRecord] = field(default_factory=list[ToolCallRecord])
    normalizations: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    steps: int = 0
    prompt_eval_count: int = 0
    eval_count: int = 0
    teardown_seconds: float = 0.0


@dataclass
class _DriveOutcome:
    """What one completed model loop produced (internal to the harness)."""

    final_text: str
    calls: tuple[ToolCallRecord, ...]
    steps: int
    prompt_eval_count: int
    eval_count: int
    teardown_seconds: float = 0.0


def _state_failure(state: _LoopState, termination: str, message: str) -> ScenarioRunError:
    """Build a :class:`ScenarioRunError` carrying the partial data collected so far."""
    return ScenarioRunError(
        message,
        termination=termination,
        calls=tuple(state.calls),
        steps=state.steps,
        prompt_eval_count=state.prompt_eval_count,
        eval_count=state.eval_count,
    )


async def _drive_session(
    stack: AsyncExitStack,
    state: _LoopState,
    scenario: Scenario,
    settings: OllamaSettings,
    server_env: Mapping[str, str],
    transcript: Transcript,
) -> _DriveOutcome:
    """Start the server session, run the model loop, and return the outcome."""
    deadline = time.monotonic() + SCENARIO_TIMEOUT_SECONDS
    try:
        command = server_command()
        params = StdioServerParameters(command=command[0], args=["serve"], env=dict(server_env))
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        _ = await session.initialize()
        tools = (await session.list_tools()).tools
        user_text = scenario.request
        if scenario.prompt_name:
            prompt = await session.get_prompt(scenario.prompt_name, dict(scenario.prompt_arguments))
            content = prompt.messages[0].content
            user_text = content.text if isinstance(content, TextContent) else str(content)
    except Exception as exc:
        raise _state_failure(state, "startup_error", f"scenario {scenario.name!r} failed to start: {exc}") from exc
    ollama_tools = to_ollama_tools(tools)
    transcript.event(
        "tools",
        count=len(tools),
        names=[tool.name for tool in tools],
        sha256=inventory_sha256(ollama_tools),
    )
    transcript.event("system", text=SYSTEM_PROMPT)
    transcript.event("user", text=user_text)
    messages: list[dict[str, object]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    for step in range(1, MAX_STEPS + 1):
        state.steps = step
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _state_failure(
                state, "scenario_timeout", f"scenario {scenario.name!r} exceeded {SCENARIO_TIMEOUT_SECONDS:.0f}s"
            )
        transcript.event(
            "chat_request",
            messages=[dict(message) for message in messages],
            normalizations=[dict(item) for item in state.normalizations],
        )
        try:
            reply = await chat(settings, messages, ollama_tools, timeout=min(CHAT_TIMEOUT_SECONDS, remaining))
        except ChatTimeoutError as exc:
            raise _state_failure(state, "chat_timeout", str(exc)) from exc
        except ChatTransportError as exc:
            raise _state_failure(state, "transport_error", str(exc)) from exc
        except AgentError as exc:
            raise _state_failure(state, "malformed_output", str(exc)) from exc
        state.prompt_eval_count += reply.prompt_eval_count
        state.eval_count += reply.eval_count
        transcript.event(
            "assistant",
            text=reply.content,
            thinking=reply.thinking,
            done_reason=reply.done_reason,
            tool_calls=[
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "arguments_raw": call.arguments_raw,
                    "normalized": call.normalized,
                }
                for call in reply.tool_calls
            ],
            prompt_eval_count=reply.prompt_eval_count,
            eval_count=reply.eval_count,
        )
        if not reply.tool_calls:
            transcript.event("final", text=reply.content)
            return _DriveOutcome(
                final_text=reply.content,
                calls=tuple(state.calls),
                steps=step,
                prompt_eval_count=state.prompt_eval_count,
                eval_count=state.eval_count,
            )
        if len(reply.tool_calls) > MAX_CALLS_PER_RESPONSE:
            raise _state_failure(
                state,
                "call_limit",
                f"scenario {scenario.name!r}: the model asked for {len(reply.tool_calls)} tool calls in one turn "
                f"(limit {MAX_CALLS_PER_RESPONSE})",
            )
        if len(state.calls) + len(reply.tool_calls) > MAX_TOTAL_CALLS:
            raise _state_failure(
                state,
                "call_limit",
                f"scenario {scenario.name!r}: more than {MAX_TOTAL_CALLS} tool calls in one scenario",
            )
        assistant: dict[str, object] = {
            "role": "assistant",
            "content": reply.content,
            "tool_calls": [dict(call.raw) for call in reply.tool_calls],
        }
        if reply.thinking:
            assistant["thinking"] = reply.thinking
        messages.append(assistant)
        for call in reply.tool_calls:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _state_failure(
                    state,
                    "scenario_timeout",
                    f"scenario {scenario.name!r} exceeded {SCENARIO_TIMEOUT_SECONDS:.0f}s between tool calls",
                )
            started = time.monotonic()
            is_error, text = await _call_tool(session, call)
            duration = time.monotonic() - started
            state.calls.append(
                ToolCallRecord(
                    name=call.name,
                    arguments=call.arguments,
                    is_error=is_error,
                    result_text=text,
                    duration_seconds=round(duration, 3),
                )
            )
            transcript.event(
                "tool",
                name=call.name,
                arguments=call.arguments,
                arguments_raw=call.arguments_raw,
                normalized=call.normalized,
                is_error=is_error,
                text=text,
                duration_seconds=duration,
            )
            messages.append({"role": "tool", "tool_name": call.name, "content": text})
        for call in reply.tool_calls:
            if call.normalized:
                state.normalizations.append({"name": call.name, "arguments_raw": call.arguments_raw})
    raise _state_failure(
        state,
        "step_limit",
        f"scenario {scenario.name!r} did not finish within {MAX_STEPS} steps; "
        f"calls so far: {[call.name for call in state.calls]}",
    )


async def _drive(
    scenario: Scenario,
    settings: OllamaSettings,
    server_env: Mapping[str, str],
    transcript: Transcript,
) -> _DriveOutcome:
    """Run the stdio session inside the execution budget and measure its teardown."""
    state = _LoopState()
    stack = AsyncExitStack()
    outcome: _DriveOutcome | None = None
    failure: BaseException | None = None
    try:
        try:
            async with asyncio.timeout(SCENARIO_TIMEOUT_SECONDS):
                outcome = await _drive_session(stack, state, scenario, settings, server_env, transcript)
        except TimeoutError as exc:
            raise _state_failure(
                state, "scenario_timeout", f"scenario {scenario.name!r} exceeded {SCENARIO_TIMEOUT_SECONDS:.0f}s"
            ) from exc
    except BaseException as exc:
        failure = exc
        raise
    finally:
        started = time.monotonic()
        await stack.aclose()
        state.teardown_seconds = time.monotonic() - started
        if outcome is not None:
            outcome.teardown_seconds = state.teardown_seconds
        if isinstance(failure, ScenarioRunError):
            failure.teardown_seconds = state.teardown_seconds
    assert outcome is not None  # every other path re-raised above
    return outcome


def run_scenario(
    scenario: Scenario,
    settings: OllamaSettings,
    data_home: Path,
    artifacts: Path,
    server_env: Mapping[str, str],
) -> ScenarioRun:
    """Run one scenario end to end and return its outcome.

    Raises:
        ScenarioRunError: On environment problems, malformed model output, a
            scenario/chat timeout, or the step and call limits; the failure
            carries the partial run data and the transcript path.
    """
    before = _tree(data_home)
    transcript_path = artifacts / TRANSCRIPTS_DIR / f"{scenario.name}.jsonl"
    started = time.monotonic()
    try:
        with Transcript(transcript_path) as transcript:
            outcome = asyncio.run(_drive(scenario, settings, server_env, transcript))
    except ScenarioRunError as exc:
        if exc.transcript is None:
            exc.transcript = transcript_path
        raise
    duration = time.monotonic() - started
    context = ScenarioContext(
        data_home=data_home,
        server_env=server_env,
        before=before,
        after=_tree(data_home),
    )
    return ScenarioRun(
        scenario=scenario,
        final_text=outcome.final_text,
        calls=outcome.calls,
        duration_seconds=duration,
        teardown_seconds=outcome.teardown_seconds,
        prompt_eval_count=outcome.prompt_eval_count,
        eval_count=outcome.eval_count,
        steps=outcome.steps,
        transcript=transcript_path,
        context=context,
    )


def transcript_text(run: ScenarioRun) -> str:
    """The full transcript of a run, for the secret-leak proof."""
    return run.transcript.read_text(encoding="utf-8")


def cli_json(env: Mapping[str, str], *args: str) -> dict[str, object]:
    """Run the installed CLI with an isolated environment and parse ``--json``.

    Raises:
        AgentError: If the command fails, exceeds :data:`CLI_TIMEOUT_SECONDS`,
            or does not print a JSON object.
    """
    script = Path(sys.executable).with_name("lion")
    if not script.exists():
        raise AgentError(f"missing console script: {script}; run 'uv sync' first")
    try:
        result = subprocess.run(
            [str(script), *args, "--json"],
            capture_output=True,
            text=True,
            env=dict(env),
            check=False,
            timeout=CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentError(f"lion {' '.join(args)} --json did not finish within {CLI_TIMEOUT_SECONDS:.0f}s") from exc
    if result.returncode != 0:
        raise AgentError(f"lion {' '.join(args)} --json failed ({result.returncode}): {result.stderr.strip()[:300]}")
    return _loads_object(result.stdout, f"lion {' '.join(args)} --json")


@dataclass(frozen=True)
class AgentEnvironment:
    """One isolated ledger plus the live agent runner bound to it."""

    data_home: Path
    server_env: dict[str, str]
    settings: OllamaSettings
    artifacts: Path

    def run(self, scenario: Scenario) -> ScenarioRun:
        """Run one scenario against this environment."""
        return run_scenario(scenario, self.settings, self.data_home, self.artifacts, self.server_env)


# --- report ------------------------------------------------------------------


class AttemptOutcome(StrEnum):
    """The final outcome of one attempt."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass(frozen=True)
class AttemptRecord:
    """Exactly one final outcome of one attempt, as written to ``report.json``."""

    name: str
    request: str
    hard: bool
    started_at: str
    outcome: AttemptOutcome
    failure: str = ""
    termination: str = "completed"
    tool_calls: tuple[str, ...] = ()
    steps: int = 0
    duration_seconds: float = 0.0
    teardown_seconds: float = 0.0
    prompt_eval_count: int = 0
    eval_count: int = 0
    final_text: str = ""
    transcript: str = ""
    first_attempt_success: bool = False
    failed_target_calls: int = 0
    retries: int = 0
    recovered: bool = False
    notes: str = ""

    def as_json(self) -> dict[str, object]:
        """The JSON shape written to ``report.json``."""
        return {
            "name": self.name,
            "request": self.request,
            "hard": self.hard,
            "started_at": self.started_at,
            "outcome": self.outcome.value,
            "failure": self.failure,
            "termination": self.termination,
            "tool_calls": list(self.tool_calls),
            "steps": self.steps,
            "duration_seconds": self.duration_seconds,
            "teardown_seconds": self.teardown_seconds,
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "final_text": self.final_text,
            "transcript": self.transcript,
            "first_attempt_success": self.first_attempt_success,
            "failed_target_calls": self.failed_target_calls,
            "retries": self.retries,
            "recovered": self.recovered,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class _PendingAttempt:
    """One attempt that was registered but whose outcome is not final yet."""

    name: str
    request: str
    hard: bool
    started_at: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    """Write JSON atomically: temp file in the target directory, fsync, ``os.replace``."""
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


class AgentReport:
    """Collects one final outcome per attempt and checkpoints ``report.json``.

    The report exists before the first attempt: every checkpoints is written
    atomically after each attempt, and :meth:`finalize` closes the run with
    ``complete`` or ``interrupted``. A preflight problem produces a report with
    ``state = "preflight_error"`` and no invented attempts.
    """

    def __init__(self, path: Path, settings: OllamaSettings) -> None:
        """Bind the report to its artifact path and the resolved Ollama settings."""
        self._path = path
        self._settings = settings
        self._model: OllamaModel | None = None
        self._entries: list[AttemptRecord] = []
        self._extras: dict[str, object] = {}
        self._pending: _PendingAttempt | None = None
        self._interrupted = False
        self._run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{os.urandom(3).hex()}"
        self._started_at = _utc_now()
        self._finished_at: str | None = None
        self._state = "running"

    def set_model(self, model: OllamaModel) -> None:
        """Record the verified model and write the first checkpoint."""
        self._model = model
        self.write()

    def mark_preflight_error(self, message: str) -> None:
        """Record a preflight failure (no attempt ran) and write the report."""
        self._state = "preflight_error"
        self._finished_at = _utc_now()
        self._extras["preflight_error"] = message
        self.write()

    def begin_attempt(self, name: str, request: str, hard: bool) -> _PendingAttempt:
        """Register one attempt before it starts."""
        if self._pending is not None:
            raise RuntimeError(f"attempt {self._pending.name!r} is still open; finish it before starting {name!r}")
        self._pending = _PendingAttempt(name=name, request=request, hard=hard, started_at=_utc_now())
        return self._pending

    def finish_attempt(self, record: AttemptRecord) -> None:
        """Store the one final outcome and checkpoint the report."""
        self._pending = None
        self._entries.append(record)
        self.write()

    def mark_interrupted(self) -> None:
        """Mark the run as interrupted; the open attempt is closed by :meth:`finalize`."""
        self._interrupted = True

    def set_extra(self, key: str, value: object) -> None:
        """Attach a summary value (for example the tool-selection ratio)."""
        self._extras[key] = value

    def write(self) -> None:
        """Write the report atomically: temp file, fsync, ``os.replace``."""
        payload: dict[str, object] = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "run": {
                "id": self._run_id,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "state": self._state,
            },
            "model": None if self._model is None else self._model.as_json(),
            "host": self._settings.host,
            "options": self._settings.options.as_json(),
            "entries": [entry.as_json() for entry in self._entries],
            "extras": dict(self._extras),
        }
        _write_json_atomic(self._path, payload)

    def finalize(self) -> None:
        """Close any open attempt, mark the run complete or interrupted, and write."""
        if self._state != "running":
            self.write()
            return
        if self._pending is not None:
            pending = self._pending
            self._pending = None
            self._entries.append(
                AttemptRecord(
                    name=pending.name,
                    request=pending.request,
                    hard=pending.hard,
                    started_at=pending.started_at,
                    outcome=AttemptOutcome.ERROR,
                    failure="attempt interrupted before completion",
                    termination="interrupted",
                    notes="the run stopped while this attempt was in flight",
                )
            )
            self._interrupted = True
        self._state = "interrupted" if self._interrupted else "complete"
        self._finished_at = _utc_now()
        self.write()


def _build_record(
    scenario: Scenario,
    pending: _PendingAttempt,
    *,
    run: ScenarioRun | None,
    failure: ScenarioRunError | None,
    failure_text: str,
    termination: str,
    outcome: AttemptOutcome,
    duration_seconds: float,
) -> AttemptRecord:
    """Build the final report entry from a run, a scenario failure, or bare counters."""
    task_passed = outcome is AttemptOutcome.PASSED
    if run is not None:
        metrics = run.recovery_metrics(task_passed=task_passed)
        return AttemptRecord(
            name=pending.name,
            request=pending.request,
            hard=pending.hard,
            started_at=pending.started_at,
            outcome=outcome,
            failure=failure_text,
            termination=termination,
            tool_calls=tuple(run.tool_names()),
            steps=run.steps,
            duration_seconds=round(run.duration_seconds, 3),
            teardown_seconds=round(run.teardown_seconds, 3),
            prompt_eval_count=run.prompt_eval_count,
            eval_count=run.eval_count,
            final_text=run.final_text,
            transcript=str(run.transcript),
            first_attempt_success=metrics.first_attempt_success,
            failed_target_calls=metrics.failed_target_calls,
            retries=metrics.retries,
            recovered=metrics.recovered,
        )
    target = frozenset(
        dict.fromkeys((*scenario.required_tools, *scenario.required_any)),
    )
    if failure is not None:
        metrics = _recovery_metrics(failure.calls, target, task_passed=False)
        return AttemptRecord(
            name=pending.name,
            request=pending.request,
            hard=pending.hard,
            started_at=pending.started_at,
            outcome=outcome,
            failure=failure_text,
            termination=termination,
            tool_calls=tuple(call.name for call in failure.calls),
            steps=failure.steps,
            duration_seconds=round(duration_seconds, 3),
            teardown_seconds=round(failure.teardown_seconds, 3),
            prompt_eval_count=failure.prompt_eval_count,
            eval_count=failure.eval_count,
            transcript="" if failure.transcript is None else str(failure.transcript),
            first_attempt_success=metrics.first_attempt_success,
            failed_target_calls=metrics.failed_target_calls,
            retries=metrics.retries,
            recovered=metrics.recovered,
        )
    return AttemptRecord(
        name=pending.name,
        request=pending.request,
        hard=pending.hard,
        started_at=pending.started_at,
        outcome=outcome,
        failure=failure_text,
        termination=termination,
        duration_seconds=round(duration_seconds, 3),
    )


def run_attempt(scenario: Scenario, env: AgentEnvironment, report: AgentReport, *, hard: bool) -> AttemptRecord:
    """Run one scenario, record exactly one outcome, and re-raise failures of hard scenarios.

    The attempt is registered before it starts and the report is checkpointed
    after it, whatever happened: ``AssertionError`` from the scenario checks is
    ``failed``; :class:`ScenarioRunError` and any other ``Exception`` is
    ``error``. A hard scenario re-raises the recorded failure; report-only
    variants (``hard=False``) never raise. ``BaseException`` (for example
    ``KeyboardInterrupt``) propagates immediately and leaves the attempt open,
    so the fixture teardown records it as interrupted.
    """
    pending = report.begin_attempt(scenario.name, scenario.request, hard)
    started = time.monotonic()
    run: ScenarioRun | None = None
    failure: ScenarioRunError | None = None
    error: BaseException | None = None
    outcome = AttemptOutcome.PASSED
    failure_text = ""
    termination = "completed"
    try:
        run = env.run(scenario)
        run.assert_required_tools()
        run.run_check()
    except AssertionError as exc:
        outcome = AttemptOutcome.FAILED
        failure_text = str(exc)
        error = exc
    except ScenarioRunError as exc:
        outcome = AttemptOutcome.ERROR
        failure_text = str(exc)
        termination = exc.termination
        failure = exc
        error = exc
    except Exception as exc:
        outcome = AttemptOutcome.ERROR
        failure_text = f"{type(exc).__name__}: {exc}"
        termination = "harness_error"
        error = exc
    except BaseException:
        report.mark_interrupted()
        raise
    record = _build_record(
        scenario,
        pending,
        run=run,
        failure=failure,
        failure_text=failure_text,
        termination=termination,
        outcome=outcome,
        duration_seconds=time.monotonic() - started,
    )
    report.finish_attempt(record)
    if hard and error is not None:
        raise error
    return record
