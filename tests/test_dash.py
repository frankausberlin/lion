"""Deterministic tests for the ``dash`` content-hash collector."""

import hashlib
import os
from pathlib import Path
from typing import cast

import pytest

from lion.state import dash
from lion.state.collector import CollectorStatus
from lion.state.registry import COLLECTORS


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the home directory used to resolve ``~/.shlib/dash``."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _dash_dir(home: Path) -> Path:
    directory = home / ".shlib" / "dash"
    directory.mkdir(parents=True)
    return directory


def _link(directory: Path, name: str, target: Path) -> Path:
    link = directory / name
    link.symlink_to(target)
    return link


def test_missing_directory_is_ok_and_empty(home: Path) -> None:
    """A missing ``~/.shlib/dash`` is an empty, successful collection."""
    result = dash.COLLECTOR.collect()
    assert result.status == CollectorStatus.OK
    assert result.error == ""
    assert result.data == {"files": []}


def test_hashes_opted_in_file_content(home: Path) -> None:
    """A prefixed link to a regular file records its SHA-256 content digest."""
    target = home / "settings.yml"
    target.write_bytes(b"key: value\n")
    _link(_dash_dir(home), "LIONSTATE_settings.yml", target)

    result = dash.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.data == {
        "files": [{"name": "LIONSTATE_settings.yml", "hash": hashlib.sha256(b"key: value\n").hexdigest()}]
    }


def test_files_are_sorted_by_name(home: Path) -> None:
    """Entries are returned in name order regardless of creation order."""
    directory = _dash_dir(home)
    for name in ("LIONSTATE_b", "LIONSTATE_a", "LIONSTATE_c"):
        target = home / name
        target.write_text(name)
        _link(directory, name, target)

    files = cast("list[dict[str, str]]", dash.COLLECTOR.collect().data["files"])

    assert [entry["name"] for entry in files] == ["LIONSTATE_a", "LIONSTATE_b", "LIONSTATE_c"]


def test_ignores_non_prefixed_plain_directory_and_broken_entries(home: Path) -> None:
    """Only prefixed links to regular files are monitored."""
    directory = _dash_dir(home)
    real = home / "real.conf"
    real.write_text("real")
    _link(directory, "ordinary.conf", real)
    (directory / "LIONSTATE_plain").write_text("plain")
    folder = home / "folder"
    folder.mkdir()
    _link(directory, "LIONSTATE_dir", folder)
    _link(directory, "LIONSTATE_broken", home / "missing")

    result = dash.COLLECTOR.collect()

    assert result.status == CollectorStatus.OK
    assert result.error == ""
    assert result.data == {"files": []}


def test_dash_path_as_file_is_unavailable(home: Path) -> None:
    """A ``dash`` path that exists but is not a directory is unavailable."""
    (home / ".shlib").mkdir()
    (home / ".shlib" / "dash").write_text("not a directory")

    result = dash.COLLECTOR.collect()

    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.error == "dash path is not a directory"
    assert result.data == {"files": []}


def test_unlistable_directory_is_unavailable(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``dash`` directory that cannot be listed is unavailable."""
    _dash_dir(home)

    def boom(_self: Path) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", boom)

    result = dash.COLLECTOR.collect()

    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.error == "Cannot list ~/.shlib/dash"
    assert result.data == {"files": []}


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_unreadable_entry_marks_unavailable_and_keeps_others(home: Path) -> None:
    """An opted-in file that cannot be read is reported while other hashes stay."""
    directory = _dash_dir(home)
    readable = home / "readable.conf"
    readable.write_bytes(b"readable")
    unreadable = home / "secret.conf"
    unreadable.write_bytes(b"secret")
    unreadable.chmod(0o000)
    _link(directory, "LIONSTATE_readable.conf", readable)
    _link(directory, "LIONSTATE_secret.conf", unreadable)

    result = dash.COLLECTOR.collect()

    assert result.status == CollectorStatus.UNAVAILABLE
    assert result.error == "Unreadable dash entry: LIONSTATE_secret.conf"
    assert result.data == {
        "files": [{"name": "LIONSTATE_readable.conf", "hash": hashlib.sha256(b"readable").hexdigest()}]
    }


def test_registry_ends_with_dash() -> None:
    """The ``dash`` collector is registered last."""
    assert COLLECTORS[-1].name == "dash"
