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

Nothing here imports Typer or the ``lion`` package: the server runs as a real
subprocess, exactly like a client would start it.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import cast

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
CHAT_TIMEOUT_SECONDS = 120.0
SCENARIO_TIMEOUT_SECONDS = 300.0
TRANSCRIPTS_DIR = "transcripts"

SYSTEM_PROMPT = (
    "You are a Linux operations assistant with access to the lion MCP tools. "
    "Answer only from tool results; never invent data. "
    "Call a tool whenever it can answer the request, then reply briefly."
)


class AgentError(RuntimeError):
    """A loud harness failure: endpoint, model, or a broken model interaction."""


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
    parsed: object = json.loads(text)
    if not isinstance(parsed, dict):
        raise AgentError(f"{what} is not a JSON object: {text[:200]!r}")
    return cast("dict[str, object]", parsed)


def _http_json(url: str, payload: Mapping[str, object] | None = None, timeout: float = 30.0) -> dict[str, object]:
    """Call Ollama and parse a JSON object; every failure is loud."""
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        excerpt = exc.read().decode("utf-8", errors="replace")[:400]
        raise AgentError(f"Ollama request to {url} failed with HTTP {exc.code}: {excerpt}") from exc
    except urllib.error.URLError as exc:
        raise AgentError(
            f"Ollama is not reachable at {url} ({exc.reason}); start it with 'ollama serve' "
            f"or point {HOST_ENV} at a running instance."
        ) from exc
    try:
        return _loads_object(body, f"Ollama response from {url}")
    except json.JSONDecodeError as exc:
        raise AgentError(f"Ollama response from {url} is not JSON: {body[:200]!r}") from exc


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
    """One tool call the model asked for, with its raw Ollama object."""

    name: str
    arguments: dict[str, object]
    raw: Mapping[str, object]


@dataclass(frozen=True)
class ChatReply:
    """One non-streamed assistant reply."""

    content: str
    tool_calls: tuple[ToolCall, ...]
    prompt_eval_count: int
    eval_count: int


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0


