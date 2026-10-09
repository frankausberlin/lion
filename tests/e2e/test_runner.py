"""Verify that a real subprocess timeout leaves usable E2E diagnostics."""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_timeout_retains_partial_output(runner: Callable[..., str], env: dict[str, str], tmp_path: Path) -> None:
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
