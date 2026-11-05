"""Key handling and file scan tests for the redaction core."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from lion.redaction import (
    KEY_MODE,
    KEY_SIZE,
    MAX_SCAN_BYTES,
    RedactionKeyError,
    create_key,
    fingerprint,
    read_key,
    scan_file,
)


def test_create_key_writes_private_random_bytes(tmp_path: Path) -> None:
    """A new key is 32 random bytes with mode 0600."""
    path = tmp_path / ".redaction-key"

    data = create_key(path)

    assert len(data) == KEY_SIZE
    assert path.read_bytes() == data
    assert stat.S_IMODE(path.stat().st_mode) == KEY_MODE


def test_create_key_refuses_existing_path(tmp_path: Path) -> None:
    """An existing key is never overwritten."""
    path = tmp_path / ".redaction-key"
    path.write_bytes(b"x" * KEY_SIZE)

    with pytest.raises(FileExistsError):
        create_key(path)

    assert path.read_bytes() == b"x" * KEY_SIZE


def test_create_key_removes_partial_file_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed key write does not leave a partial key behind."""
    path = tmp_path / ".redaction-key"

    def fail_chmod(target: object, mode: int) -> None:
        raise OSError("chmod failed")

    monkeypatch.setattr("lion.redaction.os.chmod", fail_chmod)

    with pytest.raises(OSError, match="chmod failed"):
        create_key(path)

    assert not path.exists()


def test_read_key_returns_none_when_missing(tmp_path: Path) -> None:
    """A missing key is not an error."""
    assert read_key(tmp_path / ".redaction-key") is None


def test_read_key_returns_bytes(tmp_path: Path) -> None:
    """A valid key is returned unchanged."""
    path = tmp_path / ".redaction-key"
    path.write_bytes(b"k" * KEY_SIZE)

    assert read_key(path) == b"k" * KEY_SIZE


def test_read_key_rejects_symlink(tmp_path: Path) -> None:
    """A symlinked key is never followed."""
    target = tmp_path / "target"
    target.write_bytes(b"k" * KEY_SIZE)
    path = tmp_path / ".redaction-key"
    path.symlink_to(target)

    with pytest.raises(RedactionKeyError, match="symlink"):
        read_key(path)


def test_read_key_rejects_directory(tmp_path: Path) -> None:
    """A directory at the key path is invalid."""
    path = tmp_path / ".redaction-key"
    path.mkdir()

    with pytest.raises(RedactionKeyError, match="not a regular file"):
        read_key(path)


def test_read_key_rejects_wrong_size(tmp_path: Path) -> None:
    """Empty and short keys are invalid."""
    path = tmp_path / ".redaction-key"
    path.write_bytes(b"")

    with pytest.raises(RedactionKeyError, match="exactly 32 bytes"):
        read_key(path)


def test_read_key_reports_read_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable key is an error, not a missing key."""
    path = tmp_path / ".redaction-key"
    path.write_bytes(b"k" * KEY_SIZE)
    original = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == path:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    with pytest.raises(RedactionKeyError, match="cannot read"):
        read_key(path)


def test_fingerprint_is_stable_and_key_bound() -> None:
    """Fingerprints are stable for a key and change with another key."""
    first = fingerprint("secret", b"a" * KEY_SIZE)
    second = fingerprint("secret", b"a" * KEY_SIZE)
    other_key = fingerprint("secret", b"b" * KEY_SIZE)
    other_value = fingerprint("other", b"a" * KEY_SIZE)

    assert first == second
    assert first != other_key
    assert first != other_value
    assert len(first) == 12


def test_scan_file_reports_provider_token(tmp_path: Path) -> None:
    """A provider token in a file is detected."""
    path = tmp_path / "config"
    path.write_text("ghp_" + "a" * 36 + "\n", encoding="utf-8")

    result = scan_file(path)

    assert result.skipped is None
    assert result.findings[0].kind == "github-token"


def test_scan_file_masks_sensitive_file_name(tmp_path: Path) -> None:
    """A sensitive export file name makes the whole value a finding."""
    path = tmp_path / "SERVICE_PASSWORD"
    path.write_text("hunter2\n", encoding="utf-8")

    result = scan_file(path, key_name=path.name)

    assert result.findings[0].kind == "password"
    assert result.findings[0].start == 0
    assert result.findings[0].end == len("hunter2\n")


def test_scan_file_skips_placeholder_file_value(tmp_path: Path) -> None:
    """A placeholder value is not reported even for a sensitive file name."""
    path = tmp_path / "PASSWORD"
    path.write_text("changeme\n", encoding="utf-8")

    assert scan_file(path, key_name=path.name).findings == ()


def test_scan_file_skips_binary_content(tmp_path: Path) -> None:
    """NUL bytes and invalid UTF-8 are skipped as binary."""
    nul = tmp_path / "nul"
    nul.write_bytes(b"a\x00b")
    invalid = tmp_path / "invalid"
    invalid.write_bytes(b"\xff")

    assert scan_file(nul).skipped == "binary content"
    assert scan_file(invalid).skipped == "binary content"


def test_scan_file_skips_oversized_file(tmp_path: Path) -> None:
    """Files above the cap are skipped."""
    path = tmp_path / "big"
    path.write_bytes(b"a" * (MAX_SCAN_BYTES + 1))

    assert scan_file(path).skipped == "larger than 1 MiB"


def test_scan_file_skips_non_regular_paths(tmp_path: Path) -> None:
    """Directories and missing files are skipped."""
    assert scan_file(tmp_path).skipped == "not a regular file"
    assert scan_file(tmp_path / "missing").skipped == "unreadable"


def test_scan_file_reports_read_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read error skips the file instead of raising."""
    path = tmp_path / "value"
    path.write_text("x\n", encoding="utf-8")
    original = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == path:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    assert scan_file(path).skipped == "unreadable"
