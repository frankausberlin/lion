"""Shared guard and subprocess runner for the end-to-end suite.

Every test in this directory must run inside the disposable container started by
:file:`scripts/test-e2e.sh`. The :func:`container` fixture enforces that guard,
:func:`env` provides a child environment isolated from the developer's machine
and :func:`runner` executes commands while recording their diagnostics.
"""

import os
import pwd
import subprocess
from pathlib import Path

import pytest

_TIMEOUT = 60


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


class Runner:
    """Run subprocesses, recording command, exit code, stdout and stderr."""

    def __init__(self, log: Path, prefix: tuple[str, ...] = ()) -> None:
        """Store the log path used for every invocation."""
        self._log = log
        self._prefix = prefix

    def __call__(self, *args: str, env: dict[str, str], expected: int = 0, timeout: int = _TIMEOUT) -> str:
        """Run ``args`` and return stdout, failing loudly on an unexpected exit code."""
        environment = {**env, "LC_ALL": "C"}
        command = [*self._prefix, *args]
        with self._log.open("a", encoding="utf-8") as stream:
            _ = stream.write(f"$ {command!r}\n")
            stream.flush()
            try:
                result = subprocess.run(
                    command, env=environment, capture_output=True, text=True, timeout=timeout, check=False
                )
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
                stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
                _ = stream.write(f"timeout={timeout}\nstdout:\n{stdout}\nstderr:\n{stderr}\n")
                raise
            _ = stream.write(f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n")
        assert result.returncode == expected, (
            f"{args!r} exited {result.returncode}, expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        return result.stdout


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
