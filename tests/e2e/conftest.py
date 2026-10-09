"""Shared guard and subprocess runner for the end-to-end suite.

Every test in this directory must run inside the disposable container started by
:file:`scripts/test-e2e.sh`. The :func:`container` fixture enforces that guard,
:func:`env` provides a child environment isolated from the developer's machine
and :func:`runner` executes commands while recording their diagnostics.
"""

import os
import subprocess
from pathlib import Path

import pytest

_TIMEOUT = 60


def require_container() -> None:
    """Skip the test unless it runs inside the disposable E2E container."""
    if os.environ.get("LION_E2E_CONTAINER") != "1" or not Path("/.dockerenv").exists():
        pytest.skip("Requires the disposable container started by just test-e2e")


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

    def __init__(self, log: Path) -> None:
        """Store the log path used for every invocation."""
        self._log = log

    def __call__(self, *args: str, env: dict[str, str], expected: int = 0, timeout: int = _TIMEOUT) -> str:
        """Run ``args`` and return stdout, failing loudly on an unexpected exit code."""
        environment = {**env, "LC_ALL": "C"}
        result = subprocess.run(
            list(args), env=environment, capture_output=True, text=True, timeout=timeout, check=False
        )
        with self._log.open("a", encoding="utf-8") as stream:
            _ = stream.write(
                f"$ {args!r}\nexit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n"
            )
        assert result.returncode == expected, (
            f"{args!r} exited {result.returncode}, expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        return result.stdout


@pytest.fixture
def runner(tmp_path: Path) -> Runner:
    """Return a :class:`Runner` that logs every command below ``tmp_path``."""
    return Runner(tmp_path / "commands.log")
