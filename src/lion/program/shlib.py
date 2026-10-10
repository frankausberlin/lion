"""Install, inspect and flatten the user's Zsh shell library without losing sources.

This module holds the complete shlib business logic and deliberately avoids Typer:
it returns structured data (status) or a list of messages (install/uninstall), and
the command layer in :mod:`lion.command.shlib` owns CLI output and errors.

Install and uninstall serialize themselves through a dedicated process lock
(``~/.shlib.lock``) that is separate from the reference copy ``~/.zshrc.lock``;
the lock file is intentionally left in place and is not an installation remnant.
"""

import fcntl
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import cast

from lion.program.checks import CheckStatus, DoctorContext, Finding

START = "# >>>>>> SHLIB for zsh"
END = "# <<<<<< SHLIB for zsh"
BLOCK = f"""{START}
# LION shlib: keep configuration in ~/.shlib/shlibs.
SHLIB_RC_FILE="$HOME/.zshrc"; SHLIB_LOCK_FILE="$HOME/.zshrc.lock"
[ -f "$SHLIB_LOCK_FILE" ] && ! cmp -s "$SHLIB_RC_FILE" "$SHLIB_LOCK_FILE" && diff -u "$SHLIB_LOCK_FILE" "$SHLIB_RC_FILE"
export SHLIB_EXPORTS_DIR="$HOME/.shlib/exports"
[ -d "$SHLIB_EXPORTS_DIR" ] && for f in "$SHLIB_EXPORTS_DIR"/*(N); do
    [ -f "$f" ] && export "${{f:t}}"="$(cat -- "$f")"
done
export SHLIB_LIB_DIR="$HOME/.shlib/shlibs"
[ -d "$SHLIB_LIB_DIR" ] && for s in "$SHLIB_LIB_DIR"/[0-9][0-9]*(N); do
    [ -f "$s" ] && source "$s"
done
{END}
"""
EXPORT_SOURCE = 'source "$HOME/.zshrc.exports"\n'
EXPORTS_IGNORE_CONTENT = "*\n!.gitignore\n"


class Action(StrEnum):
    """Supported shlib operations."""

    STATUS = "status"
    INSTALL = "install"
    UNINSTALL = "uninstall"


def get_home() -> Path:
    """Return the managed home directory, rejecting a foreign ``ZDOTDIR``."""
    home = Path.home()
    if os.environ.get("ZDOTDIR") and Path(os.environ["ZDOTDIR"]).resolve() != home.resolve():
        raise ValueError("Custom ZDOTDIR is not supported; shlib manages ~/.zshrc only.")
    return home


@contextmanager
def mutation_lock(home: Path) -> Generator[None]:
    """Serialize shlib mutations across processes with an exclusive file lock.

    The lock file is opened without truncation and never unlinked, so its inode
    stays stable and a permanently present lock file is never treated as an
    installation remnant. The lock is released when the handle is closed, which
    also happens on exceptions or process exit. ``shlib status`` never takes it.
    """
    stream = (home / ".shlib.lock").open("a")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another shlib operation is already running; retry when it finishes.") from exc
        yield
    finally:
        stream.close()


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _regular_target(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Refusing to replace non-regular file: {path}")


def _parts(text: str) -> tuple[str, str, str] | None:
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == START]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == END]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("Ambiguous SHLIB markers; repair .zshrc before continuing.")
    a, b = starts[0], ends[0]
    return "".join(lines[:a]), "".join(lines[a : b + 1]), "".join(lines[b + 1 :])


def _files(directory: Path, scripts: bool = False) -> list[Path]:
    if not directory.exists():
        return []
    entries = sorted(directory.iterdir(), key=lambda p: p.name)
    selected = (
        [p for p in entries if re.match(r"^[0-9]{2}", p.name)]
        if scripts
        else [p for p in entries if not p.name.startswith(".")]
    )
    for path in selected:
        if not path.is_file():
            raise ValueError(f"Not a readable regular file or file symlink: {path}")
    return selected


def _exports(directory: Path) -> str:
    lines = ["# Exports consolidated by lion shlib uninstall.\n"]
    for path in _files(directory):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", path.name) is None:
            raise ValueError(f"Invalid export name: {path.name}")
        value = _read(path).rstrip("\n")  # Match the loader's command substitution.
        if "\0" in value:
            raise ValueError(f"NUL byte in export file: {path.name}")
        lines.append(f"export {path.name}={shlex.quote(value)}\n")
    return "".join(lines)


def _validate(text: str) -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        raise ValueError("zsh is required for syntax validation; no shell configuration changed.")
    # Under NOEXEC, top-level negation can yield status 1 even for valid syntax.
    # Parse a function body to avoid evaluating that syntactic exit status.
    source = "function _lion_syntax_check() {\n" + text + "\n}\n"
    result = subprocess.run([zsh, "-f", "-n"], input=source, text=True, capture_output=True, timeout=10, check=False)
    if result.returncode:
        # Zsh diagnostics can echo source lines containing secrets.
        raise ValueError("Zsh syntax validation failed; no shell configuration changed.")


def _write(path: Path, text: str, mode: int) -> None:
    """Publish a fully written file; never follow a destination symlink."""
    _regular_target(path)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            _ = stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _backup(path: Path, suffix: str) -> Path:
    candidate = path.with_name(path.name + suffix)
    index = 0
    while candidate.exists() or candidate.is_symlink():
        index += 1
        candidate = path.with_name(f"{path.name}{suffix}.{index}")
    # Exclusive creation protects existing backups, including broken symlinks.
    with candidate.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        _ = stream.write(path.read_bytes())
    return candidate


def status(home: Path) -> dict[str, object]:
    """Return the read-only installation status for ``home`` without writing."""
    rc = home / ".zshrc"
    root = home / ".shlib"
    text = _read(rc) if rc.exists() else ""
    installed = _parts(text) is not None
    lock = home / ".zshrc.lock"
    warnings: list[str] = []
    for directory in (root / "exports", root / "shlibs"):
        if installed and not directory.is_dir():
            warnings.append(f"Missing directory: {directory}")
    for path in _files(root / "exports"):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", path.name) is None:
            warnings.append(f"Invalid export name: {path.name}")
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            warnings.append(f"Export should have mode 600: {path.name}")
    return {
        "installed": installed,
        "lock": "missing" if not lock.exists() else ("unchanged" if _read(lock) == text else "changed"),
        "shlibs": [p.name for p in _files(root / "shlibs", scripts=True)],
        "exports": [p.name for p in _files(root / "exports")],
        "warnings": warnings,
    }


def _require_clean_install_target(home: Path, root: Path, original: Path, ignore: Path) -> None:
    """Reject existing shlib paths that an install must not overwrite.

    The caller owns ``original`` and ``ignore`` so the paths checked here are
    exactly the paths it later writes.
    """
    for path in (home / ".zshrc.lock", original, home / ".zshrc.exports"):
        if path.exists() or path.is_symlink():
            raise ValueError(f"Existing file requires manual reconciliation: {path}")
    for path in (root, root / "exports", root / "shlibs", root / "dash"):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError(f"Expected a real directory: {path}")
    if _files(root / "shlibs", scripts=True):
        raise ValueError("Existing shlibs require manual reconciliation before installation.")
    if ignore.is_symlink() or (ignore.exists() and _read(ignore) != EXPORTS_IGNORE_CONTENT):
        raise ValueError(f"Existing exports ignore file requires manual reconciliation: {ignore}")


