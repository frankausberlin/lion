"""Bootstrap text and file names of the zsh shlib layout."""

BOOTSTRAP_BEGIN = "# >>>>>> SHLIB for zsh"
"""Marker that starts the bootstrap block in ``~/.zshrc``."""

BOOTSTRAP_END = "# <<<<<< SHLIB for zsh"
"""Marker that ends the bootstrap block in ``~/.zshrc``."""

LOCK_NAME = "shlib-version-of.zshrc"
"""Name of the lock file below ``~/.shlib/locks``."""

ORIGINAL_NAME = "00-original-zshrc.sh"
"""Name of the copied original configuration below ``~/.shlib/shlibs``."""

_BOOTSTRAP_LINES = (
    "# >>>>>> SHLIB for zsh",
    "# Shlib lock check (To avoid the power level warning, simply place this block before the power level code block)",
    'SHLIB_RC_FILE="$HOME/.zshrc"; SHLIB_LOCK_FILE="$HOME/.shlib/locks/shlib-version-of.zshrc"',
    '[ -f "$SHLIB_LOCK_FILE" ] && ! cmp -s "$SHLIB_RC_FILE" "$SHLIB_LOCK_FILE" '
    '&& diff -u --color=always "$SHLIB_LOCK_FILE" "$SHLIB_RC_FILE"',
    "",
    "# Exports all filenames in the folder as environment variables with the file content as the value.",
    'export SHLIB_EXPORTS_DIR="$HOME/.shlib/exports"',
    '[ -d "$SHLIB_EXPORTS_DIR" ] && for f in "$SHLIB_EXPORTS_DIR"/*(N); '
    'do [ -f "$f" ] && export "$(basename "$f")"="$(cat "$f")"; done',
    "",
    "# Executes (sorted) all scripts in SHLIB_LIB_DIR that start with two digits.",
    'export SHLIB_LIB_DIR="$HOME/.shlib/shlibs"',
    '[ -d "$SHLIB_LIB_DIR" ] && for s in "$SHLIB_LIB_DIR"/[0-9][0-9]*(N); do [ -f "$s" ] && source "$s"; done',
    "# <<<<<< SHLIB for zsh",
)

BOOTSTRAP = "\n".join(_BOOTSTRAP_LINES) + "\n"
"""The bootstrap block written to ``~/.zshrc`` and the lock file.

The text ends with exactly one newline.
"""
