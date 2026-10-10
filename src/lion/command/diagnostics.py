"""Human-readable capture warnings, separate from JSON stdout."""

import typer


def warn_incomplete(state: dict[str, dict[str, object]]) -> None:
    """Report incomplete collectors even when their data has not changed."""
    for name, section in state.items():
        if section.get("status") != "ok":
            typer.echo(f"Warning: {name}: capture incomplete ({section.get('error', '')}).", err=True)
