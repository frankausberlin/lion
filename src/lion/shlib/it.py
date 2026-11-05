"""Planning and application of ``lion shlib it``."""

from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lion.shlib.errors import ShlibError
from lion.shlib.files import atomic_write
from lion.shlib.layout import ShlibLayout
from lion.shlib.reference import load_reference, split_tail

UNSORTED_NAME = "99-unsorted.sh"
"""Fallback shlib file that receives appended ``.zshrc`` content."""

_HEADER = b"# Created by `lion shlib it` from content appended to ~/.zshrc. Keep this file at mode 0600.\n"


@dataclass(frozen=True, slots=True)
class ItPlan:
    """Validated work for ``lion shlib it``.

    Attributes:
        layout: Paths below the selected home directory.
        target: Fallback shlib file that receives the tail.
        content: Exact bytes for the target after the capture.
        mode: Permission bits for the target.
        zshrc_content: Exact bytes for the ``.zshrc`` after removing the tail.
        zshrc_mode: Permission bits for the ``.zshrc``.
        tail: Appended ``.zshrc`` bytes.
        tail_bytes: Size of ``tail`` in bytes.
        tail_sha256: Hex digest of ``tail``.
        line_start: First line of the tail in ``.zshrc``.
        line_end: Last line of the tail in ``.zshrc``.
        empty: Whether the tail is empty or whitespace only.
        already_captured: Whether the target already contains this tail hash.
        warnings: Non-fatal notices for the user.
    """

    layout: ShlibLayout
    target: Path
    content: bytes
    mode: int
    zshrc_content: bytes
    zshrc_mode: int
    tail: bytes
    tail_bytes: int
    tail_sha256: str
    line_start: int
    line_end: int
    empty: bool
    already_captured: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ItReport:
    """Result of ``lion shlib it``.

    Attributes:
        target: Fallback shlib file for the tail.
        tail_bytes: Size of the tail in bytes.
        tail_sha256: Hex digest of the tail.
        line_start: First line of the tail in ``.zshrc``.
        line_end: Last line of the tail in ``.zshrc``.
        empty: Whether there was nothing to capture.
        already_captured: Whether the tail was already present.
        wrote: Whether the target was written.
        wrote_zshrc: Whether the tail was removed from ``.zshrc``.
        warnings: Non-fatal notices produced during planning.
    """

    target: Path
    tail_bytes: int
    tail_sha256: str
    line_start: int
    line_end: int
    empty: bool
    already_captured: bool
    wrote: bool
    wrote_zshrc: bool
    warnings: tuple[str, ...]

    @property
    def line_count(self) -> int:
        """Return the number of captured lines."""
        if self.empty:
            return 0
        return self.line_end - self.line_start + 1


