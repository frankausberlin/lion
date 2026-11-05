"""Typer sub-app for ``lion shlib``."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from lion.shlib.bootstrap import ORIGINAL_NAME
from lion.shlib.errors import ShlibError
from lion.shlib.install import InstallPlan, InstallReport, apply_install, plan_install
from lion.shlib.it import UNSORTED_NAME, ItPlan, ItReport, apply_it, plan_it
from lion.shlib.layout import ShlibLayout
from lion.shlib.status import INSTALLED, StatusReport, collect_status
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
NoColorOption = Annotated[
    bool,
    typer.Option("--no-color", help="Disable ANSI colors in the status output."),
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
    """Flatten the shlib layout into ~/.zshrc and keep the old tree as a backup.

    Owner-only export files are written to ``secrets.sh`` inside the backup and
    sourced from the flattened ``.zshrc`` instead of being inlined.
    """
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
    _print_secret_notice(plan, dry_run=dry_run)

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


@shlib_app.command("status")
def status_command(home: HomeOption = None, no_color: NoColorOption = False) -> None:
    """Show installation, drift and cleanup state without changing anything."""
    try:
        layout = ShlibLayout.from_option(home)
    except ShlibError as exc:
        _fail(exc)

    report = collect_status(layout)
    for line in render_status(report, color=status_color_enabled(no_color)):
        typer.echo(line)
    raise typer.Exit(report.exit_code)


@shlib_app.command("it")
def it_command(home: HomeOption = None, dry_run: DryRunOption = False) -> None:
    """Copy content appended to ~/.zshrc into ~/.shlib/shlibs/99-unsorted.sh.

    The .zshrc is never changed. The copied tail keeps running from .zshrc
    until it is removed there manually; the user must then resolve and delete
    99-unsorted.sh.
    """
    try:
        layout = ShlibLayout.from_option(home)
    except ShlibError as exc:
        _fail(exc)
    try:
        plan = plan_it(layout)
    except ShlibError as exc:
        _fail(exc, layout.home)

    for warning in plan.warnings:
        typer.echo(f"warning: {_display_text(warning, layout.home)}")

    if plan.empty:
        typer.echo(f"No appended content after the managed block in {_display(layout.zshrc, layout.home)}.")
        return

    if plan.already_captured:
        typer.echo(
            f"Tail already captured in {_display(plan.target, layout.home)} (sha256:{_short_hash(plan.tail_sha256)})."
        )
        _print_twice_warning(plan, layout)
        return

    if dry_run:
        _print_it_capture(plan, layout, prefix="Would capture")
        _print_twice_warning(plan, layout)
        typer.echo("Dry run: no changes made.")
        return

    try:
        report = apply_it(plan)
    except ShlibError as exc:
        _fail(exc, layout.home)
    _print_it_capture(report, layout, prefix="Captured")
    _print_twice_warning(report, layout)


def _print_it_capture(capture: ItPlan | ItReport, layout: ShlibLayout, *, prefix: str) -> None:
    """Print the capture summary without revealing appended content."""
    home = layout.home
    typer.echo(
        f"{prefix} {capture.line_end - capture.line_start + 1} line(s) from "
        f"{_display(layout.zshrc, home)} into {_display(capture.target, home)}."
    )
    typer.echo(
        f"Tail: lines {capture.line_start}-{capture.line_end}, {capture.tail_bytes} bytes "
        f"(sha256:{_short_hash(capture.tail_sha256)})"
    )


def _print_twice_warning(capture: ItPlan | ItReport, layout: ShlibLayout) -> None:
    """Warn that the tail still runs from ``.zshrc`` until removed."""
    typer.echo(
        f"warning: the tail is still in {_display(layout.zshrc, layout.home)} and will run twice "
        f"until you remove lines {capture.line_start}-{capture.line_end}."
    )
    typer.echo(
        f"hint: resolve {_display(capture.target, layout.home)} into sorted shlib files, then delete it; "
        "the status stays yellow until then."
    )


def _short_hash(digest: str) -> str:
    """Return the short form used in user-facing hash output."""
    return digest[:12] + "…"


def status_color_enabled(no_color: bool) -> bool:
    """Return whether ANSI colors should be used."""
    return not no_color and "NO_COLOR" not in os.environ and sys.stdout.isatty()


def render_status(report: StatusReport, *, color: bool) -> list[str]:
    """Render the status report as plain lines with optional ANSI colors."""
    home = report.layout.home
    installation_color = typer.colors.GREEN if report.installation == INSTALLED else typer.colors.RED
    if report.zshrc == "ok":
        zshrc_color = typer.colors.GREEN
    elif report.zshrc == "drift":
        zshrc_color = typer.colors.YELLOW
    else:
        zshrc_color = typer.colors.RED
    unsorted_color = typer.colors.YELLOW if report.unsorted_exists else typer.colors.GREEN
    original_color = typer.colors.YELLOW if report.original_exists else typer.colors.GREEN

    zshrc = report.zshrc
    if report.zshrc_detail is not None:
        zshrc = f"{zshrc} ({report.zshrc_detail})"

    lines = [
        f"shlib: {_display(report.layout.root, home)}",
        f"installation: {_style(report.installation, installation_color, color)}",
        f"zshrc: {_style(zshrc, zshrc_color, color)}",
        f"exports: {report.exports} (owner-only: {report.owner_only_exports})",
        f"shlibs: {report.shlibs}",
        f"original: {_style(report.original, original_color, color)}",
        f"unsorted: {_style(report.unsorted, unsorted_color, color)}",
        f"backups: {report.backups}",
    ]
    pending = [
        name
        for name, exists in ((ORIGINAL_NAME, report.original_exists), (UNSORTED_NAME, report.unsorted_exists))
        if exists
    ]
    if pending:
        lines.append(
            _style(
                f"hint: resolve {', '.join(pending)}, then delete; the status stays yellow until then",
                typer.colors.YELLOW,
                color,
            )
        )
    for issue in report.issues:
        lines.append(_style(f"issue: {_display_text(issue, home)}", typer.colors.RED, color))
    return lines


def _style(text: str, color: str, enabled: bool) -> str:
    """Return ``text`` with ``color`` when colors are enabled."""
    if not enabled:
        return text
    return typer.style(text, fg=color)


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


def _export_summary(plan: UninstallPlan | UninstallReport) -> str:
    """Return the export count wording for a plan or report."""
    exports = len(plan.export_names)
    if plan.secret_names:
        return f"{exports} public export(s), {len(plan.secret_names)} owner-only export(s)"
    return f"{exports} export(s)"


def _print_secret_notice(plan: UninstallPlan, *, dry_run: bool) -> None:
    """Print the owner-only export notice without revealing names or values."""
    if not plan.secret_names:
        return
    home = plan.layout.home
    zshrc = _display(plan.zshrc, home)
    secrets_path = _display(plan.secrets_path, home)
    count = len(plan.secret_names)
    if dry_run:
        flattened = f"{count} owner-only export file(s) would not be flattened into {zshrc}."
        stored = f"They would be stored in {secrets_path} (mode 0600)"
        sourced = f"and would be sourced by {zshrc}."
        backup = "The backup directory would become part of your shell configuration; keep it or"
    else:
        flattened = f"{count} owner-only export file(s) were not flattened into {zshrc}."
        stored = f"They are stored in {secrets_path} (mode 0600)"
        sourced = f"and are sourced by {zshrc}."
        backup = "The backup directory is now part of your shell configuration; keep it or"
    typer.echo(f"warning: {flattened}")
    typer.echo(f"         {stored}")
    typer.echo(f"         {sourced}")
    typer.echo("         Only exports without group/other permission bits (chmod 600) are treated as secrets.")
    typer.echo(f"         {backup}")
    typer.echo(f"         move the file and update {zshrc}.")


def _print_uninstall_plan(plan: UninstallPlan) -> None:
    """Print what an uninstallation would do."""
    home = plan.layout.home
    shlibs = len(plan.shlib_files)
    typer.echo(
        f"Would write flattened {_display(plan.zshrc, home)} with {_export_summary(plan)} and {shlibs} shlib file(s)."
    )
    typer.echo(f"Would move {_display(plan.layout.root, home)} to {_display(plan.backup, home)}")


def _print_uninstall_report(report: UninstallReport, layout: ShlibLayout) -> None:
    """Print the result of an uninstallation."""
    home = layout.home
    shlibs = len(report.shlib_files)
    typer.echo(
        f"Wrote flattened {_display(report.zshrc, home)} with {_export_summary(report)} and {shlibs} shlib file(s)."
    )
    typer.echo(f"Moved {_display(layout.root, home)} to {_display(report.backup, home)}")
    typer.echo(f"Backup: {_display(report.backup, home)}")
