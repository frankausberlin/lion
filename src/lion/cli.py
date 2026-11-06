"""Command-line interface for lion."""

from typing import Annotated

import typer

from lion.dash.cli import dash_app
from lion.doctor.cli import doctor_command
from lion.output import display_path
from lion.secret_prefixes import effective_display, load_prefixes
from lion.shlib.cli import shlib_app
from lion.shlib.errors import ShlibError
from lion.shlib.layout import ShlibLayout
from lion.watch.cli import watch_command, watch_dog_app

app = typer.Typer(
    name="lion",
    help="Command-line interface for lion.",
    no_args_is_help=True,
)
app.add_typer(shlib_app, name="shlib")
app.add_typer(dash_app, name="dash")
app.add_typer(watch_dog_app, name="watch-dog")
app.command("watch")(watch_command)
app.command("doctor")(doctor_command)


@app.command(name="list-prefixes")
def list_prefixes_command(
    home: Annotated[
        str | None,
        typer.Option("--home", help="Home directory to operate on. Defaults to the current user's home."),
    ] = None,
) -> None:
    """Show the effective secret prefix rules without scanning any file."""
    try:
        layout = ShlibLayout.from_option(home)
    except ShlibError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    config = load_prefixes(layout.secret_prefixes)
    for line in effective_display(config, home=layout.home):
        typer.echo(line)
    for problem in config.problems:
        location = f"line {problem.line}: " if problem.line is not None else ""
        path = display_path(layout.secret_prefixes, layout.home)
        typer.echo(f"warning: {path}: {location}{problem.message}", err=True)
    raise typer.Exit(0)


@app.command()
def hello(name: str = "world") -> None:
    """Print a greeting."""
    typer.echo(f"Hello, {name}!")


@app.command(name="mcp")
def mcp_command() -> None:
    """Run the MCP server over stdio."""
    from lion import server

    server.mcp.run()


if __name__ == "__main__":
    app()
