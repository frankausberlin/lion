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


def get_scans_dir() -> Path:
    """Return the LION scans directory."""
    return get_data_dir() / "scans"