def _install(home: Path) -> list[str]:
    rc, lock = home / ".zshrc", home / ".zshrc.lock"
    _regular_target(rc)
    old = _read(rc) if rc.exists() else ""
    if _parts(old) is not None:
        return ["Shlib is already installed; no files changed."]
    root = home / ".shlib"
    original = root / "shlibs" / "00-original-zshrc.sh"
    ignore = root / "exports" / ".gitignore"
    _require_clean_install_target(home, root, original, ignore)
    _ = _exports(root / "exports")
    _validate(old)
    _validate(BLOCK)
    mode = stat.S_IMODE(rc.stat().st_mode) if rc.exists() else 0o644
    backup = _backup(rc, ".before-shlib") if rc.exists() else None
    for folder in (root, root / "exports", root / "shlibs", root / "dash"):
        folder.mkdir(mode=0o700, exist_ok=True)
    _write(ignore, EXPORTS_IGNORE_CONTENT, 0o600)
    _write(original, old, 0o600)
    try:
        _write(lock, BLOCK, 0o600)
        _write(rc, BLOCK, mode)
    except OSError:
        lock.unlink(missing_ok=True)
        original.unlink(missing_ok=True)
        raise
    messages = [f"Shlib installed. Original configuration: {original}"]
    if backup:
        messages.append(f"Backup: {backup}")
    return messages


def _reject_custom_block(block: str) -> None:
    """Reject a managed block that is neither ours nor the documented legacy one.

    The documented manual loader is supported; unknown custom commands inside the
    managed block need reconciliation instead of being silently discarded.
    """
    legacy = [
        'SHLIB_RC_FILE="$HOME/.zshrc"; SHLIB_LOCK_FILE="$HOME/.zshrc.lock"',
        '[ -f "$SHLIB_LOCK_FILE" ] && ! cmp -s "$SHLIB_RC_FILE" "$SHLIB_LOCK_FILE" '
        '&& diff -u --color=always "$SHLIB_LOCK_FILE" "$SHLIB_RC_FILE"',
        'export SHLIB_EXPORTS_DIR="$HOME/.shlib/exports"',
        '[ -d "$SHLIB_EXPORTS_DIR" ] && for f in "$SHLIB_EXPORTS_DIR"/*(N); do '
        '[ -f "$f" ] && export "$(basename "$f")"="$(cat "$f")"; done',
        'export SHLIB_LIB_DIR="$HOME/.shlib/shlibs"',
        '[ -d "$SHLIB_LIB_DIR" ] && for s in "$SHLIB_LIB_DIR"/[0-9][0-9]*(N); do [ -f "$s" ] && source "$s"; done',
    ]
    commands = [line for line in block.splitlines() if line.strip() and not line.startswith("#")]
    if block != BLOCK and commands != legacy:
        raise ValueError("Custom SHLIB block: reconcile its commands before uninstalling.")


def _uninstall(home: Path) -> list[str]:
    rc = home / ".zshrc"
    _regular_target(rc)
    text = _read(rc) if rc.exists() else ""
    parts = _parts(text)
    if parts is None:
        return ["Shlib is not installed; no files changed."]
    before, block, after = parts
    _reject_custom_block(block)
    root = home / ".shlib"
    if not (root / "shlibs").is_dir() or not (root / "exports").is_dir():
        raise ValueError("Incomplete shlib installation: exports/ and shlibs/ must exist.")
    exports = _exports(root / "exports")
    merged = EXPORT_SOURCE
    for path in _files(root / "shlibs", scripts=True):
        # repr keeps filenames with newlines from injecting shell code in comments.
        merged += f"\n# Shlib: {path.name!r}\n" + _read(path) + "\n"
    new_rc = before + merged + after
    _validate(exports)
    _validate(new_rc)
    target = home / ".zshrc.exports"
    _regular_target(target)
    previous_exports = _read(target) if target.exists() else None
    if previous_exports is not None:
        raise ValueError(f"Existing {target} must be reconciled first; it will not be overwritten.")
    backup = _backup(rc, ".before-shlib-uninstall")
    _write(target, exports, 0o600)
    try:
        _write(rc, new_rc, stat.S_IMODE(rc.stat().st_mode))
    except OSError:
        target.unlink()
        raise
    return [
        f"Shlib uninstalled. Exports: {target} (600). Backup: {backup}",
        f"Retained: {root} and original backups (.zshrc.before-shlib*).",
        "Review file-relative script logic and top-level return statements after flattening.",
    ]


