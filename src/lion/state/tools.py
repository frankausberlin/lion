"""Shared helper for running external tools without aborting a capture."""

import os
import subprocess


def run_tool(command: list[str], *, timeout: int) -> str | None:
    """Run an external tool and return stdout, or ``None`` when it is unavailable.

    A missing binary, a timeout, a non-zero exit, or any other subprocess
    failure all count as "unavailable" so a collector can degrade gracefully.

    Args:
        command: The command and its arguments.
        timeout: Maximum runtime in seconds.

    Returns:
        The captured stdout, or ``None`` on any failure.
    """
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout
