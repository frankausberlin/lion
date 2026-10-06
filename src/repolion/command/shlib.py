"""Install, inspect and flatten the user's Zsh shell library without losing sources."""

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path

import typer

from repolion.command import fail

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


class Action(StrEnum):
    """Supported shlib operations."""

    STATUS = "status"
    INSTALL = "install"
    UNINSTALL = "uninstall"


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


def _home() -> Path:
    home = Path.home()
    if os.environ.get("ZDOTDIR") and Path(os.environ["ZDOTDIR"]).resolve() != home.resolve():
        raise ValueError("Custom ZDOTDIR is not supported; shlib manages ~/.zshrc only.")
    return home


def _status(home: Path) -> dict[str, object]:
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


def _install(home: Path) -> None:
    rc, lock = home / ".zshrc", home / ".zshrc.lock"
    _regular_target(rc)
    old = _read(rc) if rc.exists() else ""
    if _parts(old) is not None:
        typer.echo("Shlib is already installed; no files changed.")
        return
    root = home / ".shlib"
    original = root / "shlibs" / "00-original-zshrc.sh"
    for path in (lock, original, home / ".zshrc.exports"):
        if path.exists() or path.is_symlink():
            raise ValueError(f"Existing file requires manual reconciliation: {path}")
    for path in (root, root / "exports", root / "shlibs", root / "dash"):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError(f"Expected a real directory: {path}")
    if _files(root / "shlibs", scripts=True):
        raise ValueError("Existing shlibs require manual reconciliation before installation.")
    ignore = root / "exports" / ".gitignore"
    if ignore.is_symlink() or (ignore.exists() and _read(ignore) != "*\n!.gitignore\n"):
        raise ValueError(f"Existing exports ignore file requires manual reconciliation: {ignore}")
    _ = _exports(root / "exports")
    _validate(old)
    _validate(BLOCK)
    mode = stat.S_IMODE(rc.stat().st_mode) if rc.exists() else 0o644
    backup = _backup(rc, ".before-shlib") if rc.exists() else None
    for folder in (root, root / "exports", root / "shlibs", root / "dash"):
        folder.mkdir(mode=0o700, exist_ok=True)
    _write(ignore, "*\n!.gitignore\n", 0o600)
    _write(original, old, 0o600)
    try:
        _write(lock, BLOCK, 0o600)
        _write(rc, BLOCK, mode)
    except OSError:
        lock.unlink(missing_ok=True)
        original.unlink(missing_ok=True)
        raise
    typer.echo(f"Shlib installed. Original configuration: {original}")
    if backup:
        typer.echo(f"Backup: {backup}")


def _uninstall(home: Path) -> None:
    rc = home / ".zshrc"
    _regular_target(rc)
    text = _read(rc) if rc.exists() else ""
    parts = _parts(text)
    if parts is None:
        typer.echo("Shlib is not installed; no files changed.")
        return
    before, block, after = parts
    # The documented manual loader is supported; unknown custom commands inside
    # the managed block need reconciliation instead of being silently discarded.
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
    typer.echo(f"Shlib uninstalled. Exports: {target} (600). Backup: {backup}")
    typer.echo(f"Retained: {root} and original backups (.zshrc.before-shlib*).")
    typer.echo("Review file-relative script logic and top-level return statements after flattening.")


def run(action: Action = Action.STATUS, json_output: bool = False) -> None:
    """Run a shlib operation on the current user's home directory."""
    try:
        home = _home()
        if json_output and action != Action.STATUS:
            raise ValueError("--json is supported for shlib status only.")
        if action == Action.INSTALL:
            _install(home)
        elif action == Action.UNINSTALL:
            _uninstall(home)
        else:
            result = _status(home)
            if json_output:
                typer.echo(json.dumps(result))
            else:
                for key, value in result.items():
                    typer.echo(f"{key}: {value}")
    except (OSError, ValueError) as exc:
        fail(exc)
    except subprocess.TimeoutExpired:
        fail(ValueError("Zsh syntax validation timed out; no shell configuration changed."))