def install(home: Path) -> list[str]:
    """Install the shell library under ``home`` and return the report lines."""
    with mutation_lock(home):
        return _install(home)


def uninstall(home: Path) -> list[str]:
    """Flatten the shell library under ``home`` and return the report lines."""
    with mutation_lock(home):
        return _uninstall(home)


def _dash_checks(directory: Path) -> list[Finding]:
    """Check that every visible entry in ``dash`` is a live symlink."""
    findings: list[Finding] = []
    if not directory.is_dir():
        return findings
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name.startswith("."):
            continue
        if not path.is_symlink():
            findings.append(
                Finding(
                    topic="shlib",
                    name=f"shlib.dash.{path.name}",
                    status=CheckStatus.WARN,
                    message=f"'dash' should contain only symlinks: {path.name} is not a symlink.",
                    hint="Move the file away and recreate it as a symlink.",
                )
            )
        elif not path.exists():
            findings.append(
                Finding(
                    topic="shlib",
                    name=f"shlib.dash.{path.name}",
                    status=CheckStatus.WARN,
                    message=f"Broken symlink in 'dash': {path.name}",
                    hint="Repair the target or remove the symlink.",
                )
            )
    return findings


def shlib_checks(ctx: DoctorContext) -> list[Finding]:
    """Check the shell-library installation state without writing.

    Installation, the ``~/.zshrc.lock`` reference copy and any existing ``dash``
    entries are inspected independently: a missing reference copy warns, and
    ``dash`` defects are reported even when the managed block is absent. Valid
    files retained by a regular uninstall stay allowed. This builds on
    :func:`status`; an ``OSError`` or ``ValueError`` becomes an ``error`` finding
    instead of aborting the whole doctor run.
    """
    try:
        result = status(ctx.home)
    except (OSError, ValueError) as exc:
        return [
            Finding(
                topic="shlib",
                name="shlib.status",
                status=CheckStatus.ERROR,
                message=f"Shlib status unreadable: {exc}",
                hint="Check ~/.zshrc and ~/.shlib manually.",
            )
        ]
    findings: list[Finding] = []
    installed = bool(result.get("installed"))
    if installed:
        findings.append(
            Finding(
                topic="shlib",
                name="shlib.installed",
                status=CheckStatus.OK,
                message="Shlib is installed.",
            )
        )
        warnings = result.get("warnings")
        if isinstance(warnings, list):
            for warning in cast("list[object]", warnings):
                findings.append(
                    Finding(
                        topic="shlib",
                        name="shlib.warnings",
                        status=CheckStatus.WARN,
                        message=str(warning),
                        hint="Check the installation or run 'lion shlib install' again.",
                    )
                )
        lock = result.get("lock")
        if lock == "changed":
            findings.append(
                Finding(
                    topic="shlib",
                    name="shlib.lock",
                    status=CheckStatus.WARN,
                    message="~/.zshrc differs from the reference copy ~/.zshrc.lock.",
                    hint="Apply the intended changes or discard the reference.",
                )
            )
        elif lock == "missing":
            findings.append(
                Finding(
                    topic="shlib",
                    name="shlib.lock",
                    status=CheckStatus.WARN,
                    message="Installation detected, but the reference copy ~/.zshrc.lock is missing.",
                    hint="Recreate the reference: cp ~/.zshrc ~/.zshrc.lock",
                )
            )
    else:
        findings.append(
            Finding(
                topic="shlib",
                name="shlib.installed",
                status=CheckStatus.SKIP,
                message="Shlib is not installed.",
                hint="Optional: 'lion shlib install' enables the shell library.",
            )
        )
    findings.extend(_dash_checks(ctx.home / ".shlib" / "dash"))
    return findings
