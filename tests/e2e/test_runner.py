"""Verify that a real subprocess result keeps usable E2E diagnostics."""

import subprocess
import sys
from pathlib import Path

import pytest
from e2eutil import Runner

pytestmark = pytest.mark.e2e


def test_timeout_retains_partial_output(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """A killed child leaves its command and both partial streams in the log."""
    code = (
        "import sys,time; print('started',flush=True); print('diagnostic',file=sys.stderr,flush=True); time.sleep(10)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        runner(sys.executable, "-c", code, env=env, timeout=1)
    log = (tmp_path / "commands.log").read_text(encoding="utf-8")
    assert "$ [" in log
    assert "timeout=1" in log
    assert "stdout:\nstarted\n" in log
    assert "stderr:\ndiagnostic\n" in log


def test_nonzero_exit_returns_separate_streams(runner: Runner, env: dict[str, str]) -> None:
    """An expected non-zero exit returns both streams separately without raising."""
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    result = runner.invoke(sys.executable, "-c", code, env=env, expected=3)
    assert result.returncode == 3
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"


def test_invoke_honours_cwd(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """Invoke runs the child in the requested working directory."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    result = runner.invoke(sys.executable, "-c", "import os; print(os.getcwd())", env=env, expected=0, cwd=str(workdir))
    assert result.stdout.strip() == str(workdir)
