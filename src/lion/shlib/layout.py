"""Path layout of the zsh shlib installation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lion.shlib.bootstrap import LOCK_NAME, ORIGINAL_NAME
from lion.shlib.errors import ShlibError


@dataclass(frozen=True, slots=True)
class ShlibLayout:
    """Paths used by ``lion shlib`` below a home directory.

    Attributes:
        home: Home directory on which the command operates.
    """

    home: Path

    @property
    def root(self) -> Path:
        """Return the shlib root directory."""
        return self.home / ".shlib"

    @property
    def zshrc(self) -> Path:
        """Return the zsh configuration file."""
        return self.home / ".zshrc"

    @property
    def locks(self) -> Path:
        """Return the lock directory."""
        return self.root / "locks"

    @property
    def shlibs(self) -> Path:
        """Return the directory with sourced shlib files."""
        return self.root / "shlibs"

    @property
    def exports(self) -> Path:
        """Return the directory with exported values."""
        return self.root / "exports"

    @property
    def lock_file(self) -> Path:
        """Return the lock file that mirrors the generated ``.zshrc``."""
        return self.locks / LOCK_NAME

    @property
    def original(self) -> Path:
        """Return the copy of the former ``.zshrc``."""
        return self.shlibs / ORIGINAL_NAME

    @classmethod
    def from_option(cls, home: str | None) -> ShlibLayout:
        """Build a layout from the ``--home`` option.

        Args:
            home: Home directory given on the command line, or ``None`` to use
                the current user's home directory.

        Returns:
            The resolved layout.

        Raises:
            ShlibError: If the home directory cannot be determined.
        """
        if home is None:
            try:
                path = Path.home()
            except RuntimeError as exc:
                raise ShlibError(f"cannot determine the home directory: {exc}") from exc
        else:
            path = Path(home).expanduser()
        try:
            return cls(path.resolve())
        except OSError as exc:
            raise ShlibError(f"cannot resolve home directory {path}: {exc}") from exc
