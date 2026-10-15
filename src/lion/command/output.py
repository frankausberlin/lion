"""Shared stdout/stderr printers for command reports and status mappings.

Every command that prints a program-layer report (messages on stdout, warnings
on stderr) and a status mapping (JSON or ``key: value`` text) uses these
helpers, so the user-facing shapes cannot drift between commands.
"""

import json
from collections.abc import Mapping
from typing import Protocol, cast

import typer

__all__ = ["ReportLike", "print_report", "print_status"]


class ReportLike(Protocol):
    """A program-layer report: stdout messages and stderr warnings."""

    @property
    def messages(self) -> tuple[str, ...]:
        """The report lines printed to stdout."""
        ...

    @property
    def warnings(self) -> tuple[str, ...]:
        """The warnings printed to stderr."""
        ...


def print_report(report: ReportLike) -> None:
    """Print the report messages to stdout and its warnings to stderr."""
    for message in report.messages:
        typer.echo(message)
    for warning in report.warnings:
        typer.echo(f"Warning: {warning}", err=True)


def print_status(result: Mapping[str, object], *, json_output: bool) -> None:
    """Print the status mapping as JSON or as ``key: value`` text lines."""
    if json_output:
        typer.echo(json.dumps(result))
        return
    for key, value in result.items():
        if key == "warnings":
            for warning in cast("list[object]", value):
                typer.echo(f"warning: {warning}")
        else:
            typer.echo(f"{key}: {value}")
