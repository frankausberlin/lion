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
- ``report.json`` is comparable across runs: it pins the run metadata (Lion
  and Ollama versions, best-effort git state, model digest and capabilities,
  the sampling options and the system prompt with its SHA-256), the suite
  identity, per-attempt prompt and tool-inventory hashes, and per-attempt plus
  aggregated metrics with explicit denominators. Technical task completion and
  answer quality stay separate: the checks decide the outcome, the metrics
  describe the model.

Nothing here imports Typer or the ``lion`` package: the server runs as a real
subprocess, exactly like a client would start it.
"""

import asyncio
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Protocol, cast

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, GetPromptResult, TextContent, Tool

DEFAULT_MODEL = "JetBrains/mellum2-instruct-q4_k_m:latest"
DEFAULT_HOST = "http://127.0.0.1:11434"
MODEL_ENV = "LION_AGENT_MODEL"
HOST_ENV = "LION_AGENT_OLLAMA_HOST"
SEED_ENV = "LION_AGENT_SEED"
NUM_CTX_ENV = "LION_AGENT_NUM_CTX"
SYSTEM_PROMPT_FILE_ENV = "LION_AGENT_SYSTEM_PROMPT_FILE"
REPO_ROOT = Path(__file__).resolve().parents[2]
NORMAL_DONE_REASON = "stop"
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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SystemPrompt:
    """The harness system prompt: source, text and content hash."""

    source: str
    path: str
    text: str
    sha256: str

    def as_json(self) -> dict[str, object]:
        """The prompt facts for the report."""
        return {
            "source": self.source,
            "path": self.path or None,
            "sha256": self.sha256,
            "text": self.text,
        }


BUILTIN_SYSTEM_PROMPT = SystemPrompt(source="builtin", path="", text=SYSTEM_PROMPT, sha256=_sha256(SYSTEM_PROMPT))


def load_system_prompt(env: Mapping[str, str] | None = None) -> SystemPrompt:
    """Resolve the system prompt: the builtin one or the ``LION_AGENT_SYSTEM_PROMPT_FILE`` variant.

    Raises:
        AgentError: If the override file cannot be read or is empty; a prompt
            variant must never be applied silently.
    """
    source = os.environ if env is None else env
    raw = source.get(SYSTEM_PROMPT_FILE_ENV, "")
    if not raw:
        return BUILTIN_SYSTEM_PROMPT
    path = Path(raw)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentError(f"cannot read {SYSTEM_PROMPT_FILE_ENV} file {path}: {exc}") from exc
    if not text.strip():
        raise AgentError(f"{SYSTEM_PROMPT_FILE_ENV} file {path} is empty; remove the override or fill it")
    return SystemPrompt(source="file", path=str(path), text=text, sha256=_sha256(text))


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
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatReply:
    """Send one non-streamed ``/api/chat`` request.

    ``transport`` is an injectable seam: deterministic tests pass an
    ``httpx.MockTransport``; the default opens a real connection.

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
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
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


class SessionLike(Protocol):
    """The ``ClientSession`` surface the harness uses; deterministic tests fake it."""

    async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult:
        """Execute one tool call on the session."""
        ...

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
        """Render one MCP prompt on the session."""
        ...


SessionFactory = Callable[[AsyncExitStack, StdioServerParameters], Awaitable[tuple[SessionLike, list[Tool]]]]
ChatFunction = Callable[
    [OllamaSettings, Sequence[Mapping[str, object]], Sequence[Mapping[str, object]], float],
    Awaitable[ChatReply],
]


