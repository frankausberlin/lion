"""Session fixtures for the opt-in agentic QA suite.

The provider preflight runs inside the report setup: the report exists before
it can fail, so a missing credential, an unreachable service or a missing
model results in a ``report.json`` with ``state = "preflight_error"`` and a
loud ``AgentError`` with an action hint — never a skip, because the suite is
explicitly opt-in. After the run the report is finalized and evaluated once
(:func:`tests.agentic.agent.evaluate_report`), which writes
``agentic-report-<run-id>.md`` next to it. Every test runs
against a disposable ``tmp_path`` home with an isolated ``XDG_DATA_HOME``/
``XDG_CONFIG_HOME``; the ledger builders live in :mod:`tests.agentic.ledger`.
Transcripts and ``report.json`` are collected in a session-scoped
``agentic-artifacts.*`` directory in the repository root (gitignored, removed
by ``just clean``).
"""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.agentic import ledger, suite
from tests.agentic.agent import (
    AgentEnvironment,
    AgentError,
    AgenticConfig,
    AgentReport,
    AgentSettings,
    SystemPrompt,
    check_provider,
    collect_environment,
    evaluate_report,
    load_agentic_config,
    load_system_prompt,
)
from tests.ledger_fixtures import two_states as seed_two_states

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def agent_config() -> AgenticConfig:
    """The resolved agentic config (``tests/agentic/agentic.toml`` plus env overrides)."""
    return load_agentic_config()


@pytest.fixture(scope="session")
def agent_settings(agent_config: AgenticConfig) -> AgentSettings:
    """The resolved provider, endpoint, model, credential and sampling options."""
    return AgentSettings.from_config(agent_config)


@pytest.fixture(scope="session")
def agent_system_prompt() -> SystemPrompt:
    """The resolved system prompt (builtin or the ``LION_AGENT_SYSTEM_PROMPT_FILE`` variant)."""
    return load_system_prompt()


@pytest.fixture(scope="session")
def agent_artifacts() -> Path:
    """The session artifact directory; kept for inspection after the run."""
    return Path(tempfile.mkdtemp(prefix="agentic-artifacts.", dir=REPO_ROOT))


@pytest.fixture(scope="session")
def agent_report(
    agent_artifacts: Path,
    agent_config: AgenticConfig,
    agent_settings: AgentSettings,
    agent_system_prompt: SystemPrompt,
) -> Iterator[AgentReport]:
    """Verify the provider, pin the run metadata, collect outcomes, finalize, then evaluate."""
    report = AgentReport(agent_artifacts / "report.json", agent_settings)
    report.set_system_prompt(agent_system_prompt)
    report.set_suite(suite.suite_identity())
    try:
        model = check_provider(agent_settings)
    except AgentError as exc:
        report.mark_preflight_error(str(exc))
        evaluate_report(report, agent_settings, agent_config, agent_artifacts)
        raise
    report.set_model(model)
    report.set_environment(collect_environment(agent_settings))
    yield report
    report.finalize()
    evaluate_report(report, agent_settings, agent_config, agent_artifacts)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disposable home, data and config directories; no ZDOTDIR, no redaction switch."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.delenv("LION_SERVE_REDACT", raising=False)
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)


@pytest.fixture
def agent_env(
    tmp_path: Path,
    isolated_home: None,
    agent_settings: AgentSettings,
    agent_system_prompt: SystemPrompt,
    agent_artifacts: Path,
) -> AgentEnvironment:
    """One isolated ledger plus the live agent runner bound to it."""
    home = tmp_path / "home"
    data = tmp_path / "data"
    return AgentEnvironment(
        data_home=data,
        server_env={"HOME": str(home), "XDG_DATA_HOME": str(data)},
        settings=agent_settings,
        artifacts=agent_artifacts,
        system_prompt=agent_system_prompt,
    )


@pytest.fixture
def two_states(agent_env: AgentEnvironment) -> None:
    """Seed the fixed A/B states (A older, B latest)."""
    seed_two_states()


@pytest.fixture
def search_adr(agent_env: AgentEnvironment) -> str:
    """Seed one standalone ADR carrying the unique search token."""
    return ledger.search_adr()


@pytest.fixture
def secret_recording(agent_env: AgentEnvironment) -> str:
    """Seed one stopped recording with a synthetic secret."""
    return ledger.secret_recording()
