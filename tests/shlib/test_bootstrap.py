"""Tests for the bootstrap text."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lion.shlib.bootstrap import BOOTSTRAP

EXPECTED = """\
# >>>>>> SHLIB for zsh
# Shlib lock check
SHLIB_RC_FILE="$HOME/.zshrc"; SHLIB_LOCK_FILE="$HOME/.shlib/locks/shlib-version-of.zshrc"
[ -f "$SHLIB_LOCK_FILE" ] && ! cmp -s "$SHLIB_RC_FILE" "$SHLIB_LOCK_FILE" \\
&& diff -u --color=always "$SHLIB_LOCK_FILE" "$SHLIB_RC_FILE"

# Exports all filenames in the folder as environment variables with the file content as the value.
export SHLIB_EXPORTS_DIR="$HOME/.shlib/exports"
[ -d "$SHLIB_EXPORTS_DIR" ] && for f in "$SHLIB_EXPORTS_DIR"/*(N);
do [ -f "$f" ] && export "$(basename "$f")"="$(cat "$f")"; done

# Executes (sorted) all scripts in SHLIB_LIB_DIR that start with two digits.
export SHLIB_LIB_DIR="$HOME/.shlib/shlibs"
[ -d "$SHLIB_LIB_DIR" ] && for s in "$SHLIB_LIB_DIR"/[0-9][0-9]*(N); do [ -f "$s" ] && source "$s"; done
# <<<<<< SHLIB for zsh
"""


def test_bootstrap_matches_expected_text() -> None:
    """The bootstrap matches the exact text with one trailing newline."""
    assert BOOTSTRAP == EXPECTED


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed")
def test_bootstrap_is_valid_zsh(tmp_path: Path) -> None:
    """The bootstrap block parses as zsh.

    zsh 5.9 returns status 1 for a valid block containing ``!`` under ``-n``,
    so syntax errors are detected by the diagnostics on stderr.
    """
    script = tmp_path / "bootstrap.zsh"
    script.write_text(BOOTSTRAP, encoding="utf-8")

    result = subprocess.run(["zsh", "-n", str(script)], capture_output=True, text=True, check=False)

    assert result.stderr == ""
    assert result.returncode in (0, 1)
