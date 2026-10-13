"""Agentic QA scenarios: a local LLM drives ``lion serve`` over stdio.

Opt-in via ``just test-agent`` (marker ``agentic``): advisory, never part of
``just check`` or CI. The scenarios and their hard checks live in
:mod:`tests.agentic.suite` (shared with the deterministic harness regression
tests); this module binds them to the live Ollama environment. Every attempt
records exactly one outcome in ``report.json`` through ``run_attempt``;
answer quality and the tool-selection ratio live there next to the
per-scenario transcripts and never fail the run.
"""

import pytest

from tests.agentic.agent import (
    AgentEnvironment,
    AgentReport,
    AttemptOutcome,
    run_attempt,
)
from tests.agentic.suite import (
    MATRIX,
    SCENARIO_ADR_WRITE,
    SCENARIO_DIFF,
    SCENARIO_HISTORY,
    SCENARIO_MISSING_RECORDING,
    SCENARIO_REDACTION,
    SCENARIO_REDACTION_OFF,
    SCENARIO_REVIEW,
    SCENARIO_SEARCH,
)

pytestmark = pytest.mark.agentic


# --- scenarios 1-7 -----------------------------------------------------------


def test_history_overview(agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport) -> None:
    """A history or snapshot call answers from the two stored states."""
    run_attempt(SCENARIO_HISTORY, agent_env, agent_report, hard=True)


def test_compare_two_states(agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport) -> None:
    """The model compares A to B through ``lion_diff``."""
    run_attempt(SCENARIO_DIFF, agent_env, agent_report, hard=True)


def test_review_prompt_compares_two_states(
    agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport
) -> None:
    """The rendered review prompt drives the fixed A-to-B comparison."""
    run_attempt(SCENARIO_REVIEW, agent_env, agent_report, hard=True)


def test_search_finds_unique_token(agent_env: AgentEnvironment, search_adr: str, agent_report: AgentReport) -> None:
    """``lion_search`` finds the seeded token."""
    run_attempt(SCENARIO_SEARCH, agent_env, agent_report, hard=True)


def test_adr_write_path(agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport) -> None:
    """The ADR write path publishes a valid document and writes nothing else."""
    run_attempt(SCENARIO_ADR_WRITE, agent_env, agent_report, hard=True)


def test_missing_recording_is_delivered_as_error(
    agent_env: AgentEnvironment, two_states: None, agent_report: AgentReport
) -> None:
    """Without recordings the server error reaches the model as an error result."""
    run_attempt(SCENARIO_MISSING_RECORDING, agent_env, agent_report, hard=True)


def test_redaction_hides_recorded_secret(
    agent_env: AgentEnvironment, secret_recording: str, agent_report: AgentReport
) -> None:
    """The recorded secret never reaches the model-visible transcript."""
    run_attempt(SCENARIO_REDACTION, agent_env, agent_report, hard=True)


def test_redaction_off_variant_shows_the_difference(
    agent_env: AgentEnvironment, secret_recording: str, agent_report: AgentReport
) -> None:
    """Report-only: with ``LION_SERVE_REDACT=0`` the raw secret becomes visible."""
    agent_env.server_env["LION_SERVE_REDACT"] = "0"
    record = run_attempt(SCENARIO_REDACTION_OFF, agent_env, agent_report, hard=False)
    agent_report.set_extra("redaction_off_secret_visible", record.outcome is AttemptOutcome.PASSED)


# --- scenario 8: tool-selection matrix ---------------------------------------


def test_tool_selection_matrix(
    agent_env: AgentEnvironment,
    two_states: None,
    search_adr: str,
    secret_recording: str,
    agent_report: AgentReport,
) -> None:
    """Nine read intents, one expected first tool each — the hit ratio is reported only."""
    hits = 0
    for scenario in MATRIX:
        record = run_attempt(scenario, agent_env, agent_report, hard=False)
        hits += int(record.metrics.first_tool_hit)
    ratio = hits / len(MATRIX)
    agent_report.set_extra(
        "tool_selection",
        {
            "hits": hits,
            "total": len(MATRIX),
            "ratio": ratio,
            "expected_first_tool": {scenario.name: scenario.required_tools[0] for scenario in MATRIX},
        },
    )
    print(f"\ntool selection: {hits}/{len(MATRIX)} first-tool hits ({ratio:.0%})")