def plan_it(layout: ShlibLayout) -> ItPlan:
    """Preflight a capture of content appended to ``.zshrc``.

    Only the tail after the managed reference is read. Applying the plan
    removes that tail, so the ``.zshrc`` matches the lock file afterwards. A
    missing or damaged shlib tree, a mismatched managed block or an unusable
    target aborts before anything is changed.

    Args:
        layout: Paths below the selected home directory.

    Returns:
        The validated capture plan.

    Raises:
        ShlibError: If the tree, the ``.zshrc`` or the target cannot be used
            safely.
    """
    root = layout.root
    if root.is_symlink():
        raise ShlibError(f"{root} is a symlink; shlib does not support symlinked roots")
    if not root.is_dir():
        raise ShlibError(f"{root} does not exist; run shlib install first")

    shlibs = layout.shlibs
    if shlibs.is_symlink() or not shlibs.is_dir():
        raise ShlibError(f"{shlibs} is missing or not a real directory")

    zshrc = layout.zshrc
    if zshrc.is_symlink():
        raise ShlibError(f"{zshrc} is a symlink; shlib does not read symlinked configuration files")
    if not zshrc.exists():
        raise ShlibError(f"{zshrc} does not exist")
    if not zshrc.is_file():
        raise ShlibError(f"{zshrc} is not a regular file")
    try:
        current = zshrc.read_bytes()
        zshrc_mode = stat.S_IMODE(zshrc.stat().st_mode)
    except OSError as exc:
        raise ShlibError(f"cannot read {zshrc}: {exc}") from exc

    reference = load_reference(layout, current)
    tail = split_tail(current, reference.data)
    if b"\0" in tail:
        raise ShlibError(f"the appended content in {zshrc} contains a NUL byte, which shlib cannot capture")

    warnings = list(reference.warnings)
    if not tail:
        return ItPlan(
            layout=layout,
            target=shlibs / UNSORTED_NAME,
            content=b"",
            mode=0o600,
            zshrc_content=reference.data,
            zshrc_mode=zshrc_mode,
            tail=tail,
            tail_bytes=len(tail),
            tail_sha256=hashlib.sha256(tail).hexdigest(),
            line_start=0,
            line_end=0,
            empty=True,
            already_captured=False,
            warnings=tuple(warnings),
        )

    digest = hashlib.sha256(tail).hexdigest()
    line_start = reference.data.count(b"\n") + 1
    line_end = line_start + _line_count(tail) - 1
    target = shlibs / UNSORTED_NAME

    if target.is_symlink():
        raise ShlibError(f"{target} is a symlink; refusing to write the captured content")
    if target.exists():
        if not target.is_file():
            raise ShlibError(f"{target} is not a regular file")
        try:
            existing = target.read_bytes()
            mode = stat.S_IMODE(target.stat().st_mode)
        except OSError as exc:
            raise ShlibError(f"cannot read {target}: {exc}") from exc
        if mode & 0o077:
            warnings.append(
                f"{target} is readable by group or others; shlibs are inlined by uninstall, so consider chmod 600"
            )
        already_captured = _contains_hash(existing, digest)
    else:
        existing = b""
        mode = 0o600
        already_captured = False

    return ItPlan(
        layout=layout,
        target=target,
        content=_render(existing, tail, digest),
        mode=mode,
        zshrc_content=reference.data,
        zshrc_mode=zshrc_mode,
        tail=tail,
        tail_bytes=len(tail),
        tail_sha256=digest,
        line_start=line_start,
        line_end=line_end,
        empty=False,
        already_captured=already_captured,
        warnings=tuple(warnings),
    )


def apply_it(plan: ItPlan) -> ItReport:
    """Apply a validated capture plan.

    The target is written first. Only then is the tail removed from the
    ``.zshrc`` by atomically replacing it with the reference content. An empty
    plan is a no-op.

    Args:
        plan: Plan returned by :func:`plan_it`.

    Returns:
        A report describing the capture and the ``.zshrc`` rewrite.

    Raises:
        ShlibError: If the target or the ``.zshrc`` cannot be written.
    """
    wrote = False
    wrote_zshrc = False
    if not plan.empty:
        if not plan.already_captured:
            try:
                atomic_write(plan.target, plan.content, plan.mode)
            except OSError as exc:
                raise ShlibError(f"cannot write {plan.target}: {exc}; {plan.layout.zshrc} was left unchanged") from exc
            wrote = True

        try:
            atomic_write(plan.layout.zshrc, plan.zshrc_content, plan.zshrc_mode)
        except OSError as exc:
            raise ShlibError(
                f"cannot remove the captured tail from {plan.layout.zshrc}: {exc}; "
                f"the tail is still in {plan.layout.zshrc} and also runs from {plan.target}"
            ) from exc
        wrote_zshrc = True

    return ItReport(
        target=plan.target,
        tail_bytes=plan.tail_bytes,
        tail_sha256=plan.tail_sha256,
        line_start=plan.line_start,
        line_end=plan.line_end,
        empty=plan.empty,
        already_captured=plan.already_captured,
        wrote=wrote,
        wrote_zshrc=wrote_zshrc,
        warnings=plan.warnings,
    )


def _line_count(tail: bytes) -> int:
    """Return the number of lines in ``tail``, including a last line without newline."""
    count = tail.count(b"\n")
    if tail and not tail.endswith(b"\n"):
        count += 1
    return count


def _contains_hash(existing: bytes, digest: str) -> bool:
    """Return whether ``existing`` already carries a marker for ``digest``."""
    return b"sha256:" + digest.encode() in existing


def _render(existing: bytes, tail: bytes, digest: str) -> bytes:
    """Assemble the target content around the captured tail."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    chunks: list[bytes] = []
    if existing:
        chunks.append(existing)
        if not existing.endswith(b"\n"):
            chunks.append(b"\n")
        chunks.append(b"\n")
    else:
        chunks.append(_HEADER)
        chunks.append(b"\n")

    chunks.append(f"# >>> lion shlib it {timestamp} sha256:{digest}\n".encode())
    chunks.append(tail)
    if not tail.endswith(b"\n"):
        chunks.append(b"\n")
    chunks.append(f"# <<< lion shlib it sha256:{digest}\n".encode())
    return b"".join(chunks)