def _normalize_arguments(raw: object) -> dict[str, object]:
    """Accept object or JSON-string arguments and drop ``null`` values."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentError(f"model produced unparseable tool arguments: {raw[:200]!r}") from exc
    if not isinstance(raw, dict):
        raise AgentError(f"model produced non-object tool arguments: {raw!r}")
    arguments: dict[str, object] = {}
    for key, value in cast("dict[object, object]", raw).items():
        if value is not None:
            arguments[str(key)] = value
    return arguments


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


def chat(
    settings: OllamaSettings,
    messages: Sequence[Mapping[str, object]],
    tools: Sequence[Mapping[str, object]],
    timeout: float = CHAT_TIMEOUT_SECONDS,
) -> ChatReply:
    """Send one non-streamed ``/api/chat`` request.

    Raises:
        AgentError: On transport errors, HTTP errors (with a body excerpt) and
            malformed tool calls.
    """
    payload: dict[str, object] = {
        "model": settings.model,
        "messages": [dict(message) for message in messages],
        "tools": [dict(tool) for tool in tools],
        "stream": False,
        "options": settings.options.as_ollama(),
        "keep_alive": settings.options.keep_alive,
    }
    reply = _http_json(f"{settings.host}/api/chat", payload, timeout=timeout)
    message = _mapping(reply.get("message", {}))
    calls: list[ToolCall] = []
    for item in _sequence(message.get("tool_calls", []) or []):
        entry = _mapping(item)
        function = _mapping(entry.get("function", {}))
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise AgentError(f"model produced a tool call without a name: {item!r}")
        calls.append(ToolCall(name=name, arguments=_normalize_arguments(function.get("arguments")), raw=entry))
    content = message.get("content", "")
    return ChatReply(
        content=content if isinstance(content, str) else str(content),
        tool_calls=tuple(calls),
        prompt_eval_count=_int_value(reply.get("prompt_eval_count")),
        eval_count=_int_value(reply.get("eval_count")),
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
    hit when it is not empty.
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
class ScenarioRun:
    """Everything one scenario produced: transcript, calls and outcomes."""

    scenario: Scenario
    final_text: str
    calls: tuple[ToolCallRecord, ...]
    duration_seconds: float
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

    def first_call(self, name: str) -> ToolCallRecord | None:
        """The first executed call of one tool, or ``None``."""
        return next((call for call in self.calls if call.name == name), None)

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

    def report_entry(self, hard: bool, passed: bool, notes: str = "") -> "ReportEntry":
        """Summarize the run for ``report.json``."""
        return ReportEntry(
            name=self.scenario.name,
            request=self.scenario.request,
            hard=hard,
            passed=passed,
            tool_calls=self.tool_names(),
            steps=self.steps,
            duration_seconds=round(self.duration_seconds, 3),
            prompt_eval_count=self.prompt_eval_count,
            eval_count=self.eval_count,
            final_text=self.final_text,
            notes=notes,
        )


# --- report ------------------------------------------------------------------


@dataclass(frozen=True)
class ReportEntry:
    """One scenario outcome for ``report.json``."""

    name: str
    request: str
    hard: bool
    passed: bool
    tool_calls: list[str]
    steps: int
    duration_seconds: float
    prompt_eval_count: int
    eval_count: int
    final_text: str
    notes: str = ""

    def as_json(self) -> dict[str, object]:
        """The JSON shape written to ``report.json``."""
        return {
            "name": self.name,
            "request": self.request,
            "hard": self.hard,
            "passed": self.passed,
            "tool_calls": list(self.tool_calls),
            "steps": self.steps,
            "duration_seconds": self.duration_seconds,
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "final_text": self.final_text,
            "notes": self.notes,
        }


class AgentReport:
    """Collects scenario outcomes and writes ``report.json`` once."""

    def __init__(self, path: Path, settings: OllamaSettings, model: OllamaModel) -> None:
        """Bind the report to its artifact path and the verified model."""
        self._path = path
        self._settings = settings
        self._model = model
        self._entries: list[ReportEntry] = []
        self._extras: dict[str, object] = {}

    def add(self, entry: ReportEntry) -> None:
        """Append one scenario outcome."""
        self._entries.append(entry)

    def set_extra(self, key: str, value: object) -> None:
        """Attach a summary value (for example the tool-selection ratio)."""
        self._extras[key] = value

    def write(self) -> None:
        """Write the report atomically enough for an artifact directory."""
        payload: dict[str, object] = {
            "model": self._model.as_json(),
            "host": self._settings.host,
            "options": self._settings.options.as_json(),
            "entries": [entry.as_json() for entry in self._entries],
            "extra": dict(self._extras),
        }
        self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def evaluate(run: ScenarioRun, report: AgentReport, hard: bool = True) -> None:
    """Run the hard checks, record the outcome, and re-raise when hard.

    A hard failure is recorded as ``passed: false`` with the failure message in
    ``notes`` before the assertion is re-raised, so ``report.json`` stays
    truthful even for a failed run.
    """
    failure = ""
    try:
        run.assert_required_tools()
        run.run_check()
    except AssertionError as exc:
        failure = str(exc)
    report.add(run.report_entry(hard=hard, passed=not failure, notes=failure))
    if hard and failure:
        raise AssertionError(failure)


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


async def _drive(
    scenario: Scenario,
    settings: OllamaSettings,
    server_env: Mapping[str, str],
    transcript: Transcript,
    deadline: float,
) -> tuple[str, list[ToolCallRecord], int, int, int]:
    """Run the real stdio session and the model loop for one scenario."""
    params = StdioServerParameters(command=server_command()[0], args=["serve"], env=dict(server_env))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        _ = await session.initialize()
        tools = (await session.list_tools()).tools
        ollama_tools = to_ollama_tools(tools)
        user_text = scenario.request
        if scenario.prompt_name:
            prompt = await session.get_prompt(scenario.prompt_name, dict(scenario.prompt_arguments))
            content = prompt.messages[0].content
            user_text = content.text if isinstance(content, TextContent) else str(content)
        transcript.event("system", text=SYSTEM_PROMPT)
        transcript.event("user", text=user_text)
        messages: list[dict[str, object]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ]
        calls: list[ToolCallRecord] = []
        prompt_eval_total = 0
        eval_total = 0
        for step in range(1, MAX_STEPS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentError(f"scenario {scenario.name!r} exceeded {SCENARIO_TIMEOUT_SECONDS:.0f}s")
            reply = chat(settings, messages, ollama_tools, timeout=min(CHAT_TIMEOUT_SECONDS, remaining))
            prompt_eval_total += reply.prompt_eval_count
            eval_total += reply.eval_count
            transcript.event(
                "assistant",
                text=reply.content,
                tool_calls=[{"name": call.name, "arguments": call.arguments} for call in reply.tool_calls],
                prompt_eval_count=reply.prompt_eval_count,
                eval_count=reply.eval_count,
            )
            if not reply.tool_calls:
                transcript.event("final", text=reply.content)
                return reply.content, calls, step, prompt_eval_total, eval_total
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.content,
                    "tool_calls": [dict(call.raw) for call in reply.tool_calls],
                }
            )
            for call in reply.tool_calls:
                started = time.monotonic()
                is_error, text = await _call_tool(session, call)
                duration = time.monotonic() - started
                calls.append(
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
                    is_error=is_error,
                    text=text,
                    duration_seconds=duration,
                )
                messages.append({"role": "tool", "content": text})
        raise AgentError(
            f"scenario {scenario.name!r} did not finish within {MAX_STEPS} steps; "
            f"calls so far: {[call.name for call in calls]}"
        )


def run_scenario(
    scenario: Scenario,
    settings: OllamaSettings,
    data_home: Path,
    artifacts: Path,
    server_env: Mapping[str, str],
) -> ScenarioRun:
    """Run one scenario end to end and return its outcome.

    Raises:
        AgentError: On environment problems, malformed model output, a scenario
            timeout, or an agent loop that exceeds :data:`MAX_STEPS`.
    """
    before = _tree(data_home)
    transcript_path = artifacts / TRANSCRIPTS_DIR / f"{scenario.name}.jsonl"
    started = time.monotonic()
    deadline = started + SCENARIO_TIMEOUT_SECONDS
    with Transcript(transcript_path) as transcript:
        final_text, calls, steps, prompt_eval_total, eval_total = asyncio.run(
            _drive(scenario, settings, server_env, transcript, deadline)
        )
    duration = time.monotonic() - started
    context = ScenarioContext(
        data_home=data_home,
        server_env=server_env,
        before=before,
        after=_tree(data_home),
    )
    return ScenarioRun(
        scenario=scenario,
        final_text=final_text,
        calls=tuple(calls),
        duration_seconds=duration,
        prompt_eval_count=prompt_eval_total,
        eval_count=eval_total,
        steps=steps,
        transcript=transcript_path,
        context=context,
    )


def transcript_text(run: ScenarioRun) -> str:
    """The full transcript of a run, for the secret-leak proof."""
    return run.transcript.read_text(encoding="utf-8")


def cli_json(env: Mapping[str, str], *args: str) -> dict[str, object]:
    """Run the installed CLI with an isolated environment and parse ``--json``.

    Raises:
        AgentError: If the command fails or does not print a JSON object.
    """
    script = Path(sys.executable).with_name("lion")
    result = subprocess.run(
        [str(script), *args, "--json"],
        capture_output=True,
        text=True,
        env=dict(env),
        check=False,
    )
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
