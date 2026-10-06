"""Entry point for LION and the CLI help texts.

The help strings live here so that :mod:`repolion.cli` stays focused on the
Typer decorators and the command wiring. ``main`` imports the app lazily so the
two modules stay importable in either order without a cycle.
"""

ROOT_EPILOG = """Common commands:

  lion                 compare the current state with the latest stored one
  lion scan            collect and store the current state
  lion history         list stored states with their stable references
  lion diff 1 2        compare two stored states
  lion shlib           manage the Zsh shell library (~/.shlib)
  lion --version       print the installed version

Run 'lion <command> --help' for details on a command.
"""

SCAN_EPILOG = """The first run creates a history entry, an unchanged run refreshes its
timestamp, and a changed state appends a new entry. Only 'scan' writes to the
history.

Examples:

  lion scan            collect and store the current state
  lion scan --json     emit the result as a single JSON object
"""

STATUS_EPILOG = """Read-only: it never changes the history. Running 'lion' without a command is
the same as 'lion status'.

Examples:

  lion status          show what changed since the latest stored state
  lion status --json   emit the comparison as a single JSON object
"""

HISTORY_EPILOG = """Entries are listed oldest first. The REF column is the stable reference accepted
by 'lion diff'. '--limit N' shows only the newest N entries while keeping the
global indices.

Examples:

  lion history             list stored states, oldest first
  lion history --limit 5   show only the five newest states
  lion history --json      emit the listing as a single JSON object
"""

DIFF_EPILOG = """Read-only. A reference is a 1-based index from 'lion history', an alias
('latest', 'previous'), the compact reference, a unique prefix or an ISO
timestamp. The second reference defaults to the latest state.

Examples:

  lion diff 1          compare entry 1 with the latest entry
  lion diff 1 2        compare entries 1 and 2
  lion diff previous   compare the previous entry with the latest
"""

SHLIB_HELP = """Manage the Zsh shell library that keeps ~/.zshrc small by moving configuration
into ~/.shlib/:

  exports/   one file per environment variable (name = variable, content = value)
  shlibs/    numbered scripts sourced in order (00-, 01-, ...)
  dash/      your own symlinks to important configuration files
"""

SHLIB_EPILOG = """Running 'lion shlib' without an operation shows the status.

Examples:

  lion shlib            show the current status (same as 'lion shlib status')
  lion shlib install    install into ~/.zshrc and back up the current file
  lion shlib uninstall  flatten scripts into ~/.zshrc and
                        exports into ~/.zshrc.exports

Link a configuration file into dash:

  ln -s ~/.config/app/settings.toml ~/.shlib/dash/app_settings.toml

Load an existing script without moving it:

  ln -s ~/dotfiles/aliases.zsh ~/.shlib/shlibs/20-aliases.sh
"""


def main() -> None:
    """Run the LION command-line interface."""
    from repolion.cli import app

    app()


if __name__ == "__main__":
    main()
