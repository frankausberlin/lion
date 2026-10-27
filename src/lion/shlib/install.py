"""Planning and application of ``lion shlib install``."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from lion.shlib.bootstrap import BOOTSTRAP, BOOTSTRAP_BEGIN
from lion.shlib.errors import ShlibError
from lion.shlib.files import atomic_write
from lion.shlib.layout import ShlibLayout


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """Validated work for a shlib installation.

    Attributes:
        layout: Paths below the selected home directory.
        directories: Directories to create with mode ``0700``.
        original: Destination of the copied original ``.zshrc``.
        lock_file: Lock file that mirrors the new ``.zshrc``.
        zshrc: Configuration file to replace.
        original_mode: Permission bits of the original ``.zshrc``.
        bootstrap: Exact bootstrap bytes for the new ``.zshrc`` and lock.
    """

    layout: ShlibLayout
    directories: tuple[Path, ...]
    original: Path
    lock_file: Path
    zshrc: Path
    original_mode: int
    bootstrap: bytes


@dataclass(frozen=True, slots=True)
class InstallReport:
    """Result of a successful shlib installation.

    Attributes:
        directories: Directories created by the installation.
        original: Created copy of the original ``.zshrc``.
        lock_file: Written lock file.
        zshrc: Replaced configuration file.
    """

    directories: tuple[Path, ...]
    original: Path
    lock_file: Path
    zshrc: Path


def plan_install(layout: ShlibLayout) -> InstallPlan:
    """Preflight a shlib installation without changing the filesystem.

    Args:
        layout: Paths below the selected home directory.

    Returns:
        The validated installation plan.

    Raises:
        ShlibError: If the home, the shlib root or the ``.zshrc`` is not in a
            state that allows a safe installation.
    """
    home = layout.home
    if not home.is_dir():
        raise ShlibError(f"home directory does not exist or is not a directory: {home}")

    root = layout.root
    if root.is_symlink() or root.exists():
        raise ShlibError(f"{root} already exists; remove it first or choose another home")

    zshrc = layout.zshrc
    if zshrc.is_symlink():
        raise ShlibError(f"{zshrc} is a symlink; shlib does not overwrite symlinked configuration files")
    if not zshrc.exists():
        raise ShlibError(f"{zshrc} does not exist; create it before running shlib install")
    if not zshrc.is_file():
        raise ShlibError(f"{zshrc} is not a regular file")

    try:
        original = zshrc.read_bytes()
        original_mode = stat.S_IMODE(zshrc.stat().st_mode)
    except OSError as exc:
        raise ShlibError(f"cannot read {zshrc}: {exc}") from exc

    if BOOTSTRAP_BEGIN.encode() in original:
        raise ShlibError(f"shlib is already installed in {zshrc}")

    return InstallPlan(
        layout=layout,
        directories=(root, layout.locks, layout.shlibs, layout.exports),
        original=layout.original,
        lock_file=layout.lock_file,
        zshrc=zshrc,
        original_mode=original_mode,
        bootstrap=BOOTSTRAP.encode(),
    )


def apply_install(plan: InstallPlan) -> InstallReport:
    """Apply a validated installation plan.

    The shlib tree is removed again if a step before replacing ``.zshrc``
    fails, so the original configuration stays usable. If the final atomic
    replacement fails, the prepared tree is kept and named in the error.

    Args:
        plan: Plan returned by :func:`plan_install`.

    Returns:
        A report describing the created paths.

    Raises:
        ShlibError: If the installation cannot be completed.
    """
    try:
        for directory in plan.directories:
            directory.mkdir()
            os.chmod(directory, 0o700)
        shutil.copy2(plan.zshrc, plan.original)
        atomic_write(plan.lock_file, plan.bootstrap, 0o644)
    except OSError as exc:
        shutil.rmtree(plan.layout.root, ignore_errors=True)
        raise ShlibError(f"install failed: {exc}; {plan.zshrc} was left unchanged") from exc

    try:
        atomic_write(plan.zshrc, plan.bootstrap, plan.original_mode)
    except OSError as exc:
        raise ShlibError(
            f"cannot replace {plan.zshrc}: {exc}; the prepared shlib tree remains at {plan.layout.root}"
        ) from exc

    return InstallReport(
        directories=plan.directories,
        original=plan.original,
        lock_file=plan.lock_file,
        zshrc=plan.zshrc,
    )
