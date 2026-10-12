"""Shared helpers for the end-to-end suite.

:class:`CommandResult` is the typed return value of one subprocess invocation
and :func:`manifest` captures the observable filesystem state so read-only
commands can be checked for preservation without following symlinks. The
:class:`Runner` executes commands while recording their diagnostics.
"""

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, cast

#: Default timeout in seconds for one subprocess invocation.
DEFAULT_TIMEOUT = 60

#: A filesystem manifest keyed by the POSIX-style path relative to its root.
Manifest = dict[str, tuple[object, ...]]


@dataclass(frozen=True)
class CommandResult:
    """The streams and exit code of one finished subprocess."""

    stdout: str
    stderr: str
    returncode: int


class UserTmux(NamedTuple):
    """How to reach the unprivileged tmux server of the ``user_tmux`` fixture.

    ``prefix`` runs the tmux client as ``lion-e2e`` (so the server it talks to
    belongs to that user), ``env`` carries the private ``TMUX_TMPDIR`` socket
    directory. Both are passed to ``watchutil.Zsh``.
    """

    prefix: tuple[str, ...]
    env: dict[str, str]


def doctor_findings(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    """Index a ``doctor --json`` payload's findings by their ``name``.

    This is the single place that knows the doctor findings shape, so the
    scenario files cannot drift apart when the schema changes.
    """
    raw = payload["findings"]
    assert isinstance(raw, list), raw
    result: dict[str, dict[str, object]] = {}
    for item in cast("list[object]", raw):
        assert isinstance(item, dict), item
        finding = cast("dict[str, object]", item)
        result[str(finding["name"])] = finding
    return result


def dash_link(home: Path, name: str, content: str) -> Path:
    """Create ``~/.shlib/dash/<name>`` resolving to a fresh content file.

    Encodes the ``LIONSTATE_`` opt-in convention shared by the dash scenarios:
    the target lives under ``home/config/<name>.data`` and an existing link is
    replaced. Returns the created target.
    """
    dash = home / ".shlib" / "dash"
    dash.mkdir(parents=True, exist_ok=True)
    target = home / "config" / f"{name}.data"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    link = dash / name
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)
    return target


def _excluded(relative: str, exclude: tuple[str, ...]) -> bool:
    """Return whether a relative path matches an excluded path or subtree."""
    return any(relative == item or relative.startswith(f"{item}/") for item in exclude)


def manifest(root: Path, *, exclude: tuple[str, ...] = ()) -> Manifest:
    """Return an ``lstat``-based snapshot of every path below ``root``.

    Entries are keyed by their POSIX-style path relative to ``root`` and sorted
    by that key. A directory maps to ``("dir", mode)``, a regular file to
    ``("file", mode, content)`` and a symlink to ``("link", target)`` without
    being followed. Neither ``atime`` nor ``mtime`` is recorded, so a manifest
    is stable across reads. ``exclude`` drops harness artefacts by their
    relative path (or whole subtree).
    """
    entries: Manifest = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not _excluded((base / name).relative_to(root).as_posix(), exclude)]
        for name in sorted([*dirnames, *filenames]):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if _excluded(relative, exclude):
                continue
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                entries[relative] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                entries[relative] = ("dir", mode)
            elif stat.S_ISREG(info.st_mode):
                entries[relative] = ("file", mode, path.read_bytes())
            else:
                entries[relative] = ("other", mode)
    return entries


class Runner:
    """Run subprocesses, recording command, exit code, stdout and stderr."""

    def __init__(self, log: Path, prefix: tuple[str, ...] = ()) -> None:
        """Store the log path used for every invocation."""
        self._log = log
        self._prefix = prefix

    def invoke(
        self,
        *args: str,
        env: dict[str, str],
        expected: int | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        cwd: str | None = None,
    ) -> CommandResult:
        """Run ``args``, returning both streams and the exit code.

        ``expected=None`` performs no exit-code assertion; pass an integer to
        fail loudly on an unexpected exit code.
        """
        environment = {**env, "LC_ALL": "C"}
        command = [*self._prefix, *args]
        with self._log.open("a", encoding="utf-8") as stream:
            _ = stream.write(f"$ {command!r}\n")
            stream.flush()
            try:
                result = subprocess.run(
                    command,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    cwd=cwd,
                )
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
                stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
                _ = stream.write(f"timeout={timeout}\nstdout:\n{stdout}\nstderr:\n{stderr}\n")
                raise
            _ = stream.write(f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n")
        if expected is not None:
            assert result.returncode == expected, (
                f"{args!r} exited {result.returncode}, expected {expected}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return CommandResult(stdout=result.stdout, stderr=result.stderr, returncode=result.returncode)

    def __call__(
        self,
        *args: str,
        env: dict[str, str],
        expected: int = 0,
        timeout: int = DEFAULT_TIMEOUT,
        cwd: str | None = None,
    ) -> str:
        """Run ``args`` and return stdout, failing loudly on an unexpected exit code."""
        return self.invoke(*args, env=env, expected=expected, timeout=timeout, cwd=cwd).stdout
