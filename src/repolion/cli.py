"""Command-line interface for LION."""

import json
from typing import Annotated, NoReturn

import typer

from repolion.scan import SystemInfo, scan_system
from repolion.storage import load_latest_scan, load_scan, save_scan

app = typer.Typer(name="lion", help="Linux Operator Nerd.")


def _render_system(info: SystemInfo) -> None:
    typer.echo(f"Host:    {info.hostname}")
    typer.echo(f"OS:      {info.distribution} {info.distribution_version}")
    typer.echo(f"Kernel:  {info.kernel}")
    typer.echo(f"Arch:    {info.architecture}")
    typer.echo(f"CPU:     {info.cpu_model}")
    typer.echo(f"Cores:   {info.cpu_logical_cores}")
    typer.echo(f"Memory:  {info.memory_total_bytes // (1024**3)} GiB")


def _fail(exc: OSError | ValueError) -> NoReturn:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


@app.command()
def scan(json_output: Annotated[bool, typer.Option("--json", help="Output the saved scan as JSON.")] = False) -> None:
    """Scan the local Linux system and save the result."""
    try:
        info = scan_system()
        path = save_scan(system_info=info)
        if json_output:
            typer.echo(json.dumps(load_scan(path).to_dict()))
        else:
            _render_system(info)
            typer.echo(f"Saved:   {path}")
    except (OSError, ValueError) as exc:
        _fail(exc)


@app.command()
def status(
    json_output: Annotated[bool, typer.Option("--json", help="Output the latest scan as JSON.")] = False,
) -> None:
    """Show the latest saved system status without running a new scan."""
    try:
        latest_scan = load_latest_scan()
    except (OSError, ValueError) as exc:
        _fail(exc)

    if json_output:
        typer.echo(json.dumps(latest_scan.to_dict() if latest_scan else None))
    elif latest_scan is None:
        typer.echo("No scan found. Run 'lion scan' first.")
    else:
        typer.echo(f"Last scan: {latest_scan.timestamp}")
        _render_system(latest_scan.system)
