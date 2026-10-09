"""Regenerate or verify ``docs/reference/cli.md`` from the Typer application.

The CLI reference is a generated artifact: the source of truth is the app in
``src/lion/cli.py`` together with the help texts in ``src/lion/main.py``. This
script runs Typer's own documentation generator, prepends the "do not edit"
header and writes ``docs/reference/cli.md`` in place.

Run it through ``just docs`` (write) or ``just docs-check`` (verify only). Never
edit the output by hand.
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs" / "reference" / "cli.md"

HEADER = """<!-- Generated file. Do not edit by hand. -->
<!-- Source: src/lion/cli.py and src/lion/main.py. Regenerate with: just docs -->

"""


def build_document() -> str:
    """Return the full CLI reference (header plus generated body) as text."""
    command = [
        sys.executable,
        "-m",
        "typer",
        "lion.cli",
        "utils",
        "docs",
        "--name",
        "lion",
        "--title",
        "CLI reference",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"typer docs generation failed with exit code {result.returncode}")

    body = result.stdout.rstrip("\n") + "\n"
    return HEADER + body


def main() -> None:
    """Write ``docs/reference/cli.md`` or, with ``--check``, verify it is current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; fail if the committed page differs from the generated one",
    )
    arguments = parser.parse_args()

    relative = OUTPUT.relative_to(REPO_ROOT)
    content = build_document()

    if arguments.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            raise SystemExit(f"{relative} is out of date; run `just docs`.")
        print(f"{relative} is up to date.")
        return

    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Wrote {relative}")


if __name__ == "__main__":
    main()
