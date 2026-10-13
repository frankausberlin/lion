"""Session fixtures for the opt-in agentic QA suite.

The Ollama guard is a session fixture that fails loudly (never skips): the
suite is explicitly opt-in, so a missing service or model is a setup error with
an action hint, not a reason to pass silently. Every test runs against a
disposable ``tmp_path`` home with an isolated ``XDG_DATA_HOME``/
``XDG_CONFIG_HOME``; the ledger builders live in :mod:`ledger`. Transcripts and
``report.json`` are collected in a session-scoped ``agentic-artifacts.*``
directory in the repository root (gitignored, removed by ``just clean``).
"""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import ledger
import pytest
from agent import AgentEnvironment, AgentReport, OllamaModel, OllamaSettings, check_ollama

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def ollama_settings() -> OllamaSettings:
    """The resolved Ollama endpoint, model and sampling options."""
    return OllamaSettings.from_env()


@pytest.fixture(scope="session")
def ollama_model(ollama_settings: OllamaSettings) -> OllamaModel:
    """Verify the local Ollama model once; a setup problem fails loudly."""
    return check_ollama(ollama_settings)


@pytest.fixture(scope="session")
def agent_artifacts() -> Path:
    """The session artifact directory; kept for inspection after the run."""
    return Path(tempfile.mkdtemp(prefix="agentic-artifacts.", dir=REPO_ROOT))


@pytest.fixture(scope="session")
def agent_report(
    agent_artifacts: Path,
    ollama_settings: OllamaSettings,
    ollama_model: OllamaModel,
) -> Iterator[AgentReport]:
    """Collect scenario outcomes and write ``report.json`` at session end."""
    report = AgentReport(agent_artifacts / "report.json", ollama_settings, ollama_model)
    yield report
    report.write()


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
    ollama_settings: OllamaSettings,
    agent_artifacts: Path,
) -> AgentEnvironment:
    """One isolated ledger plus the live agent runner bound to it."""
    home = tmp_path / "home"
    data = tmp_path / "data"
    return AgentEnvironment(
        data_home=data,
        server_env={"HOME": str(home), "XDG_DATA_HOME": str(data)},
        settings=ollama_settings,
        artifacts=agent_artifacts,
    )


@pytest.fixture
def two_states(agent_env: AgentEnvironment) -> None:
    """Seed the fixed A/B states (A older, B latest)."""
    ledger.two_states()


@pytest.fixture
def search_adr(agent_env: AgentEnvironment) -> str:
    """Seed one standalone ADR carrying the unique search token."""
    return ledger.search_adr()


@pytest.fixture
def secret_recording(agent_env: AgentEnvironment) -> str:
    """Seed one stopped recording with a synthetic secret."""
    return ledger.secret_recording()
