"""Typer command for ``lion doctor``."""

from __future__ import annotations

from typing import Annotated

import typer

from lion.cli_common import fail, layout_or_exit
from lion.output import color_enabled, display_text

from .checks import collect_diagnosis, execute_fixes, plan_fixes
from .model import DiagnosisReport
from .render import render_report

HomeOption = Annotated[
    str | None,
    typer.Option("--home", help="Home directory to operate on. Defaults to the current user's home."),
]
FixOption = Annotated[
    bool,
    typer.Option("--fix", help="Apply the scoped fixes to lion-owned artifacts."),
]
DeepOption = Annotated[
    bool,
    typer.Option("--deep", help="Run the optional Gitleaks stage when it is installed."),
]
DryRunOption = Annotated[
    bool,
    typer.Option("--dry-run", help="Show the fix plan without changing anything."),
]
NoColorOption = Annotated[
    bool,
    typer.Option("--no-color", help="Disable ANSI colors in the report."),
]


def doctor_command(
    home: HomeOption = None,
    fix: FixOption = False,
    dry_run: DryRunOption = False,
    no_color: NoColorOption = False,
    deep: DeepOption = False,
) -> None:
    """Diagnose shlib, dash, lock and secret state without printing file contents.

    The default run is read-only. ``--fix`` creates missing lion-owned
    directories and the redaction key, and removes orphaned dash locks; it
    never touches a dash link, a changed target or a suspected secret.
    ``--deep`` adds the optional Gitleaks stage; it never runs automatically.
    """
    layout = layout_or_exit(home)
    if fix and dry_run:
        fail("--fix and --dry-run are mutually exclusive")

    report = collect_diagnosis(layout, deep=deep)
    color = color_enabled(no_color)

    if not fix and not dry_run:
        _print_report(report, color)
        raise typer.Exit(report.exit_code)

    if dry_run:
        _print_report(report, color)
    plan = plan_fixes(layout)
    if plan.refused is not None:
        typer.echo(f"doctor: {plan.refused}")
        if dry_run:
            typer.echo("Dry run: no changes made.")
            raise typer.Exit(0)
        raise typer.Exit(report.exit_code)

    if plan.actions:
        typer.echo("Planned fixes:")
        for action in plan.actions:
            typer.echo(f"  {action.summary}")
    else:
        typer.echo("No fixable findings.")
    for note in plan.notes:
        typer.echo(f"note: {note}")

    if dry_run:
        typer.echo("Dry run: no changes made.")
        raise typer.Exit(0)

    outcome = execute_fixes(layout, plan)
    for error in outcome.errors:
        typer.echo(f"error: {display_text(error, layout.home)}", err=True)

    report = collect_diagnosis(layout, deep=deep)
    _print_report(report, color)
    raise typer.Exit(2 if outcome.errors else report.exit_code)


def _print_report(report: DiagnosisReport, color: bool) -> None:
    """Print a rendered diagnosis."""
    for line in render_report(report, color=color):
        typer.echo(line)
