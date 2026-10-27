"""Command-line interface for lion."""

import typer

app = typer.Typer(
    name="lion",
    help="Command-line interface for lion.",
    no_args_is_help=True,
)


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
