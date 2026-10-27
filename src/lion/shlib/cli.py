"""Typer sub-app for ``lion shlib``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from lion.shlib.errors import ShlibError
from lion.shlib.install import InstallPlan, InstallReport, apply_install, plan_install
from lion.shlib.layout import ShlibLayout
from lion.shlib.uninstall import (
    UninstallPlan,
    UninstallReport,
    apply_uninstall,
    plan_uninstall,
)

shlib_app = typer.Typer(
    name="shlib",
    help="Install or remove the zsh shlib layout.",
    no_args_is_help=True,
)

HomeOption = Annotated[
    str | None,
    typer.Option("--home", help="Home directory to operate on. Defaults to the current user's home."),
]
DryRunOption = Annotated[
    bool,
    typer.Option("--dry-run", help="Run all checks and show the plan without changing anything."),
]
ForceOption = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Overwrite a .zshrc that differs from the lock file or has no lock file.",
    ),
]


@shlib_app.command("install")
def install_command(home: HomeOption = None, dry_run: DryRunOption = False) -> None:
    """Install the shlib layout and replace ~/.zshrc with the bootstrap block."""
    try:
        layout = ShlibLayout.from_option(home)
    except ShlibError as exc:
        _fail(exc)
    try:
        plan = plan_install(layout)
    except ShlibError as exc:
        _fail(exc, layout.home)

    if dry_run:
        _print_install_plan(plan)
        typer.echo("Dry run: no changes made.")
        return

    try:
        report = apply_install(plan)
    except ShlibError as exc:
        _fail(exc, layout.home)
    _print_install_report(report, layout)
    typer.echo("Start a new shell or run: source ~/.zshrc")


@shlib_app.command("uninstall")
def uninstall_command(
    home: HomeOption = None,
    dry_run: DryRunOption = False,
    force: ForceOption = False,
) -> None:
    """Flatten the shlib layout into ~/.zshrc and keep the old tree as a backup."""
    try:
        layout = ShlibLayout.from_option(home)
    except ShlibError as exc:
        _fail(exc)
    try:
        plan = plan_uninstall(layout, force=force)
    except ShlibError as exc:
        _fail(exc, layout.home)

    for warning in plan.warnings:
        typer.echo(f"warning: {_display_text(warning, layout.home)}")

    if dry_run:
        _print_uninstall_plan(plan)
        typer.echo("Dry run: no changes made.")
        return

    try:
        report = apply_uninstall(plan)
    except ShlibError as exc:
        _fail(exc, layout.home)
    _print_uninstall_report(report, layout)
    typer.echo("The former configuration was flattened, not restored.")
    typer.echo("Start a new shell or run: source ~/.zshrc")


def _fail(error: ShlibError, home: Path | None = None) -> NoReturn:
    """Print a user-facing error and exit with status 1."""
    message = str(error)
    if home is not None:
        message = _display_text(message, home)
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def _display(path: Path, home: Path) -> str:
    """Return a path abbreviated with ``~`` when it is below ``home``."""
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    if str(relative) == ".":
        return "~"
    return f"~/{relative}"


def _display_text(text: str, home: Path) -> str:
    """Abbreviate occurrences of ``home`` inside a message."""
    return text.replace(str(home), "~")


def _print_install_plan(plan: InstallPlan) -> None:
    """Print what an installation would do."""
    home = plan.layout.home
    typer.echo("Would create:")
    for directory in plan.directories:
        typer.echo(f"  {_display(directory, home)}")
    typer.echo(f"Would copy {_display(plan.zshrc, home)} to {_display(plan.original, home)}")
    typer.echo(f"Would write lock file: {_display(plan.lock_file, home)}")
    typer.echo(f"Would replace {_display(plan.zshrc, home)} with the shlib bootstrap")


def _print_install_report(report: InstallReport, layout: ShlibLayout) -> None:
    """Print the result of an installation."""
    home = layout.home
    typer.echo("Created:")
    for directory in report.directories:
        typer.echo(f"  {_display(directory, home)}")
    typer.echo(f"Copied {_display(layout.zshrc, home)} to {_display(report.original, home)}")
    typer.echo(f"Wrote lock file: {_display(report.lock_file, home)}")
    typer.echo(f"Wrote shlib bootstrap to {_display(report.zshrc, home)}")


def _print_uninstall_plan(plan: UninstallPlan) -> None:
    """Print what an uninstallation would do."""
    home = plan.layout.home
    exports = len(plan.export_names)
    shlibs = len(plan.shlib_files)
    typer.echo(
        f"Would write flattened {_display(plan.zshrc, home)} with {exports} export(s) and {shlibs} shlib file(s)."
    )
    typer.echo(f"Would move {_display(plan.layout.root, home)} to {_display(plan.backup, home)}")


def _print_uninstall_report(report: UninstallReport, layout: ShlibLayout) -> None:
    """Print the result of an uninstallation."""
    home = layout.home
    exports = len(report.export_names)
    shlibs = len(report.shlib_files)
    typer.echo(f"Wrote flattened {_display(report.zshrc, home)} with {exports} export(s) and {shlibs} shlib file(s).")
    typer.echo(f"Moved {_display(layout.root, home)} to {_display(report.backup, home)}")
    typer.echo(f"Backup: {_display(report.backup, home)}")
