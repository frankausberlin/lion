"""Errors raised by ``lion watch``."""


class WatchError(Exception):
    """A user-facing watch failure.

    The CLI prints it as ``error: ...`` on stderr and exits with status 2.
    """


class WatchDogError(Exception):
    """A user-facing ``lion watch-dog`` failure.

    The CLI prints it as ``error: ...`` on stderr and exits with status 2.
    """
