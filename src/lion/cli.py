"""Command-line interface for LION.

This module only wires Typer decorators and options; the command bodies live in
:mod:`lion.command`, one module per command, and the help texts live in
:mod:`lion.main`. ``status`` is the read-only *command*; *state* is the
internal representation it compares.

Help rule: a command group that has a ``status`` subcommand runs it when invoked
without arguments (``lion`` becomes ``lion status``, ``lion shlib`` becomes
``lion shlib status``). Groups without a ``status`` subcommand keep the default
and show their help instead.
"""

from typing import Annotated

import typer

from lion import __version__
from lion.command.adr import run as run_adr
from lion.command.classify import run as run_classify
from lion.command.diff import run as run_diff
from lion.command.doctor import run as run_doctor
from lion.command.history import run as run_history
from lion.command.records import run as run_records
from lion.command.scan import run as run_scan
from lion.command.shell import run as run_shell
from lion.command.shlib import Action
from lion.command.shlib import run as run_shlib
from lion.command.status import run as run_status
from lion.command.watch import run as run_watch
from lion.main import (
    ADR_EPILOG,
    ADR_HELP,
    CLASSIFY_EPILOG,
    CLASSIFY_HELP,
    DIFF_EPILOG,
    DOCTOR_EPILOG,
    HISTORY_EPILOG,
    RECORDS_EPILOG,
    RECORDS_HELP,
    ROOT_EPILOG,
    SCAN_EPILOG,
    SHELL_EPILOG,
    SHELL_HELP,
    SHLIB_EPILOG,
    SHLIB_HELP,
    STATUS_EPILOG,
    WATCH_EPILOG,
    WATCH_HELP,
)

