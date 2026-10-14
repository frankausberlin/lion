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
  lion records         read and edit the ADRs attached to recordings
  lion adr             document standalone decisions (ADRs)
  lion classify        classify recorded input blocks (read-only)
  lion serve           run the MCP server on stdio
  lion --version       print the installed version

Run 'lion <command> --help' for details on a command.
"""

SCAN_EPILOG = """The first run creates a history entry, an unchanged run refreshes its
timestamp, and a changed state appends a new entry. Only 'scan' writes to the
history. Every run also regenerates the derived OKF v0.2 wiki under
$XDG_DATA_HOME/lion/wiki/ (index.md plus one page per collector); never edit it
by hand.

Examples:

  lion scan            collect and store the current state
  lion scan --json     emit the result as a single JSON object
"""

STATUS_EPILOG = """Read-only: it never changes the history or the wiki. Running 'lion' without a
command is the same as 'lion status'. The 'wiki:' line below the diff points at
the wiki written by the latest 'scan'; it can be minimally older than the fresh
collection.

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
tools, the history, the data directory, the wiki and the shell library. It never
stores that state and never runs a fix.

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
  R        the active recording's ADR fields and classified blocks (read-only)
  R-       the previous block's one-line classification (read-only)

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
  lion watch R                 ADR fields and blocks of the active recording
  lion watch R-                the previous block in one line
  lion watch stop --note "done"

Secret masking is best-effort; set LION_WATCH_MASK=0 to opt out.
"""

RECORDS_HELP = """Read and edit the decisions (ADRs) attached to recordings as flat ``#-LION:``,
``#+LION:`` and ``#.LION:`` annotation lines:

  status   overview of the recording space and the ADR counters (read-only)
  list     recordings like 'lion watch list' plus an ADR column (read-only)
  adrs     list all ADRs; with REF show one ADR in detail (read-only)
  edit     edit the ADR overlay (adr.toml) of a stopped recording in $EDITOR

Annotations stay inside the recording; the read commands only read them, and
'edit' writes the record's single adr.toml overlay.
"""

RECORDS_EPILOG = """Running 'lion records' without an operation shows the status. Read commands never
collect a state and never write.

The markers '#-LION:' (previous block), '#+LION:' (next block) and '#.LION:'
(ditto) count only as full comment lines outside strings and here-documents.
Fields are 'adr.<name> <value>', 'adr.<name>' deletes a field and 'adr' clears
the entry; '#.LION:' text without an 'adr' token continues the last field.

Examples:

  lion records                 recording space and ADR counters
  lion records list            recordings with event counts and ADR state
  lion records adrs            all ADRs, oldest first
  lion records adrs latest     the newest recording's ADR in detail
  lion records edit latest     edit the ADR overlay of a stopped recording
"""

ADR_HELP = """Document standalone decisions (ADRs) that exist without a recording, such as
switching the default browser. Each ADR is a TOML document under
$XDG_DATA_HOME/lion/adrs/:

  status   overview of the ADR store and the counters (read-only)
  add      create a new ADR; at least one field is required
  edit     edit one ADR in $EDITOR (overwrites and adds fields)
  list     list stored ADRs (read-only)
  show     show one ADR in detail (read-only)

The fields, the completeness rule (title + decision) and the status values are
the same as for recording ADRs; 'lion records' keeps the recording-bound view.
"""

ADR_EPILOG = """Running 'lion adr' without an operation shows the status. Read commands never
collect a state and never write. There is no delete command: 'edit' overwrites
and adds fields, and LION never removes a field. A REF is the id, a unique
prefix or 'latest'.

Examples:

  lion adr                          the ADR counters
  lion adr add --title "Use X" --decision "Because Y" --status accepted
  lion adr add --field component shell --recording latest
  lion adr list                     all ADRs, oldest first
  lion adr show latest              one ADR in detail
  lion adr edit latest              edit an ADR in $EDITOR
"""

CLASSIFY_HELP = """Classify the recorded input blocks of a recording into groups and fields with
the declarative rule set: built-in defaults plus the optional user file
$XDG_CONFIG_HOME/lion/classifier.json.

The groups are packages, services, containers, shell, downloads, interruptions
and checks. A match is metadata derived from the input text, never a claim that
a command ran or succeeded; unsupported shell syntax yields no matches.

The optional policy file $XDG_CONFIG_HOME/lion/policies.toml evaluates every
command segment against declared work rules: a policy carries a human-readable
text and optional success/fail patterns over the group and field catalog.
"""

CLASSIFY_EPILOG = """Read-only: it never collects a state, never changes a recording and never
executes anything. The classification is derived from the raw events on every
run; the JSON carries the rule set version and hash plus one object per block.
With a policy file every block also carries one object per command segment with
its own matches and policy results; a policy outcome never changes the exit
code.

Examples:

  lion classify                the active, else the newest recording
  lion classify latest         one recording by reference
  lion classify --json         emit the classification as a single JSON object

Inside an active recording, 'lion watch R' lists the blocks tersely and
'lion watch R-' shows only the previous block; both stay policy-free.
"""

SERVE_HELP = """Run an MCP server over stdio for MCP clients (editors, agents): a read-only
view of the stored states, recordings, ADRs and classifications, plus two
tools that create and update standalone ADRs.

The server never collects a state; the ledger changes only through 'lion scan'
and 'lion watch' on this machine. Recorded text and ADR values are masked
best-effort (set LION_SERVE_REDACT=0 to opt out); masking is not a security
boundary.
"""

SERVE_EPILOG = """The transport is stdio only; stdout carries pure JSON-RPC and diagnostics go to
stderr. An MCP client starts the server with command "lion" and args ["serve"].
The prompt defaults are read once at startup; a broken file is a loud error.

Examples:

  lion serve    start the MCP server on stdio (blocks until the client stops it)

Read-only commands never write; 'lion_adr_add' and 'lion_adr_update' are the
only writing tools and touch only $XDG_DATA_HOME/lion/adrs/.
"""


def main() -> None:
    """Run the LION command-line interface."""
    from lion.cli import app

    app()


if __name__ == "__main__":
    main()