async def _stdio_session(stack: AsyncExitStack, params: StdioServerParameters) -> tuple[SessionLike, list[Tool]]:
    """The real session factory: spawn ``lion serve`` and speak stdio through the SDK."""
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    _ = await session.initialize()
    tools = (await session.list_tools()).tools
    return session, tools


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
    normalized: bool
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
class AttemptMetrics:
    """Per-attempt model metrics; the aggregate reuses the same names.

    Definitions (all over the scenario's own call history):

    - ``expected_tool``: the scenario's primary tool (the first required one,
      else the first alternative); ``""`` when the scenario expects none.
    - ``first_tool_hit``: the very first executed call is the expected tool
      (an efficiency/discoverability measure, not a task judgement).
    - ``expected_tool_called`` / ``expected_tool_succeeded``: any call / any
      successful call of the expected tool.
    - ``arguments_valid_before_normalization``: every call delivered arguments
      in the shape the API expects (nothing had to be parsed or cleaned).
    - ``calls_count`` / ``tool_errors``: executed calls and their error results.
    - ``repeated_calls``: calls whose ``(name, arguments)`` already appeared
      earlier in the same attempt.
    - ``done_reasons``: the ``done_reason`` of every model reply, in order.
    - ``empty_final_answer``: the model finished its loop with empty final text.
    """

    expected_tool: str = ""
    first_tool_hit: bool = False
    expected_tool_called: bool = False
    expected_tool_succeeded: bool = False
    arguments_valid_before_normalization: bool = False
    calls_count: int = 0
    tool_errors: int = 0
    repeated_calls: int = 0
    done_reasons: tuple[str, ...] = ()
    empty_final_answer: bool = False

    def as_json(self) -> dict[str, object]:
        """The JSON shape of one attempt's metrics."""
        return {
            "expected_tool": self.expected_tool,
            "first_tool_hit": self.first_tool_hit,
            "expected_tool_called": self.expected_tool_called,
            "expected_tool_succeeded": self.expected_tool_succeeded,
            "arguments_valid_before_normalization": self.arguments_valid_before_normalization,
            "calls_count": self.calls_count,
            "tool_errors": self.tool_errors,
            "repeated_calls": self.repeated_calls,
            "done_reasons": list(self.done_reasons),
            "empty_final_answer": self.empty_final_answer,
        }


def _expected_tool(scenario: Scenario) -> str:
    """The scenario's primary tool: first required, else first alternative."""
    if scenario.required_tools:
        return scenario.required_tools[0]
    if scenario.required_any:
        return scenario.required_any[0]
    return ""


def _attempt_metrics(
    scenario: Scenario,
    calls: Sequence[ToolCallRecord],
    *,
    done_reasons: Sequence[str] = (),
    empty_final_answer: bool = False,
) -> AttemptMetrics:
    """Compute the per-attempt metrics over one call history."""
    expected = _expected_tool(scenario)
    names = [call.name for call in calls]
    seen: set[str] = set()
    repeated = 0
    for call in calls:
        key = json.dumps([call.name, call.arguments], sort_keys=True, ensure_ascii=False)
        if key in seen:
            repeated += 1
        seen.add(key)
    return AttemptMetrics(
        expected_tool=expected,
        first_tool_hit=bool(expected) and names[:1] == [expected],
        expected_tool_called=bool(expected) and expected in names,
        expected_tool_succeeded=any(call.name == expected and not call.is_error for call in calls),
        arguments_valid_before_normalization=bool(calls) and all(not call.normalized for call in calls),
        calls_count=len(calls),
        tool_errors=sum(1 for call in calls if call.is_error),
        repeated_calls=repeated,
        done_reasons=tuple(done_reasons),
        empty_final_answer=empty_final_answer,
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
    done_reasons: tuple[str, ...]
    user_text_sha256: str
    tool_inventory_sha256: str
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

    def target_tool_names(self) -> tuple[str, ...]:
        """The scenario's target tools: every required plus every alternative."""
        return tuple(dict.fromkeys((*self.scenario.required_tools, *self.scenario.required_any)))

    def recovery_metrics(self, *, task_passed: bool) -> RecoveryMetrics:
        """The recovery metrics of this run."""
        return _recovery_metrics(self.calls, frozenset(self.target_tool_names()), task_passed=task_passed)

    def attempt_metrics(self) -> AttemptMetrics:
        """The per-attempt model metrics over this run's call history."""
        return _attempt_metrics(
            self.scenario,
            self.calls,
            done_reasons=self.done_reasons,
            empty_final_answer=not self.final_text.strip(),
        )

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
        done_reasons: Sequence[str] = (),
        user_text_sha256: str = "",
        tool_inventory_sha256: str = "",
        transcript: Path | None = None,
    ) -> None:
        """Record the termination reason and the partial run data."""
        super().__init__(message)
        self.termination = termination
        self.calls = tuple(calls)
        self.steps = steps
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count
        self.done_reasons = tuple(done_reasons)
        self.user_text_sha256 = user_text_sha256
        self.tool_inventory_sha256 = tool_inventory_sha256
        self.transcript = transcript
        self.teardown_seconds = 0.0


# --- agent loop --------------------------------------------------------------


