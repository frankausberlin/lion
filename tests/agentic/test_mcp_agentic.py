"""Agentic QA scenarios: a local LLM drives ``lion serve`` over stdio.

Opt-in via ``just test-agent`` (marker ``agentic``): advisory, never part of
``just check`` or CI. Hard assertions stay model-independent — a required tool
was called successfully, the ADR write path produced exactly one valid
document, no recorded secret reached the model-visible text, a server error
was delivered as an error. The checks scan the whole call history (not only
the first call of a tool), so a failed call followed by a successful retry
counts as recovery and a successful but wrong result never passes.

Every attempt records exactly one outcome in ``report.json`` through
:func:`agent.run_attempt`; answer quality and the tool-selection ratio live
next to the per-scenario transcripts and never fail the run.
"""

import json
import stat
from pathlib import Path
from typing import cast

import pytest
from agent import (
    AgentEnvironment,
    AgentReport,
    AttemptOutcome,
    Scenario,
    ScenarioCheck,
    ScenarioRun,
    ToolCallRecord,
    cli_json,
    run_attempt,
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


def _payload(call: ToolCallRecord) -> dict[str, object]:
    payload = call.result_json()
    assert payload is not None, f"{call.name} returned no JSON object: {call.result_text[:200]}"
    return payload


def _results(calls: list[ToolCallRecord]) -> list[str]:
    return [f"{call.name}(is_error={call.is_error}): {call.result_text[:160]}" for call in calls]


def _entry_refs(payload: dict[str, object]) -> set[str]:
    """Every ``entries[].ref`` of a ``lion_history`` payload."""
    raw = payload.get("entries")
    if not isinstance(raw, list):
        return set()
    refs: set[str] = set()
    for item in cast("list[object]", raw):
        if isinstance(item, dict):
            ref = cast("dict[str, object]", item).get("ref")
            if isinstance(ref, str):
                refs.add(ref)
    return refs


def _is_latest_snapshot(payload: dict[str, object]) -> bool:
    """Whether a ``lion_snapshot`` payload is the fixture's newest state B."""
    if payload.get("ref") != B_REF:
        return False
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        return False
    collectors = cast("dict[str, object]", snapshot).get("collectors")
    if not isinstance(collectors, dict):
        return False
    host = cast("dict[str, object]", collectors).get("host")
    if not isinstance(host, dict):
        return False
    return cast("dict[str, object]", host).get("hostname") == "other"


# --- scenario checks ---------------------------------------------------------


def _check_history_delivered(run: ScenarioRun) -> None:
    """A successful history or snapshot result must carry the A/B fixture states."""
    for call in run.successful_calls_of("lion_history"):
        payload = call.result_json()
        if payload is not None and {A_REF, B_REF} <= _entry_refs(payload):
            return
    for call in run.successful_calls_of("lion_snapshot"):
        payload = call.result_json()
        if payload is not None and _is_latest_snapshot(payload):
            return
    raise AssertionError(
        "no successful history or snapshot result carried the A/B fixture states; "
        f"results: {_results(run.calls_of('lion_history') + run.calls_of('lion_snapshot'))}"
    )


def _check_diff_a_to_b(run: ScenarioRun) -> None:
    """At least one successful ``lion_diff`` must resolve A to B and report a change."""
    calls = run.calls_of("lion_diff")
    assert calls, f"lion_diff was not called; called: {run.tool_names()}"
    for call in run.successful_calls_of("lion_diff"):
        payload = call.result_json()
        if payload is None:
            continue
        from_ref = _ref(payload.get("from"), "diff.from")
        to_ref = _ref(payload.get("to"), "diff.to")
        if (from_ref, to_ref) == (A_REF, B_REF) and payload.get("changed") is True:
            return
    raise AssertionError(f"no successful lion_diff resolved A -> B with changed=true; results: {_results(calls)}")


def _check_search_hit(run: ScenarioRun) -> None:
    """A successful ``lion_search`` result must contain the unique token."""
    calls = run.calls_of("lion_search")
    assert calls, f"lion_search was not called; called: {run.tool_names()}"
    for call in run.successful_calls_of("lion_search"):
        payload = call.result_json()
        if payload is None:
            continue
        hits = payload.get("hits")
        if isinstance(hits, list) and any(
            SEARCH_TOKEN in json.dumps(cast("dict[str, object]", hit), ensure_ascii=False)
            for hit in cast("list[object]", hits)
            if isinstance(hit, dict)
        ):
            return
    raise AssertionError(f"no successful lion_search result contained {SEARCH_TOKEN}; results: {_results(calls)}")


def _check_adr_write(run: ScenarioRun) -> None:
    """Exactly one valid ADR is published, it says ``stdio``, and nothing else changed."""
    published: list[tuple[str, Path]] = []
    for call in run.successful_calls_of("lion_adr_add"):
        payload = _payload(call)
        ref = payload.get("ref")
        raw_path = payload.get("path")
        assert isinstance(ref, str) and ref, f"lion_adr_add returned no reference: {payload}"
        assert isinstance(raw_path, str) and raw_path, f"lion_adr_add returned no path: {payload}"
        path = Path(raw_path)
        assert path.is_file(), f"the ADR file does not exist: {path}"
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"the ADR file mode is {oct(mode)}, expected 0o600"
        published.append((ref, path))
    assert len(published) == 1, f"expected exactly one published ADR, got {len(published)}"
    ref, path = published[0]
    shown = cli_json(run.context.server_env, "adr", "show", ref)
    fields = shown.get("fields")
    assert isinstance(fields, dict) and fields, f"lion adr show read no fields back: {shown}"
    title = cast("dict[str, object]", fields).get("title")
    decision = cast("dict[str, object]", fields).get("decision")
    assert isinstance(title, str) and title.strip(), f"the ADR title is missing: {fields}"
    assert isinstance(decision, str) and decision.strip(), f"the ADR decision is missing: {fields}"
    assert "stdio" in decision.lower(), f"the decision text does not name stdio: {decision!r}"
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


def _check_redaction_off(run: ScenarioRun) -> None:
    """With ``LION_SERVE_REDACT=0`` the raw secret must reach the transcript."""
    calls = run.calls_of("lion_recording")
    assert calls, f"lion_recording was not called: {run.tool_names()}"
    assert SECRET in transcript_text(run), "the raw secret did not become visible with redaction disabled"


def _matrix_check(expected: str) -> ScenarioCheck:
    """A report-only check: the first call must be the expected tool."""

    def check(run: ScenarioRun) -> None:
        first = run.tool_names()[:1]
        assert first == [expected], f"expected first tool {expected!r}; first: {first}"

    return check


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
    check=_check_redaction_off,
)

