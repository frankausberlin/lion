"""Scenario definitions and hard checks of the agentic QA suite.

The scenarios are data: a request (or an MCP prompt), the expected tools and a
check over the whole call history. The checks scan every successful call of the
expected tool, so a failed call followed by a successful retry counts as
recovery and a successful but wrong result never passes. This module holds no
pytest fixtures or markers, so the deterministic harness regression tests in
``tests/test_agentic_harness.py`` can reuse the very same scenarios and checks
without Ollama.

:func:`suite_identity` pins the comparability of two ``report.json`` runs: its
hash covers every scenario definition and the fixture seed constants.
"""

import hashlib
import json
import stat
from pathlib import Path
from typing import cast

from tests.agentic.agent import Scenario, ScenarioCheck, ScenarioRun, ToolCallRecord, cli_json, transcript_text
from tests.agentic.ledger import ADR_FIELDS, RECORDING_EVENTS, SEARCH_TOKEN, SECRET
from tests.ledger_fixtures import A_REF, B_REF

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

    check.__name__ = f"first_tool_{expected}"
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

SCENARIOS: tuple[Scenario, ...] = (
    SCENARIO_HISTORY,
    SCENARIO_DIFF,
    SCENARIO_REVIEW,
    SCENARIO_SEARCH,
    SCENARIO_ADR_WRITE,
    SCENARIO_MISSING_RECORDING,
    SCENARIO_REDACTION,
    SCENARIO_REDACTION_OFF,
)

ALL_SCENARIOS: tuple[Scenario, ...] = (*SCENARIOS, *MATRIX)


# --- suite identity ----------------------------------------------------------


def _scenario_identity(scenario: Scenario) -> dict[str, object]:
    check = scenario.check
    return {
        "name": scenario.name,
        "request": scenario.request,
        "required_tools": list(scenario.required_tools),
        "required_any": list(scenario.required_any),
        "prompt_name": scenario.prompt_name,
        "prompt_arguments": dict(scenario.prompt_arguments),
        "check": "" if check is None else check.__name__,
    }


def suite_identity() -> dict[str, object]:
    """The stable identity of this scenario suite for cross-run comparisons.

    The SHA-256 covers every scenario definition (request, expected tools, MCP
    prompt and check function) and the fixture seed values owned by this suite
    (search token, secret, ADR fields, recording events) plus the shared A/B
    state references. Two reports with the same hash ran the same scenarios
    over the same fixtures; changing any definition changes the hash. The
    composition is pinned by a harness regression test.
    """
    payload = {
        "scenarios": [_scenario_identity(scenario) for scenario in ALL_SCENARIOS],
        "seeds": {
            "search_token": SEARCH_TOKEN,
            "secret": SECRET,
            "adr_fields": ADR_FIELDS,
            "recording_events": [list(event) for event in RECORDING_EVENTS],
            "a_ref": A_REF,
            "b_ref": B_REF,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "scenarios": [scenario.name for scenario in ALL_SCENARIOS],
    }
