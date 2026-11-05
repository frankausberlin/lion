"""Command-line interface for lion."""

import typer

from lion.dash.cli import dash_app
from lion.doctor.cli import doctor_command
from lion.shlib.cli import shlib_app
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
