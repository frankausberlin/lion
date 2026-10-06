"""Shared helper for running external tools without aborting a capture."""

import os
import subprocess


def run_tool(command: list[str], *, timeout: int, failures: list[str] | None = None) -> str | None:
    """Run an external tool and return stdout, or ``None`` when it is unavailable.

    A missing binary, a timeout, a non-zero exit, or any other subprocess
    failure all count as "unavailable" so a collector can degrade gracefully.

    Args:
        command: The command and its arguments.
        timeout: Maximum runtime in seconds.
        failures: Optional destination for stable diagnostics, excluding tool output.

    Returns:
        The captured stdout, or ``None`` on any failure.
    """

    def failed(reason: str) -> None:
        if failures is not None:
            failures.append(f"{command[0]}: {reason}")

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except FileNotFoundError:
        failed("executable not found")
        return None
    except subprocess.TimeoutExpired:
        failed("timed out")
        return None
    except (OSError, subprocess.SubprocessError, UnicodeError):
        failed("execution or decoding failed")
        return None
    if completed.returncode != 0:
        failed(f"exit code {completed.returncode}")
        return None
    return completed.stdout