app = typer.Typer(
    name="lion",
    help="Linux Operator Nerd: collect, store and compare the machine state.",
    epilog=ROOT_EPILOG,
    invoke_without_command=True,
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    """Print the package version and stop before any command runs."""
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Run ``lion status`` when no other command is given."""
    if ctx.invoked_subcommand is None:
        run_status(False)


@app.command(epilog=SCAN_EPILOG)
def scan(json_output: Annotated[bool, typer.Option("--json", help="Output the result as JSON.")] = False) -> None:
    """Collect the current state and store it in the history."""
    run_scan(json_output)


@app.command(epilog=STATUS_EPILOG)
def status(json_output: Annotated[bool, typer.Option("--json", help="Output the comparison as JSON.")] = False) -> None:
    """Compare the current state with the latest stored one (read-only)."""
    run_status(json_output)


@app.command(epilog=HISTORY_EPILOG)
def history(
    json_output: Annotated[bool, typer.Option("--json", help="Output the entries as JSON.")] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Show only the newest N entries (oldest first within the selection)."),
    ] = None,
) -> None:
    """List stored states with their stable references."""
    run_history(json_output, limit)


@app.command(epilog=DIFF_EPILOG)
def diff(
    reference: Annotated[str, typer.Argument(help="Older state reference (see 'lion history').")],
    second: Annotated[str | None, typer.Argument(help="Newer state reference; defaults to the latest.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output the comparison as JSON.")] = False,
) -> None:
    """Compare two stored states without collecting or writing."""
    run_diff(reference, second, json_output)


@app.command(epilog=DOCTOR_EPILOG)
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Output the findings as JSON.")] = False,
    show: Annotated[
        bool,
        typer.Option("--show", help="Also print the generated reco script (text mode only)."),
    ] = False,
) -> None:
    """Run a read-only diagnosis and write a reviewable fix script."""
    run_doctor(json_output, show)


@app.command(help=CLASSIFY_HELP, epilog=CLASSIFY_EPILOG)
def classify(
    reference: Annotated[
        str | None,
        typer.Argument(help="Recording reference; defaults to the active or newest recording."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output the classification as JSON.")] = False,
) -> None:
    """Classify the recorded input blocks with the declarative rules (read-only)."""
    run_classify(reference, json_output)


shlib_app = typer.Typer(
    name="shlib",
    help=SHLIB_HELP,
    short_help="Manage the Zsh shell library (~/.shlib).",
    epilog=SHLIB_EPILOG,
    invoke_without_command=True,
    no_args_is_help=False,
)


@shlib_app.callback()
def shlib_root(ctx: typer.Context) -> None:
    """Run ``lion shlib status`` when no operation is given."""
    if ctx.invoked_subcommand is None:
        run_shlib(Action.STATUS, False)


@shlib_app.command("status")
def shlib_status(json_output: Annotated[bool, typer.Option("--json", help="Output status as JSON.")] = False) -> None:
    """Show whether the shell library is installed and report its state."""
    run_shlib(Action.STATUS, json_output)


@shlib_app.command("install")
def shlib_install() -> None:
    """Install the shell library and back up the current ~/.zshrc."""
    run_shlib(Action.INSTALL, False)


@shlib_app.command("uninstall")
def shlib_uninstall() -> None:
    """Flatten scripts into ~/.zshrc and exports into ~/.zshrc.exports."""
    run_shlib(Action.UNINSTALL, False)


app.add_typer(shlib_app)

shell_app = typer.Typer(
    name="shell",
    help=SHELL_HELP,
    short_help="Manage the watch shell integration (~/.zshrc, ~/.shlib).",
    epilog=SHELL_EPILOG,
    invoke_without_command=True,
    no_args_is_help=False,
)


@shell_app.callback()
def shell_root(ctx: typer.Context) -> None:
    """Run ``lion shell status`` when no operation is given."""
    if ctx.invoked_subcommand is None:
        run_shell("status", False)


@shell_app.command("status")
def shell_status(json_output: Annotated[bool, typer.Option("--json", help="Output status as JSON.")] = False) -> None:
    """Show whether the watch integration is inserted and where."""
    run_shell("status", json_output)


@shell_app.command("insert")
def shell_insert() -> None:
    """Insert the watch integration without overwriting anything."""
    run_shell("insert", False)


@shell_app.command("remove")
def shell_remove() -> None:
    """Remove the watch integration again."""
    run_shell("remove", False)


watch_app = typer.Typer(
    name="watch",
    help=WATCH_HELP,
    short_help="Record shell sessions and read the recordings.",
    epilog=WATCH_EPILOG,
    invoke_without_command=True,
    no_args_is_help=False,
)


@watch_app.callback()
def watch_root(ctx: typer.Context) -> None:
    """Run ``lion watch status`` when no operation is given."""
    if ctx.invoked_subcommand is None:
        run_watch("status", None, False)


@watch_app.command("status")
def watch_status(json_output: Annotated[bool, typer.Option("--json", help="Output status as JSON.")] = False) -> None:
    """Show the active recording and what changed since it started."""
    run_watch("status", None, json_output)


@watch_app.command("start")
def watch_start(
    note: Annotated[str, typer.Option("--note", help="Free-text note stored with the recording.")] = "",
) -> None:
    """Start a recording; requires 'lion shell insert'."""
    run_watch("start", note, False)


@watch_app.command("stop")
def watch_stop(
    note: Annotated[str, typer.Option("--note", help="Free-text note stored with the recording.")] = "",
) -> None:
    """Stop the active recording and store its end state."""
    run_watch("stop", note, False)


@watch_app.command("list")
def watch_list(json_output: Annotated[bool, typer.Option("--json", help="Output the list as JSON.")] = False) -> None:
    """List stored recordings with their event counts and size."""
    run_watch("list", None, json_output)


@watch_app.command("show")
def watch_show(
    reference: Annotated[
        str | None,
        typer.Argument(help="Recording reference ('latest', 'active', a unique prefix); defaults to active or latest."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output the recording as JSON.")] = False,
) -> None:
    """Print the events of one recording (read-only)."""
    run_watch("show", reference, json_output)


@watch_app.command("R")
def watch_overview() -> None:
    """Show the active recording's ADR fields and every classified block."""
    run_watch("R", None, False)


@watch_app.command("R-")
def watch_previous() -> None:
    """Show the classified previous block of the active recording."""
    run_watch("R-", None, False)


app.add_typer(shell_app)
app.add_typer(watch_app)

records_app = typer.Typer(
    name="records",
    help=RECORDS_HELP,
    short_help="Read and edit the ADRs attached to recordings.",
    epilog=RECORDS_EPILOG,
    invoke_without_command=True,
    no_args_is_help=False,
)


@records_app.callback()
def records_root(ctx: typer.Context) -> None:
    """Run ``lion records status`` when no operation is given."""
    if ctx.invoked_subcommand is None:
        run_records("status", None, False)


@records_app.command("status")
def records_status(json_output: Annotated[bool, typer.Option("--json", help="Output status as JSON.")] = False) -> None:
    """Show the recording space and the ADR counters (read-only)."""
    run_records("status", None, json_output)


@records_app.command("list")
def records_list(json_output: Annotated[bool, typer.Option("--json", help="Output the list as JSON.")] = False) -> None:
    """List stored recordings with their ADR state (read-only)."""
    run_records("list", None, json_output)


@records_app.command("adrs")
def records_adrs(
    reference: Annotated[
        str | None,
        typer.Argument(help="Recording reference for the detail view; without it all ADRs are listed."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output the ADR(s) as JSON.")] = False,
) -> None:
    """List all ADRs, or show one ADR in detail when REF is given."""
    run_records("adrs", reference, json_output)


@records_app.command("edit")
def records_edit(
    reference: Annotated[
        str | None,
        typer.Argument(help="Recording reference; defaults to the active, else the newest recording."),
    ] = None,
) -> None:
    """Edit the ADR overlay of a stopped recording in $EDITOR."""
    run_records("edit", reference, False)


app.add_typer(records_app)

adr_app = typer.Typer(
    name="adr",
    help=ADR_HELP,
    short_help="Document standalone decisions (ADRs).",
    epilog=ADR_EPILOG,
    invoke_without_command=True,
    no_args_is_help=False,
)


@adr_app.callback()
def adr_root(ctx: typer.Context) -> None:
    """Run ``lion adr status`` when no operation is given."""
    if ctx.invoked_subcommand is None:
        run_adr("status", json_output=False)


@adr_app.command("status")
def adr_status(json_output: Annotated[bool, typer.Option("--json", help="Output status as JSON.")] = False) -> None:
    """Show the standalone ADR counters (read-only)."""
    run_adr("status", json_output=json_output)


@adr_app.command("add")
def adr_add(
    title: Annotated[str, typer.Option("--title", help="The decision title.")] = "",
    decision: Annotated[str, typer.Option("--decision", help="The decision text.")] = "",
    status: Annotated[str, typer.Option("--status", help="The status value (proposed, accepted, superseded).")] = "",
    field: Annotated[
        list[str] | None,
        typer.Option("--field", help="Add a field as NAME=VALUE (repeatable)."),
    ] = None,
    recording_ref: Annotated[
        str,
        typer.Option("--recording", help="Reference a recording the decision belongs to."),
    ] = "",
) -> None:
    """Create a new standalone ADR; at least one field is required."""
    run_adr(
        "add",
        title=title,
        decision=decision,
        status=status,
        fields=field or [],
        recording_ref=recording_ref,
    )


@adr_app.command("edit")
def adr_edit(
    reference: Annotated[
        str | None,
        typer.Argument(help="ADR reference (unique prefix, exact id); defaults to latest."),
    ] = None,
) -> None:
    """Edit one ADR in $EDITOR (overwrites and adds fields)."""
    run_adr("edit", reference)


@adr_app.command("list")
def adr_list(json_output: Annotated[bool, typer.Option("--json", help="Output the list as JSON.")] = False) -> None:
    """List stored ADRs, oldest first (read-only)."""
    run_adr("list", json_output=json_output)


@adr_app.command("show")
def adr_show(
    reference: Annotated[
        str | None,
        typer.Argument(help="ADR reference ('latest', a unique prefix); defaults to latest."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output the ADR as JSON.")] = False,
) -> None:
    """Show one standalone ADR in detail (read-only)."""
    run_adr("show", reference, json_output)


app.add_typer(adr_app)
