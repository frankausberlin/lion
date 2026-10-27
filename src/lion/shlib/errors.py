"""Errors raised by the shlib commands."""


class ShlibError(Exception):
    """A user-facing shlib failure.

    The message names the affected path and the next sensible step. The CLI
    prints it as ``error: ...`` and exits with status 1.
    """
