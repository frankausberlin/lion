"""Filesystem paths for LION."""

import os
from pathlib import Path


def get_data_dir() -> Path:
    """Return the LION data directory."""
    base = Path(
        os.environ.get(
            "XDG_DATA_HOME",
            Path.home() / ".local" / "share",
        )
    )
    return base / "lion"


def get_history_dir() -> Path:
    """Return the LION state history directory."""
    return get_data_dir() / "history"