MATRIX: tuple[Scenario, ...] = (
    Scenario(
        name="matrix-history",
        request="How many system states are stored?",
        required_tools=("lion_history",),
        check=_matrix_check("lion_history"),
    ),
    Scenario(
        name="matrix-snapshot",
        request="Show the newest stored state in full.",
        required_tools=("lion_snapshot",),
        check=_matrix_check("lion_snapshot"),
    ),
    Scenario(
        name="matrix-diff",
        request="What changed between the two stored states?",
        required_tools=("lion_diff",),
        check=_matrix_check("lion_diff"),
    ),
    Scenario(
        name="matrix-recordings",
        request="Which shell recordings exist?",
        required_tools=("lion_recordings",),
        check=_matrix_check("lion_recordings"),
    ),
    Scenario(
        name="matrix-recording",
        request="Show the latest shell recording.",
        required_tools=("lion_recording",),
        check=_matrix_check("lion_recording"),
    ),
    Scenario(
        name="matrix-classify",
        request="Which known commands appear in the latest recording?",
        required_tools=("lion_classify",),
        check=_matrix_check("lion_classify"),
    ),
    Scenario(
        name="matrix-adr-list",
        request="Which decisions (ADRs) are recorded?",
        required_tools=("lion_adr_list",),
        check=_matrix_check("lion_adr_list"),
    ),
    Scenario(
        name="matrix-adr-show",
        request="Show the latest recorded decision (ADR).",
        required_tools=("lion_adr_show",),
        check=_matrix_check("lion_adr_show"),
    ),
    Scenario(
        name="matrix-search",
        request=f"Search everything for {SEARCH_TOKEN}.",
        required_tools=("lion_search",),
        check=_matrix_check("lion_search"),
    ),
)


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
    expected_first_tool: dict[str, str] = {}
    for scenario in MATRIX:
        expected = scenario.required_tools[0]
        record = run_attempt(scenario, agent_env, agent_report, hard=False)
        hit = list(record.tool_calls)[:1] == [expected]
        hits += int(hit)
        expected_first_tool[scenario.name] = expected
    ratio = hits / len(MATRIX)
    agent_report.set_extra(
        "tool_selection",
        {"hits": hits, "total": len(MATRIX), "ratio": ratio, "expected_first_tool": expected_first_tool},
    )
    print(f"\ntool selection: {hits}/{len(MATRIX)} first-tool hits ({ratio:.0%})")
