"""Command-line interface for LION."""

import typer

from repolion.paths import get_data_dir, get_scans_dir
from repolion.scan import scan_system
from repolion.storage import load_latest_scan, save_scan

app = typer.Typer(
    name="lion",
    help="Linux Operator Nerd.",
)


@app.command()
def init() -> None:
    """Initialize LION."""
    data_dir = get_data_dir()
    scans_dir = get_scans_dir()

    scans_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"LION initialized at {data_dir}")


@app.command()
def scan() -> None:
    """Scan the local Linux system."""
    info = scan_system()

    typer.echo(f"OS:      {info.distribution} {info.distribution_version}")
    typer.echo(f"Kernel:  {info.kernel}")
    typer.echo(f"Arch:    {info.architecture}")
    typer.echo(f"CPU:     {info.cpu_model}")
    typer.echo(f"Cores:   {info.cpu_logical_cores}")
    typer.echo(f"Memory:  {info.memory_total_bytes // (1024**3)} GiB")

    path = save_scan(system_info=info)
    typer.echo(f"Saved:   {path}")


@app.command()
def status() -> None:
    """Show the latest system status."""
    latest_scan = load_latest_scan()

    if not latest_scan:
        typer.echo("No scan found. Run 'lion scan' first.")
        return

    typer.echo(f"Last scan: {latest_scan.timestamp}")
    typer.echo(f"OS:      {latest_scan.system.distribution} {latest_scan.system.distribution_version}")
    typer.echo(f"Kernel:  {latest_scan.system.kernel}")
    typer.echo(f"Arch:    {latest_scan.system.architecture}")
    typer.echo(f"CPU:     {latest_scan.system.cpu_model}")
    typer.echo(f"Cores:   {latest_scan.system.cpu_logical_cores}")
    typer.echo(f"Memory:  {latest_scan.system.memory_total_bytes // (1024**3)} GiB")
