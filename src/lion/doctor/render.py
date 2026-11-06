"""Human-readable rendering for ``lion doctor``."""

from __future__ import annotations

import os
import sys

from .model import ERROR, INFO, OK, WARNING, DiagnosisReport, Finding

_SECTION_CODES = {
    "dash": "dash",
    "locks": "dash",
    "redaction": "security",
    "prefixes": "security",
    "exports.possible_secret": "security",
    "shlibs.possible_secret": "security",
    "gitleaks": "security",
    "scan": "security",
}
_SECTIONS = ("shlib", "dash", "security")
_EMPTY_SECTION_TEXT = {"security": "no suspected secrets found (best effort)"}
_COLORS = {
    ERROR: "\x1b[31m",
    WARNING: "\x1b[33m",
    INFO: "\x1b[36m",
    OK: "\x1b[32m",
}
_RESET = "\x1b[0m"


def color_enabled(no_color: bool) -> bool:
    """Return whether ANSI colors should be used.

    Args:
        no_color: Value of the ``--no-color`` option.

    Returns:
        ``True`` only for a TTY without ``NO_COLOR`` and without ``--no-color``.
    """
    return not no_color and "NO_COLOR" not in os.environ and sys.stdout.isatty()


def render_report(report: DiagnosisReport, *, color: bool) -> list[str]:
    """Render a diagnosis as plain lines with optional ANSI colors.

    Args:
        report: Diagnosis to render.
        color: Whether to add ANSI colors.

    Returns:
        Lines in section order followed by the summary line.
    """
    lines: list[str] = []
    for section in _SECTIONS:
        findings = [finding for finding in report.findings if _section(finding) == section]
        lines.append(_style(f"{section}:", OK, color))
        if not findings:
            empty = _EMPTY_SECTION_TEXT.get(section, "ok")
            lines.append(f"  {_style(empty, OK, color)}")
            continue
        for finding in findings:
            lines.extend(_render_finding(finding, color))
    lines.append(f"doctor: {len(report.fixable)} fixable, {len(report.manual)} manual")
    return lines


def _render_finding(finding: Finding, color: bool) -> list[str]:
    """Render one finding with its state, optional detail and hint."""
    state = "fixable" if finding.fixable else "info" if finding.severity == INFO else "manual"
    label = _style(finding.severity, finding.severity, color)
    lines = [f"  {label} {finding.code}: {finding.summary} [{state}]"]
    if finding.detail:
        lines.append(f"    detail: {finding.detail}")
    if finding.hint:
        lines.append(f"    hint: {finding.hint}")
    return lines


def _section(finding: Finding) -> str:
    """Return the output section for a finding."""
    prefix = finding.code.split(".", 1)[0]
    return _SECTION_CODES.get(prefix, _SECTION_CODES.get(finding.code, "shlib"))


def _style(text: str, severity: str, enabled: bool) -> str:
    """Wrap ``text`` in the severity color when enabled."""
    if not enabled:
        return text
    return f"{_COLORS.get(severity, '')}{text}{_RESET}"
