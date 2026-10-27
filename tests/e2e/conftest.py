"""Pytest configuration for the opt-in Docker E2E suite."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_skipped = False


def _gate_error() -> str | None:
    """Return the first missing container precondition, if any."""
    if os.environ.get("LUPYSTA_E2E") != "1":
        return "tests/e2e collected without the opt-in gate; use just test-e2e"
    if os.environ.get("LUPYSTA_E2E_CONTAINER") != "1":
        return "tests/e2e must run inside the E2E container; use just test-e2e"
    if not Path("/.dockerenv").exists():
        return "tests/e2e must run inside Docker; use just test-e2e"
    return None


def pytest_collectstart(collector: pytest.Collector) -> None:
    """Abort before pytest imports any E2E test module."""
    message = _gate_error()
    if message is not None:
        pytest.exit(message, returncode=pytest.ExitCode.TESTS_FAILED)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Remember whether any module was skipped during collection."""
    global _skipped
    if report.skipped:
        _skipped = True


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Remember whether any E2E test was skipped."""
    global _skipped
    if report.skipped:
        _skipped = True


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Turn skips into failures inside the E2E container."""
    global _skipped
    if exitstatus == 0 and _skipped and os.environ.get("LUPYSTA_E2E_CONTAINER") == "1":
        print(
            "Skipped tests are failures in the opt-in E2E suite.",
            file=sys.stderr,
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    _skipped = False
