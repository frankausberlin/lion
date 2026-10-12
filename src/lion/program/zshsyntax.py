"""Zsh syntax validation shared by the shlib and watch integrations.

The checker only parses (``zsh -n``) and never executes the text. Diagnostics
are deliberately not echoed: zsh can include source lines with secrets in its
error output.
"""

import shutil
import subprocess


def validate_zsh(text: str) -> None:
    """Validate zsh syntax without executing ``text``.

    Args:
        text: The shell configuration to parse.

    Raises:
        ValueError: If ``zsh`` is unavailable or the text is not valid syntax.
    """
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
