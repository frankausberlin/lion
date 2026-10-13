"""Agentic QA scenarios: a local LLM drives ``lion serve`` over stdio.

Opt-in via ``just test-agent`` (marker ``agentic``): advisory, never part of
``just check`` or CI. Hard assertions stay model-independent — a required tool
was called, the ADR write path produced a valid document, no recorded secret
reached the model-visible text, a server error was delivered as an error.
Answer quality and the tool-selection ratio live in ``report.json`` next to the
per-scenario transcripts.
"""

import stat
from pathlib import Path
from typing import cast

import pytest
from agent import (
    AgentEnvironment,
    AgentError,
    AgentReport,
    ReportEntry,
    Scenario,
    ScenarioRun,
    ToolCallRecord,
    cli_json,
    evaluate,
    transcript_text,
)
from ledger import SEARCH_TOKEN, SECRET

from tests.ledger_fixtures import A_REF, B_REF

pytestmark = pytest.mark.agentic


# --- helpers -----------------------------------------------------------------


def _mapping(value: object, what: str) -> dict[str, object]:
    assert isinstance(value, dict), f"{what} is not a JSON object: {value!r}"
    return cast("dict[str, object]", value)


def _ref(value: object, what: str) -> str:
    ref = _mapping(value, what).get("ref")
    assert isinstance(ref, str) and ref, f"{what} carries no reference: {value!r}"
    return ref


def _successful_call(run: ScenarioRun, name: str) -> ToolCallRecord:
    call = run.first_call(name)
    assert call is not None, f"{name} was not called; called: {run.tool_names()}"
    assert not call.is_error, f"{name} failed: {call.result_text[:300]}"
    return call


def _payload(call: ToolCallRecord) -> dict[str, object]:
    payload = call.result_json()
    assert payload is not None, f"{call.name} returned no JSON object: {call.result_text[:200]}"
    return payload


# --- scenario checks ---------------------------------------------------------


def _check_history_delivered(run: ScenarioRun) -> None:
    assert any(not call.is_error for call in run.calls), "no successful tool result was delivered"


def _check_diff_a_to_b(run: ScenarioRun) -> None:
    payload = _payload(_successful_call(run, "lion_diff"))
    from_ref = _ref(payload.get("from"), "diff.from")
    to_ref = _ref(payload.get("to"), "diff.to")
    assert (from_ref, to_ref) == (A_REF, B_REF), f"expected the A to B diff, got {from_ref} -> {to_ref}"
    assert payload.get("changed") is True, f"the diff reported no change: {payload}"


def _check_search_hit(run: ScenarioRun) -> None:
    call = _successful_call(run, "lion_search")
    hits = _payload(call).get("hits")
    assert isinstance(hits, list) and hits, f"lion_search returned no hits: {call.result_text[:300]}"
    assert SEARCH_TOKEN in call.result_text, f"the token is missing from the result: {call.result_text[:300]}"


def _check_adr_write(run: ScenarioRun) -> None:
    payload = _payload(_successful_call(run, "lion_adr_add"))
    ref = payload.get("ref")
    raw_path = payload.get("path")
    assert isinstance(ref, str) and ref, f"lion_adr_add returned no reference: {payload}"
    assert isinstance(raw_path, str) and raw_path, f"lion_adr_add returned no path: {payload}"
    path = Path(raw_path)
    assert path.is_file(), f"the ADR file does not exist: {path}"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"the ADR file mode is {oct(mode)}, expected 0o600"
    shown = cli_json(run.context.server_env, "adr", "show", ref)
    fields = shown.get("fields")
    assert isinstance(fields, dict) and fields, f"lion adr show read no fields back: {shown}"
    expected = str(path.relative_to(run.context.data_home))
    changed = run.context.changed_files()
    assert changed == {expected}, f"unexpected ledger writes: {sorted(changed)}"


def _check_missing_recording_error(run: ScenarioRun) -> None:
    calls = run.calls_of("lion_recording")
    assert calls, f"lion_recording was not called: {run.tool_names()}"
    assert any(call.is_error for call in calls), "the missing-recording result was not delivered as an error"
    assert any("No recording stored" in call.result_text for call in calls), (
        "the error text does not name the missing recording"
    )


def _check_redaction(run: ScenarioRun) -> None:
    calls = run.calls_of("lion_recording")
    assert calls, f"lion_recording was not called: {run.tool_names()}"
    assert any("***" in call.result_text for call in calls), "no masked value appeared in the tool results"
    assert SECRET not in transcript_text(run), "the recorded secret reached the model-visible transcript"


# --- scenarios ---------------------------------------------------------------


SCENARIO_HISTORY = Scenario(
    name="history-overview",
    request="How many system states are stored? Give a one-sentence overview.",
    required_any=("lion_history", "lion_snapshot"),
    check=_check_history_delivered,
)
SCENARIO_DIFF = Scenario(
    name="compare-two-states",
    request="Compare the two stored system states and summarize what changed.",
    required_tools=("lion_diff",),
    check=_check_diff_a_to_b,
)
SCENARIO_REVIEW = Scenario(
    name="review-prompt",
    request="review_latest_changes prompt rendered as the user turn",
    required_tools=("lion_diff",),
    prompt_name="review_latest_changes",
    check=_check_diff_a_to_b,
)
SCENARIO_SEARCH = Scenario(
    name="search-token",
    request=f"Search all records for {SEARCH_TOKEN} and report what you found.",
    required_tools=("lion_search",),
    check=_check_search_hit,
)
SCENARIO_ADR_WRITE = Scenario(
    name="adr-write",
    request=(
        "Record this decision with the ADR tool: the lion MCP server speaks stdio only. "
        "Use a clear title and the decision text."
    ),
    required_tools=("lion_adr_add",),
    check=_check_adr_write,
)
SCENARIO_MISSING_RECORDING = Scenario(
    name="missing-recording",
    request="Open the latest shell recording and tell me what commands it contains.",
    required_tools=("lion_recording",),
    check=_check_missing_recording_error,
)
SCENARIO_REDACTION = Scenario(
    name="redaction",
    request="Show me the latest shell recording and its commands.",
    required_tools=("lion_recording",),
    check=_check_redaction,
)
SCENARIO_REDACTION_OFF = Scenario(
    name="redaction-off",
    request="Show me the latest shell recording and its commands.",
    required_tools=("lion_recording",),
)

