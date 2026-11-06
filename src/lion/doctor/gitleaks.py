"""Optional Gitleaks stage for ``lion doctor --deep``.

Gitleaks is never a security boundary and never runs automatically. The stage
is best effort: a missing binary, an unsupported version and every scanner
error become findings, while the built-in detection stages keep running.

Only rule id and positions are used. Gitleaks' ``Secret`` and ``Match`` fields
are never read into a finding, and stderr is never copied into a message.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from lion.redaction import SecretFinding

MIN_VERSION = (8, 0, 0)
VERSION_TIMEOUT_SECONDS = 5
SCAN_TIMEOUT_SECONDS = 10
MAX_TARGET_MEGABYTES = "1"

_CONFIG_ENVIRONMENT = frozenset({"GITLEAKS_CONFIG", "GITLEAKS_CONFIG_TOML"})
_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_GENERIC_RULE = "generic-api-key"


@dataclass(frozen=True, slots=True)
class GitleaksProblem:
    """One scanner problem without stderr, secret or match content.

    Attributes:
        code: Stable doctor finding code.
        message: Value-free description.
    """

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class GitleaksSetup:
    """Result of locating and version-checking the optional binary.

    Attributes:
        executable: Resolved binary path, or ``None`` when unavailable.
        problem: Why the deep stage cannot run, or ``None`` when it can.
    """

    executable: str | None
    problem: GitleaksProblem | None = None


@dataclass(frozen=True, slots=True)
class GitleaksScan:
    """Findings and problems for one file.

    Attributes:
        findings: Position-only findings.
        problems: Non-fatal scanner problems for this file.
    """

    findings: tuple[SecretFinding, ...] = ()
    problems: tuple[GitleaksProblem, ...] = ()


def detect() -> GitleaksSetup:
    """Locate Gitleaks and verify a supported version.

    Returns:
        The executable for a supported version, otherwise a setup problem.
    """
    executable = shutil.which("gitleaks")
    if executable is None:
        return GitleaksSetup(
            None,
            GitleaksProblem("gitleaks.unavailable", "gitleaks is not installed; deep scan skipped"),
        )
    try:
        result = subprocess.run(
            [executable, "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERSION_TIMEOUT_SECONDS,
            env=_environment(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return GitleaksSetup(
            None,
            GitleaksProblem("gitleaks.unsupported", "cannot determine the gitleaks version; deep scan skipped"),
        )
    version = _parse_version(result.stdout + result.stderr if result.returncode == 0 else "")
    if version is None or version < MIN_VERSION:
        return GitleaksSetup(
            None,
            GitleaksProblem("gitleaks.unsupported", "gitleaks 8.0.0 or newer is required; deep scan skipped"),
        )
    return GitleaksSetup(executable)


def scan_file(path: Path, text: str, *, executable: str) -> GitleaksScan:
    """Run one Gitleaks scan and map positions into ``text`` offsets.

    Args:
        path: Original entry passed to Gitleaks.
        text: Already decoded content of the entry.
        executable: Binary returned by :func:`detect`.

    Returns:
        Position-only findings and non-fatal problems.
    """
    command = [
        executable,
        "dir",
        str(path),
        "--no-banner",
        "--redact",
        "--report-format",
        "json",
        "--report-path",
        "-",
        "--exit-code",
        "0",
        "--log-level",
        "error",
        "--max-target-megabytes",
        MAX_TARGET_MEGABYTES,
    ]
    if path.is_symlink():
        command.append("--follow-symlinks")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SCAN_TIMEOUT_SECONDS,
            env=_environment(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GitleaksScan(problems=(GitleaksProblem("gitleaks.error", "gitleaks timed out"),))
    except OSError:
        return GitleaksScan(problems=(GitleaksProblem("gitleaks.error", "cannot run gitleaks"),))
    if result.returncode != 0:
        return GitleaksScan(
            problems=(GitleaksProblem("gitleaks.error", f"gitleaks exited with status {result.returncode}"),)
        )
    payload = result.stdout.strip()
    if not payload:
        return GitleaksScan()
    try:
        report: object = json.loads(payload)
    except json.JSONDecodeError:
        return GitleaksScan(problems=(GitleaksProblem("gitleaks.error", "gitleaks returned invalid JSON"),))
    if report is None:
        return GitleaksScan()
    if not isinstance(report, list):
        return GitleaksScan(problems=(GitleaksProblem("gitleaks.error", "gitleaks returned an unexpected report"),))

    findings: list[SecretFinding] = []
    problems: list[GitleaksProblem] = []
    for item in cast("list[object]", report):
        finding = _finding_for_item(item, text)
        if finding is None:
            problems.append(GitleaksProblem("gitleaks.error", "gitleaks returned an unusable result"))
            continue
        findings.append(finding)
    return GitleaksScan(findings=tuple(findings), problems=tuple(problems))


def _finding_for_item(item: object, text: str) -> SecretFinding | None:
    """Return one finding for a Gitleaks JSON entry, or ``None``."""
    if not isinstance(item, dict):
        return None
    entry = cast("dict[str, object]", item)
    rule_id = entry.get("RuleID")
    start_line = entry.get("StartLine")
    start_column = entry.get("StartColumn")
    end_line = entry.get("EndLine")
    end_column = entry.get("EndColumn")
    if not isinstance(rule_id, str) or not rule_id:
        return None
    if not _is_plain_int(start_line) or not _is_plain_int(start_column):
        return None
    if not _is_plain_int(end_line) or not _is_plain_int(end_column):
        return None
    start_line = cast("int", start_line)
    start_column = cast("int", start_column)
    end_line = cast("int", end_line)
    end_column = cast("int", end_column)
    start = _offset(text, start_line, start_column, inclusive_end=False)
    end = _offset(text, end_line, end_column, inclusive_end=True)
    if start is None or end is None or end <= start or end > len(text):
        return None
    kind = _sanitize_rule(rule_id)
    confidence = 85 if kind == _GENERIC_RULE else 95
    return SecretFinding(
        kind=kind,
        rule=f"gitleaks.{kind}",
        start=start,
        end=end,
        confidence=confidence,
    )


def _is_plain_int(value: object) -> bool:
    """Return whether ``value`` is an integer but not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _offset(text: str, line: int, column: int, *, inclusive_end: bool) -> int | None:
    """Convert a one-based line and column to a text offset."""
    if line < 1 or column < 1:
        return None
    line_start = 0
    for _ in range(line - 1):
        newline = text.find("\n", line_start)
        if newline == -1:
            return None
        line_start = newline + 1
    offset = line_start + column - 1
    if inclusive_end:
        offset += 1
    if offset > len(text):
        return None
    return offset


def _sanitize_rule(rule_id: str) -> str:
    """Return a stable, value-free kind for a Gitleaks rule id."""
    return re.sub(r"[^a-z0-9]+", "-", rule_id.lower()).strip("-") or "secret"


def _parse_version(output: str) -> tuple[int, int, int] | None:
    """Return the first semantic version in ``output``."""
    match = _VERSION_PATTERN.search(output)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _environment() -> dict[str, str]:
    """Return the environment without Gitleaks configuration overrides."""
    return {key: value for key, value in os.environ.items() if key not in _CONFIG_ENVIRONMENT}
