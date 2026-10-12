"""Shared guard and subprocess runner for the end-to-end suite.

Every test in this directory must run inside the disposable container started by
:file:`scripts/test-e2e.sh`. The :func:`container` fixture enforces that guard,
:func:`env` provides a child environment isolated from the developer's machine
and :func:`runner` executes commands while recording their diagnostics.
"""

import os
import pwd
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from e2eutil import Runner
from watchutil import TMUX_CONFIG


def require_container() -> None:
    """Skip the test unless it runs inside the disposable E2E container."""
    if os.environ.get("LION_E2E_CONTAINER") != "1":
        pytest.skip("Requires the disposable container started by just test-e2e")
    if not Path("/.dockerenv").exists():
        pytest.fail("E2E opt-in requires the disposable Docker container")


@pytest.fixture(autouse=True)
def container() -> None:
    """Guard every test in this suite: only the disposable container may run it."""
    require_container()


@pytest.fixture(scope="session")
def tmux_server() -> Iterator[None]:
    """Start a tmux server for the interactive scenarios and tear it down.

    The server is started with the committed configuration and serves every
    ``Zsh`` (one session each). ``exit-empty off`` keeps it alive between
    sessions, so the configuration and the default socket persist for the run;
    the driver stops the server again when the last session closes, which is also
    when tmux reaps the exited shell. This fixture guarantees a clean start and
    that no server is ever left behind.
    """
    _ = subprocess.run(["tmux", "kill-server"], capture_output=True, check=False)
    _ = subprocess.run(["tmux", "-f", str(TMUX_CONFIG), "start-server"], capture_output=True, check=True)
    try:
        yield
    finally:
        _ = subprocess.run(["tmux", "kill-server"], capture_output=True, check=False)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """Return an isolated child environment pointing at temporary directories."""
    (tmp_path / "home").mkdir()
    environment = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "LC_ALL": "C",
        "LANG": "C",
    }
    environment.pop("ZDOTDIR", None)
    return environment


@pytest.fixture
def runner(tmp_path: Path) -> Runner:
    """Return a :class:`Runner` that logs every command below ``tmp_path``."""
    return Runner(tmp_path / "commands.log")


@pytest.fixture
def user_runner(tmp_path: Path, env: dict[str, str]) -> Runner:
    """Run Lion as an unprivileged container user with owned temporary paths."""
    if os.geteuid() != 0:
        pytest.fail("The E2E harness must start as root to switch users")
    if not tmp_path.is_relative_to("/artifacts/work"):
        pytest.fail("Non-root scenarios require the runner basetemp under /artifacts/work")
    account = pwd.getpwnam("lion-e2e")
    # Pytest's basetemp ancestors are private by default; permit traversal only.
    for ancestor in tmp_path.parents:
        if ancestor == Path("/artifacts"):
            break
        ancestor.chmod(0o755)
    os.chown(tmp_path, account.pw_uid, account.pw_gid)
    os.chown(env["HOME"], account.pw_uid, account.pw_gid)
    return Runner(tmp_path / "user-commands.log", ("runuser", "--preserve-environment", "-u", "lion-e2e", "--"))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """An opted-in E2E run must never report success with skipped scenarios."""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if os.environ.get("LION_E2E_CONTAINER") == "1" and reporter is not None and reporter.stats.get("skipped"):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
