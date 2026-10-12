"""Entry point for LION and the CLI help texts.

The help strings live here so that :mod:`lion.cli` stays focused on the
Typer decorators and the command wiring. ``main`` imports the app lazily so the
two modules stay importable in either order without a cycle.
"""

ROOT_EPILOG = """Running 'lion' without a command is the same as 'lion status'.

Common commands:

  lion                 compare the current state with the latest stored one
  lion scan            collect and store the current state
  lion history         list stored states with their stable references
  lion diff 1 2        compare two stored states
  lion doctor          diagnose the machine and write a reviewable fix script
  lion shlib           manage the Zsh shell library (~/.shlib)
  lion shell           manage the watch shell integration (~/.zshrc, ~/.shlib)
  lion watch           record shell sessions and read the recordings
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

DOCTOR_EPILOG = """Read-only. 'doctor' runs a fresh collection and checks the collectors, the external
tools, the history, the data directory and the shell library. It never stores
that state and never runs a fix.

Findings use 'ok', 'warn', 'error' and 'skip'. If anything is 'warn' or 'error',
exactly one reviewable script is written under $XDG_DATA_HOME/lion/recos/ for
you to read and run yourself; LION never runs it.

Examples:

  lion doctor          show the diagnosis and the reco script path
  lion doctor --show   also print the reco script to stdout
  lion doctor --json   emit the findings as a single JSON object
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

Opt a dash file into content monitoring with the LIONSTATE_ prefix:

  ln -s ~/.config/app/settings.toml ~/.shlib/dash/LIONSTATE_app_settings.toml

Load an existing script without moving it:

  ln -s ~/dotfiles/aliases.zsh ~/.shlib/shlibs/20-aliases.sh
"""

SHELL_HELP = """Manage the watch shell integration that records your Zsh input blocks into the
active recording:

  insert   install the integration (a script under ~/.shlib/shlibs when shlib
           is installed, otherwise a marked block in ~/.zshrc)
  remove   remove the recognized artifact again
  status   show mode, path, modified and warnings (read-only)

The integration loads silently and never starts a process in the input path.
"""

SHELL_EPILOG = """Running 'lion shell' without an operation shows the status.

Examples:

  lion shell            show the current status (same as 'lion shell status')
  lion shell insert     install the integration and back up ~/.zshrc
  lion shell remove     remove the integration again

After 'lion shell insert', open a new shell or run 'exec zsh'. Existing shells
pick up start and stop with their next prompt.
"""

WATCH_HELP = """Record shell sessions: start a recording, work in any Zsh, stop it later and
read the captured events:

  start    begin a recording (requires 'lion shell insert')
  stop     end the active recording; optional --note TEXT
  status   show the active recording and a diff since its start (read-only)
  list     list stored recordings with event counts and size (read-only)
  show     print the events of one recording (read-only)

Events are captured in zsh; Python never runs in the input path.
"""

WATCH_EPILOG = """One recording is active per user and machine. A recording survives shell
restarts, 'exec zsh' and reboots: segment liveness is derived from the kernel
boot id plus the writing shell's PID and start time, and a missing exit event
is never treated as success.

Examples:

  lion watch start --note "debugging wifi"
  lion watch status            what changed since the recording started
  lion watch list              stored recordings with events and size
  lion watch show latest       print the recorded events
  lion watch stop --note "done"

Secret masking is best-effort; set LION_WATCH_MASK=0 to opt out.
"""


def main() -> None:
    """Run the LION command-line interface."""
    from lion.cli import app

    app()


if __name__ == "__main__":
    main()