MATRIX: tuple[Scenario, ...] = (
    Scenario(name="matrix-history", request="How many system states are stored?", required_tools=("lion_history",)),
    Scenario(
        name="matrix-snapshot",
        request="Show the newest stored state in full.",
        required_tools=("lion_snapshot",),
    ),
    Scenario(name="matrix-diff", request="What changed between the two stored states?", required_tools=("lion_diff",)),
    Scenario(name="matrix-recordings", request="Which shell recordings exist?", required_tools=("lion_recordings",)),
    Scenario(name="matrix-recording", request="Show the latest shell recording.", required_tools=("lion_recording",)),
    Scenario(
        name="matrix-classify",
        request="Which known commands appear in the latest recording?",
        required_tools=("lion_classify",),
    ),
    Scenario(name="matrix-adr-list", request="Which decisions (ADRs) are recorded?", required_tools=("lion_adr_list",)),
    Scenario(
        name="matrix-adr-show",
        request="Show the latest recorded decision (ADR).",
        required_tools=("lion_adr_show",),
    ),
    Scenario(name="matrix-search", request=f"Search everything for {SEARCH_TOKEN}.", required_tools=("lion_search",)),
)


# --- scenarios 1-7 -----------------------------------------------------------


def test_history_overview(agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport) -> None:
    """A history or snapshot call answers from the two stored states."""
    run = agent_env.run(SCENARIO_HISTORY)
    evaluate(run, agent_report)


def test_compare_two_states(agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport) -> None:
    """The model compares A to B through ``lion_diff``."""
    run = agent_env.run(SCENARIO_DIFF)
    evaluate(run, agent_report)


def test_review_prompt_compares_two_states(
    agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport
) -> None:
    """The rendered review prompt drives the fixed A-to-B comparison."""
    run = agent_env.run(SCENARIO_REVIEW)
    evaluate(run, agent_report)


def test_search_finds_unique_token(agent_env: AgentEnvironment, search_adr: str, agent_report: AgentReport) -> None:
    """``lion_search`` finds the seeded token."""
    run = agent_env.run(SCENARIO_SEARCH)
    evaluate(run, agent_report)


def test_adr_write_path(agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport) -> None:
    """The ADR write path publishes a valid document and writes nothing else."""
    run = agent_env.run(SCENARIO_ADR_WRITE)
    evaluate(run, agent_report)


def test_missing_recording_is_delivered_as_error(
    agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport
) -> None:
    """Without recordings the server error reaches the model as an error result."""
    run = agent_env.run(SCENARIO_MISSING_RECORDING)
    evaluate(run, agent_report)


def test_redaction_hides_recorded_secret(
    agent_env: AgentEnvironment, secret_recording: str, agent_report: AgentReport
) -> None:
    """The recorded secret never reaches the model-visible transcript."""
    run = agent_env.run(SCENARIO_REDACTION)
    evaluate(run, agent_report)


def test_redaction_off_variant_shows_the_difference(
    agent_env: AgentEnvironment, secret_recording: str, agent_report: AgentReport
) -> None:
    """Report-only: with ``LION_SERVE_REDACT=0`` the raw secret becomes visible."""
    agent_env.server_env["LION_SERVE_REDACT"] = "0"
    run = agent_env.run(SCENARIO_REDACTION_OFF)
    visible = SECRET in transcript_text(run)
    agent_report.add(run.report_entry(hard=False, passed=visible, notes="secret visible with redaction disabled"))
    agent_report.set_extra("redaction_off_secret_visible", visible)


# --- scenario 8: tool-selection matrix ---------------------------------------


def test_tool_selection_matrix(
    agent_env: AgentEnvironment,
    two_states: None,
    search_adr: str,
    secret_recording: str,
    agent_report: AgentReport,
) -> None:
    """Nine read intents, one expected tool each — the hit ratio is reported only."""
    hits = 0
    for scenario in MATRIX:
        expected = scenario.required_tools[0]
        try:
            run = agent_env.run(scenario)
        except AgentError as exc:
            agent_report.add(
                ReportEntry(
                    name=scenario.name,
                    request=scenario.request,
                    hard=False,
                    passed=False,
                    tool_calls=[],
                    steps=0,
                    duration_seconds=0.0,
                    prompt_eval_count=0,
                    eval_count=0,
                    final_text="",
                    notes=f"harness error: {exc}",
                )
            )
            continue
        first = run.tool_names()[:1]
        hit = first == [expected]
        hits += int(hit)
        agent_report.add(
            run.report_entry(hard=False, passed=hit, notes=f"expected first tool: {expected}; first: {first}")
        )
    ratio = hits / len(MATRIX)
    agent_report.set_extra("tool_selection", {"hits": hits, "total": len(MATRIX), "ratio": ratio})
    print(f"\ntool selection: {hits}/{len(MATRIX)} first-tool hits ({ratio:.0%})")
