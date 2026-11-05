"""Reference and tail logic shared by ``shlib status`` and ``shlib it``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lion.shlib.bootstrap import BOOTSTRAP, BOOTSTRAP_BEGIN, BOOTSTRAP_END
from lion.shlib.errors import ShlibError
from lion.shlib.layout import ShlibLayout


@dataclass(frozen=True, slots=True)
class Reference:
    """Content that a managed ``.zshrc`` is expected to start with.

    Attributes:
        data: Exact reference bytes.
        path: Lock file that provided the reference, or ``None`` when the
            built-in bootstrap was used.
        warnings: Non-fatal notices, for example a fallback without a lock.
    """

    data: bytes
    path: Path | None
    warnings: tuple[str, ...]


def load_reference(layout: ShlibLayout, current: bytes) -> Reference:
    """Load the reference for the managed ``.zshrc``.

    The lock file is preferred. If it is missing, the built-in bootstrap is
    used when ``current`` starts with it. A symlinked, non-regular or
    unreadable lock file is an error.

    Args:
        layout: Paths below the selected home directory.
        current: Current bytes of ``.zshrc``.

    Returns:
        The reference and any non-fatal notices.

    Raises:
        ShlibError: If the lock file is unusable or no reference can be
            determined.
    """
    lock_file = layout.lock_file
    if lock_file.is_symlink():
        raise ShlibError(f"{lock_file} is a symlink; refusing to use it as a reference")
    if lock_file.exists():
        if not lock_file.is_file():
            raise ShlibError(f"{lock_file} is not a regular file")
        try:
            data = lock_file.read_bytes()
        except OSError as exc:
            raise ShlibError(f"cannot read {lock_file}: {exc}") from exc
        _validate_reference(data, lock_file)
        return Reference(data=data, path=lock_file, warnings=())

    bootstrap = BOOTSTRAP.encode()
    if current.startswith(bootstrap):
        warning = f"{lock_file} is missing; using the built-in bootstrap as reference"
        return Reference(data=bootstrap, path=None, warnings=(warning,))

    raise ShlibError(
        f"{lock_file} is missing and {layout.zshrc} does not begin with the built-in bootstrap; "
        "cannot determine the managed block"
    )


def split_tail(current: bytes, reference: bytes) -> bytes:
    """Return the content appended after the managed reference.

    Args:
        current: Current bytes of ``.zshrc``.
        reference: Bytes the managed ``.zshrc`` is expected to start with.

    Returns:
        Everything after the reference, which may be empty.

    Raises:
        ShlibError: If ``current`` does not start with the reference.
    """
    if not current.startswith(reference):
        raise ShlibError(
            "the managed block does not match the reference; only appended content can be captured. "
            "Move other changes manually"
        )
    return current[len(reference) :]


def _validate_reference(data: bytes, lock_file: Path) -> None:
    """Reject an empty, truncated or incomplete managed block."""
    begin = BOOTSTRAP_BEGIN.encode() + b"\n"
    end = BOOTSTRAP_END.encode()
    if not data.startswith(begin) or not data.endswith(b"\n") or not data.rstrip(b"\n").endswith(end):
        raise ShlibError(
            f"{lock_file} is empty or incomplete; refusing to use it as a reference. "
            "Restore it or remove it to use the built-in bootstrap"
        )
