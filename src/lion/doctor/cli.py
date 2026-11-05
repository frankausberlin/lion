"""Typer command for ``lion doctor``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from lion.output import display_text
from lion.shlib.errors import ShlibError
from lion.shlib.layout import ShlibLayout

from .checks import collect_diagnosis, execute_fixes, plan_fixes
from .model import DiagnosisReport
from .render import color_enabled, render_report

HomeOption = Annotated[
    str | None,
    typer.Option("--home", help="Home directory to operate on. Defaults to the current user's home."),
]
FixOption = Annotated[
    bool,
    typer.Option("--fix", help="Apply the scoped fixes to lion-owned artifacts."),
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
) -> None:
    """Diagnose shlib, dash, lock and secret state without printing file contents.

    The default run is read-only. ``--fix`` creates missing lion-owned
    directories and the redaction key, and removes orphaned dash locks; it
    never touches a dash link, a changed target or a suspected secret.
    """
    try:
        layout = ShlibLayout.from_option(home)
    except ShlibError as exc:
        _fail(exc)
    if fix and dry_run:
        _fail_message("--fix and --dry-run are mutually exclusive")

    report = collect_diagnosis(layout)
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

    report = collect_diagnosis(layout)
    _print_report(report, color)
    raise typer.Exit(2 if outcome.errors else report.exit_code)


def _print_report(report: DiagnosisReport, color: bool) -> None:
    """Print a rendered diagnosis."""
    for line in render_report(report, color=color):
        typer.echo(line)


def _fail(error: ShlibError) -> NoReturn:
    """Print a user-facing error and exit with status 2."""
    _fail_message(str(error))


def _fail_message(message: str, home: Path | None = None) -> NoReturn:
    """Print a plain error message and exit with status 2."""
    if home is not None:
        message = display_text(message, home)
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(2)
