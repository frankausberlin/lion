"""Errors raised by the dash commands."""


class DashError(Exception):
    """A user-facing dash failure.

    The message names the affected path and the next sensible step. The CLI
    prints it as ``error: ...`` and exits with status 2.
    """
