"""Data model for ``lion doctor``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lion.shlib.layout import ShlibLayout
from lion.shlib.status import StatusReport

OK = "ok"
INFO = "info"
WARNING = "warning"
ERROR = "error"

SEVERITIES = (OK, INFO, WARNING, ERROR)


@dataclass(frozen=True, slots=True)
class Finding:
    """One diagnosis finding without any secret value.

    Attributes:
        code: Stable finding id such as ``dash.broken_link``.
        severity: ``ok``, ``info``, ``warning`` or ``error``.
        summary: Human-readable text without file contents.
        path: Affected path, if any.
        line: One-based line number, if the finding belongs to a line.
        fixable: Whether ``lion doctor --fix`` repairs this finding.
        detail: Additional non-secret context.
        hint: Manual command or next step; never executed.
    """

    code: str
    severity: str
    summary: str
    path: Path | None = None
    line: int | None = None
    fixable: bool = False
    detail: str | None = None
    hint: str | None = None


@dataclass(frozen=True, slots=True)
class DiagnosisReport:
    """Complete read-only diagnosis.

    Attributes:
        layout: Paths below the selected home directory.
        status: The shlib status snapshot used by the checks.
        findings: Collected findings in collection order.
        exit_code: ``0`` ok, ``1`` warnings, ``2`` errors.
    """

    layout: ShlibLayout
    status: StatusReport
    findings: tuple[Finding, ...]
    exit_code: int

    @property
    def fixable(self) -> tuple[Finding, ...]:
        """Return findings that ``--fix`` can repair."""
        return tuple(finding for finding in self.findings if finding.fixable)

    @property
    def manual(self) -> tuple[Finding, ...]:
        """Return warning and error findings that need a manual decision."""
        return tuple(
            finding for finding in self.findings if not finding.fixable and finding.severity in {WARNING, ERROR}
        )


@dataclass(frozen=True, slots=True)
class FixAction:
    """One planned filesystem action.

    Attributes:
        kind: ``create_dir``, ``create_key`` or ``remove_lock``.
        path: Affected path.
        name: Dash entry name for ``remove_lock``.
        summary: Human-readable description without secret values.
    """

    kind: str
    path: Path
    name: str | None
    summary: str


@dataclass(frozen=True, slots=True)
class FixPlan:
    """Planned fixes and the reason they were refused.

    Attributes:
        actions: Ordered actions to apply.
        notes: Non-action notes that affect a later run.
        refused: Reason all fixes are refused, or ``None``.
    """

    actions: tuple[FixAction, ...]
    notes: tuple[str, ...] = ()
    refused: str | None = None


@dataclass(frozen=True, slots=True)
class FixOutcome:
    """Result of applying a fix plan.

    Attributes:
        errors: One non-secret error message per failed action.
    """

    errors: tuple[str, ...] = ()
