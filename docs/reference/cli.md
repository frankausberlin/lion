# CLI

Options and examples for every command live canonically in
`lion <cmd> --help`. This page is a curated directory: it lists what each
command is for and points to its own help instead of repeating its options.

## Global behavior

| Invocation | Behavior |
| --- | --- |
| `lion` | Runs `lion status` (see [CLI conventions](../explanation/cli-conventions.md)). |
| `lion --help` | Shows the top-level help. |
| `lion --version` | Prints the version from the single source in `src/lion/__init__.py` and exits before any collector runs. |

## Implemented commands

| Command | Purpose | Help |
| --- | --- | --- |
| `lion scan` | Collect the current state and store it in the history. The only state command that writes. | `lion scan --help` |
| `lion status` | Compare the current state with the latest stored one; read-only. | `lion status --help` |
| `lion history` | List stored states with their stable references; read-only. | `lion history --help` |
| `lion diff` | Compare two stored states; read-only. | `lion diff --help` |
| `lion shlib` | Manage the Zsh shell library. Without an operation it runs `shlib status`. | `lion shlib --help` |
| `lion shlib status` | Show whether the shell library is installed and report its state. | `lion shlib status --help` |
| `lion shlib install` | Install the shell library and back up the current `~/.zshrc`. | `lion shlib install --help` |
| `lion shlib uninstall` | Flatten scripts into `~/.zshrc` and exports into `~/.zshrc.exports`. | `lion shlib uninstall --help` |

`scan`, `status`, `history` and `diff` accept `--json`; `shlib status` does too.
`history` also accepts `--limit N` to show only the newest `N` entries while
keeping the global indices.

## Planned commands

`shell`, `watch`, `wiki`, `doctor` and `serve` are described in the
[README Getting started table](../../README.md#getting-started) but are not
implemented yet. They appear here only so the command surface is not mistaken
for the current one.
