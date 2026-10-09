"""Regenerate ``docs/reference/cli.md`` from the Typer application.

The CLI reference is a generated artifact: the source of truth is the app in
``src/lion/cli.py`` together with the help texts in ``src/lion/main.py``. This
script runs Typer's own documentation generator, prepends the "do not edit"
header and writes ``docs/reference/cli.md`` in place.

Run it through ``just docs``. Never edit the output by hand.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs" / "reference" / "cli.md"

HEADER = """<!-- Generated file. Do not edit by hand. -->
<!-- Source: src/lion/cli.py and src/lion/main.py. Regenerate with: just docs -->

"""


def main() -> None:
    """Generate ``docs/reference/cli.md`` and write it in place."""
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
    OUTPUT.write_text(HEADER + body, encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