def _lion_script() -> Path:
    """The installed console script, or a loud setup error."""
    script = Path(sys.executable).with_name("lion")
    if not script.exists():
        raise AgentError(f"missing console script: {script}; run 'uv sync' first")
    return script


def server_command() -> list[str]:
    """The installed console script, started exactly like a client would."""
    return [str(_lion_script()), "serve"]


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


async def _call_tool(session: SessionLike, call: ToolCall) -> tuple[bool, str]:
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
    done_reasons: list[str] = field(default_factory=list[str])
    steps: int = 0
    prompt_eval_count: int = 0
    eval_count: int = 0
    user_text_sha256: str = ""
    tool_inventory_sha256: str = ""
    teardown_seconds: float = 0.0


@dataclass
class _DriveOutcome:
    """What one completed model loop produced (internal to the harness)."""

    final_text: str
    calls: tuple[ToolCallRecord, ...]
    steps: int
    prompt_eval_count: int
    eval_count: int
    done_reasons: tuple[str, ...]
    user_text_sha256: str
    tool_inventory_sha256: str
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
        done_reasons=tuple(state.done_reasons),
        user_text_sha256=state.user_text_sha256,
        tool_inventory_sha256=state.tool_inventory_sha256,
    )


def _timeout_failure(state: _LoopState, scenario: Scenario, *, between_calls: bool = False) -> ScenarioRunError:
    """The scenario-timeout failure with the place the deadline hit."""
    suffix = " between tool calls" if between_calls else ""
    return _state_failure(
        state,
        "scenario_timeout",
        f"scenario {scenario.name!r} exceeded {SCENARIO_TIMEOUT_SECONDS:.0f}s{suffix}",
    )


