"""Pytest configuration for the opt-in agent suite.

This skeleton only guards the suite. The provider preflight (endpoint, model,
credentials, limits) belongs in the project-specific harness, not here.
"""

from __future__ import annotations

import os
import sys

import pytest

_skipped = False


def _gate_error() -> str | None:
    """Return the opt-in gate error, if the suite lacks its opt-in."""
    if os.environ.get("LUPYSTA_AGENT") != "1":
        return "tests/agent collected without the opt-in gate; use just test-agent"
    return None


def pytest_collectstart(collector: pytest.Collector) -> None:
    """Abort before pytest imports any agent test module."""
    message = _gate_error()
    if message is not None:
        pytest.exit(message, returncode=pytest.ExitCode.TESTS_FAILED)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Remember whether any module was skipped during collection."""
    global _skipped
    if report.skipped:
        _skipped = True


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Remember whether any agent test was skipped."""
    global _skipped
    if report.skipped:
        _skipped = True


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Turn skips into failures for an opted-in agent run."""
    global _skipped
    if exitstatus == 0 and _skipped and os.environ.get("LUPYSTA_AGENT") == "1":
        print(
            "Skipped tests are failures in the opt-in agent suite.",
            file=sys.stderr,
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    _skipped = False
