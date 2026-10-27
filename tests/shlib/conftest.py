"""Shared fixtures for the shlib tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lion.shlib.layout import ShlibLayout


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Return a throwaway home with an original ``.zshrc``."""
    directory = tmp_path / "home"
    directory.mkdir()
    (directory / ".zshrc").write_text("export ORIGINAL=1\n", encoding="utf-8")
    return directory


@pytest.fixture
def layout(home: Path) -> ShlibLayout:
    """Return the shlib layout for the throwaway home."""
    return ShlibLayout(home)