async def _drive_session(
    stack: AsyncExitStack,
    state: _LoopState,
    scenario: Scenario,
    settings: OllamaSettings,
    server_env: Mapping[str, str],
    transcript: Transcript,
    *,
    system_prompt: SystemPrompt,
    session_factory: SessionFactory,
    chat_function: ChatFunction,
) -> _DriveOutcome:
    """Start the server session, run the model loop, and return the outcome."""
    deadline = time.monotonic() + SCENARIO_TIMEOUT_SECONDS
    try:
        command = server_command()
        params = StdioServerParameters(command=command[0], args=["serve"], env=dict(server_env))
        session, tools = await session_factory(stack, params)
        user_text = scenario.request
        if scenario.prompt_name:
            prompt = await session.get_prompt(scenario.prompt_name, dict(scenario.prompt_arguments))
            content = prompt.messages[0].content
            user_text = content.text if isinstance(content, TextContent) else str(content)
    except Exception as exc:
        raise _state_failure(state, "startup_error", f"scenario {scenario.name!r} failed to start: {exc}") from exc
    ollama_tools = to_ollama_tools(tools)
    state.user_text_sha256 = _sha256(user_text)
    state.tool_inventory_sha256 = inventory_sha256(ollama_tools)
    transcript.event(
        "tools",
        count=len(tools),
        names=[tool.name for tool in tools],
        sha256=state.tool_inventory_sha256,
    )
    transcript.event("system", text=system_prompt.text)
    transcript.event("user", text=user_text)
    messages: list[dict[str, object]] = [
        {"role": "system", "content": system_prompt.text},
        {"role": "user", "content": user_text},
    ]
    for step in range(1, MAX_STEPS + 1):
        state.steps = step
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _timeout_failure(state, scenario)
        transcript.event(
            "chat_request",
            messages=[dict(message) for message in messages],
            normalizations=[dict(item) for item in state.normalizations],
        )
        try:
            reply = await chat_function(settings, messages, ollama_tools, min(CHAT_TIMEOUT_SECONDS, remaining))
        except ChatTimeoutError as exc:
            raise _state_failure(state, "chat_timeout", str(exc)) from exc
        except ChatTransportError as exc:
            raise _state_failure(state, "transport_error", str(exc)) from exc
        except AgentError as exc:
            raise _state_failure(state, "malformed_output", str(exc)) from exc
        state.prompt_eval_count += reply.prompt_eval_count
        state.eval_count += reply.eval_count
        state.done_reasons.append(reply.done_reason)
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
                done_reasons=tuple(state.done_reasons),
                user_text_sha256=state.user_text_sha256,
                tool_inventory_sha256=state.tool_inventory_sha256,
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
                raise _timeout_failure(state, scenario, between_calls=True)
            started = time.monotonic()
            is_error, text = await _call_tool(session, call)
            duration = time.monotonic() - started
            state.calls.append(
                ToolCallRecord(
                    name=call.name,
                    arguments=call.arguments,
                    normalized=call.normalized,
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
    *,
    system_prompt: SystemPrompt,
    session_factory: SessionFactory,
    chat_function: ChatFunction,
) -> _DriveOutcome:
    """Run the stdio session inside the execution budget and measure its teardown."""
    state = _LoopState()
    stack = AsyncExitStack()
    outcome: _DriveOutcome | None = None
    failure: BaseException | None = None
    try:
        try:
            async with asyncio.timeout(SCENARIO_TIMEOUT_SECONDS):
                outcome = await _drive_session(
                    stack,
                    state,
                    scenario,
                    settings,
                    server_env,
                    transcript,
                    system_prompt=system_prompt,
                    session_factory=session_factory,
                    chat_function=chat_function,
                )
        except TimeoutError as exc:
            raise _timeout_failure(state, scenario) from exc
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
    system_prompt: SystemPrompt,
    *,
    session_factory: SessionFactory = _stdio_session,
    chat_function: ChatFunction = chat,
) -> ScenarioRun:
    """Run one scenario end to end and return its outcome.

    ``session_factory`` and ``chat_function`` default to the real stdio session
    and the real Ollama chat; deterministic tests inject fakes.

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
            outcome = asyncio.run(
                _drive(
                    scenario,
                    settings,
                    server_env,
                    transcript,
                    system_prompt=system_prompt,
                    session_factory=session_factory,
                    chat_function=chat_function,
                )
            )
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
        done_reasons=outcome.done_reasons,
        user_text_sha256=outcome.user_text_sha256,
        tool_inventory_sha256=outcome.tool_inventory_sha256,
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
    script = _lion_script()
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
    """One isolated ledger plus the agent runner bound to it.

    ``session_factory`` and ``chat_function`` default to the real stdio session
    and the real Ollama chat; deterministic tests replace them with fakes.
    """

    data_home: Path
    server_env: dict[str, str]
    settings: OllamaSettings
    artifacts: Path
    system_prompt: SystemPrompt
    session_factory: SessionFactory = _stdio_session
    chat_function: ChatFunction = chat

    def run(self, scenario: Scenario) -> ScenarioRun:
        """Run one scenario against this environment."""
        return run_scenario(
            scenario,
            self.settings,
            self.data_home,
            self.artifacts,
            self.server_env,
            self.system_prompt,
            session_factory=self.session_factory,
            chat_function=self.chat_function,
        )


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
    prompt_name: str = ""
    user_text_sha256: str = ""
    tool_inventory_sha256: str = ""
    first_attempt_success: bool = False
    failed_target_calls: int = 0
    retries: int = 0
    recovered: bool = False
    metrics: AttemptMetrics = field(default_factory=AttemptMetrics)
    notes: str = ""

    def __post_init__(self) -> None:
        """Refuse a termination outside the closed vocabulary."""
        if self.termination not in TERMINATIONS:
            raise AgentError(f"unknown termination reason: {self.termination!r}; add it to TERMINATIONS")

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
            "prompt_name": self.prompt_name,
            "user_text_sha256": self.user_text_sha256,
            "tool_inventory_sha256": self.tool_inventory_sha256,
            "first_attempt_success": self.first_attempt_success,
            "failed_target_calls": self.failed_target_calls,
            "retries": self.retries,
            "recovered": self.recovered,
            "metrics": self.metrics.as_json(),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class _PendingAttempt:
    """One attempt that was registered but whose outcome is not final yet."""

    name: str
    request: str
    hard: bool
    started_at: str

    def as_json(self) -> dict[str, object]:
        """The in-flight attempt as published in the report checkpoint."""
        return {"name": self.name, "request": self.request, "hard": self.hard, "started_at": self.started_at}


def _record_base(
    pending: _PendingAttempt,
    *,
    outcome: AttemptOutcome,
    failure: str,
    termination: str,
) -> AttemptRecord:
    """The fields every final record shares, before the branch-specific ones."""
    return AttemptRecord(
        name=pending.name,
        request=pending.request,
        hard=pending.hard,
        started_at=pending.started_at,
        outcome=outcome,
        failure=failure,
        termination=termination,
    )


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


def _ratio(numerator: int, denominator: int) -> dict[str, object]:
    """A ratio with its explicit denominator (``None`` when there is none)."""
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def _aggregate_metrics(entries: Sequence[AttemptRecord]) -> dict[str, object]:
    """Aggregate the per-attempt metrics with explicit denominators.

    Every attempt counts, including the report-only matrix and harness errors:
    the outcomes stay visible as their own counts, and the ratios never replace
    them. Task completion (the checks) and answer quality stay separate. The
    ``done_reasons`` histogram keeps its reply and abnormal totals namespaced,
    so a model reason can never shadow them.
    """
    expected = [entry for entry in entries if entry.metrics.expected_tool]
    with_calls = [entry for entry in entries if entry.metrics.calls_count > 0]
    completed = [entry for entry in entries if entry.termination == "completed"]
    recovery_opportunities = [entry for entry in entries if entry.failed_target_calls > 0]
    reason_counts: dict[str, int] = {}
    replies = 0
    abnormal = 0
    for entry in entries:
        for reason in entry.metrics.done_reasons:
            replies += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if reason != NORMAL_DONE_REASON:
                abnormal += 1
    calls_total = sum(entry.metrics.calls_count for entry in entries)
    return {
        "attempts": {
            "total": len(entries),
            "passed": sum(1 for entry in entries if entry.outcome is AttemptOutcome.PASSED),
            "failed": sum(1 for entry in entries if entry.outcome is AttemptOutcome.FAILED),
            "error": sum(1 for entry in entries if entry.outcome is AttemptOutcome.ERROR),
            "hard": sum(1 for entry in entries if entry.hard),
            "report_only": sum(1 for entry in entries if not entry.hard),
        },
        "task_completed": _ratio(sum(1 for entry in entries if entry.outcome is AttemptOutcome.PASSED), len(entries)),
        "first_tool_hit": _ratio(sum(1 for entry in expected if entry.metrics.first_tool_hit), len(expected)),
        "expected_tool_called": _ratio(
            sum(1 for entry in expected if entry.metrics.expected_tool_called), len(expected)
        ),
        "expected_tool_succeeded": _ratio(
            sum(1 for entry in expected if entry.metrics.expected_tool_succeeded), len(expected)
        ),
        "arguments_valid_before_normalization": _ratio(
            sum(1 for entry in with_calls if entry.metrics.arguments_valid_before_normalization), len(with_calls)
        ),
        "first_attempt_success": _ratio(sum(1 for entry in expected if entry.first_attempt_success), len(expected)),
        "recovered_success": _ratio(
            sum(1 for entry in recovery_opportunities if entry.recovered), len(recovery_opportunities)
        ),
        "tool_errors": _ratio(sum(entry.metrics.tool_errors for entry in entries), calls_total),
        "repeated_calls": _ratio(sum(entry.metrics.repeated_calls for entry in entries), calls_total),
        "empty_final_answer": _ratio(sum(1 for entry in completed if entry.metrics.empty_final_answer), len(completed)),
        "done_reasons": {"counts": reason_counts, "replies": replies, "abnormal": abnormal},
        "duration_seconds": {
            "total": round(sum(entry.duration_seconds for entry in entries), 3),
            "max": round(max((entry.duration_seconds for entry in entries), default=0.0), 3),
        },
        "teardown_seconds": {
            "total": round(sum(entry.teardown_seconds for entry in entries), 3),
            "max": round(max((entry.teardown_seconds for entry in entries), default=0.0), 3),
        },
        "prompt_eval_count": sum(entry.prompt_eval_count for entry in entries),
        "eval_count": sum(entry.eval_count for entry in entries),
        "steps": {
            "total": sum(entry.steps for entry in entries),
            "max": max((entry.steps for entry in entries), default=0),
        },
    }


def _package_version() -> str:
    """The installed lion version, or ``""`` outside an installation."""
    try:
        return importlib.metadata.version("lion")
    except importlib.metadata.PackageNotFoundError:
        return ""


def _git_output(*args: str) -> str | None:
    """Best-effort git output in the repository; ``None`` on any failure."""
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def collect_environment(settings: OllamaSettings) -> dict[str, object]:
    """Best-effort run metadata: package and git state plus the Ollama build.

    The git fields are ``None`` when git is unavailable or the directory is not
    a repository; that never fails the suite. The Ollama version stays loud:
    this is called after the preflight succeeded.
    """
    version = _http_json(f"{settings.host}/api/version")
    dirty = _git_output("status", "--porcelain", "--untracked-files=no")
    return {
        "lion_version": _package_version(),
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_dirty": None if dirty is None else bool(dirty),
        "ollama_version": str(version.get("version", "")),
    }


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
        self._system_prompt: SystemPrompt | None = None
        self._environment: dict[str, object] = {}
        self._suite: dict[str, object] = {}
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

    def set_system_prompt(self, prompt: SystemPrompt) -> None:
        """Record the resolved system prompt (source, text, hash)."""
        self._system_prompt = prompt

    def set_environment(self, environment: Mapping[str, object]) -> None:
        """Record the run environment (versions and the git state)."""
        self._environment = dict(environment)

    def set_suite(self, identity: Mapping[str, object]) -> None:
        """Pin the suite identity (scenario definitions plus fixture seeds)."""
        self._suite = dict(identity)

    def mark_preflight_error(self, message: str) -> None:
        """Record a preflight failure (no attempt ran) and write the report."""
        self._state = "preflight_error"
        self._finished_at = _utc_now()
        self._extras["preflight_error"] = message
        self.write()

    def begin_attempt(self, name: str, request: str, hard: bool) -> _PendingAttempt:
        """Register one attempt before it starts and checkpoint the report."""
        if self._pending is not None:
            raise RuntimeError(f"attempt {self._pending.name!r} is still open; finish it before starting {name!r}")
        self._pending = _PendingAttempt(name=name, request=request, hard=hard, started_at=_utc_now())
        self.write()
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
        """Write the report atomically: temp file, fsync, ``os.replace``.

        Raises:
            AgentError: If the run state is outside the closed vocabulary.
        """
        if self._state not in RUN_STATES:
            raise AgentError(f"unknown run state: {self._state!r}; add it to RUN_STATES")
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
            "think": {"sent": False, "effective": "model default"},
            "system_prompt": None if self._system_prompt is None else self._system_prompt.as_json(),
            "environment": dict(self._environment),
            "suite": dict(self._suite),
            "pending": None if self._pending is None else self._pending.as_json(),
            "entries": [entry.as_json() for entry in self._entries],
            "metrics": _aggregate_metrics(self._entries),
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
                replace(
                    _record_base(
                        pending,
                        outcome=AttemptOutcome.ERROR,
                        failure="attempt interrupted before completion",
                        termination="interrupted",
                    ),
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
    base = _record_base(pending, outcome=outcome, failure=failure_text, termination=termination)
    if run is not None:
        metrics = run.recovery_metrics(task_passed=task_passed)
        return replace(
            base,
            tool_calls=tuple(run.tool_names()),
            steps=run.steps,
            duration_seconds=round(run.duration_seconds, 3),
            teardown_seconds=round(run.teardown_seconds, 3),
            prompt_eval_count=run.prompt_eval_count,
            eval_count=run.eval_count,
            final_text=run.final_text,
            transcript=str(run.transcript),
            prompt_name=scenario.prompt_name,
            user_text_sha256=run.user_text_sha256,
            tool_inventory_sha256=run.tool_inventory_sha256,
            first_attempt_success=metrics.first_attempt_success,
            failed_target_calls=metrics.failed_target_calls,
            retries=metrics.retries,
            recovered=metrics.recovered,
            metrics=run.attempt_metrics(),
        )
    target = frozenset(
        dict.fromkeys((*scenario.required_tools, *scenario.required_any)),
    )
    if failure is not None:
        metrics = _recovery_metrics(failure.calls, target, task_passed=False)
        return replace(
            base,
            tool_calls=tuple(call.name for call in failure.calls),
            steps=failure.steps,
            duration_seconds=round(duration_seconds, 3),
            teardown_seconds=round(failure.teardown_seconds, 3),
            prompt_eval_count=failure.prompt_eval_count,
            eval_count=failure.eval_count,
            transcript="" if failure.transcript is None else str(failure.transcript),
            prompt_name=scenario.prompt_name,
            user_text_sha256=failure.user_text_sha256,
            tool_inventory_sha256=failure.tool_inventory_sha256,
            first_attempt_success=metrics.first_attempt_success,
            failed_target_calls=metrics.failed_target_calls,
            retries=metrics.retries,
            recovered=metrics.recovered,
            metrics=_attempt_metrics(scenario, failure.calls, done_reasons=failure.done_reasons),
        )
    return replace(
        base,
        duration_seconds=round(duration_seconds, 3),
        prompt_name=scenario.prompt_name,
        metrics=_attempt_metrics(scenario, ()),
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
