![](https://lh3.googleusercontent.com/d/16oJMjpAhsFov-BWYSM1P7JZASx2hIPc5)

# Lion

*(🚧 WIP)* The project is in an early stage of development.


**LION - Linux Operator Nerd** tracks the machine status with collectors, enables structured system individualization with the shell library (shlib), optimizes agentic work on the system with an mcp server, enables the storage and management of shell recordings, offers a monitoring mechanism for commands with regex-based qualification and creates an OKF v0.2 compliant wiki about the system.

***Lion requires no root and modifies no system files. Its writes are confined to Lion's own directories and the shell startup files it manages.***


## Getting started

Requires Linux and Python 3.12 or newer.

|`lion`|command|sub|params|description|
|-|-|-|-|-|
||||--help\|--version|Shows the the help / version|
||**shlib**<br><br><br>|[*status*]<br>*install*<br>*uninstall*||Shows the status of the Shlib system<br>Installs the Shlib system<br>Removes the Shlib system|
||**scan**||--json|collect the current state and save it|
||[**status**]||--json|compare the current state with the latest saved one|
||**history**||--json\|--limit &lt;nr>|list every stored state and its stable reference|
||**diff**||&lt;nr>\|&lt;nr> &lt;nr>\|previous|compare two stored states|
||**shell**<br><br><br>|[*status*]<br>*insert*<br>*remove*||Shows whether the watch hook is inserted in zsh<br>Inserts the watch hook into zsh<br>Removes the watch hook from zsh|
||**watch**<br><br><br><br>|[*status*]<br>*start*<br>*stop*<br>*dog*||Shows the status of lion watch<br>start watching<br>stop watching and offer the option to enter a description of the recording<br>Start in watch-dog-mode (requires confirmation of critical orders)|
||**wiki**<br><br>|[*status*]<br>*sync*||Shows the status of the wiki<br>Rebuild the wiki with the current status and recording list|
||**doctor**|||makes doctor stuff (under construction)|
||**serve**<br><br><br>|*tools*<br>*resources*<br>*prompts*||Tools for agents to access Lion functions<br>The lion states and the recordings<br>Short recipes for agents to work optimally|

> * No initialization step is needed. `scan` creates its data directory automatically.
> * Lion serve is only used in the agent harness
> * tools, resources, prompts are not subcommands, just the description of the mcp functions used
> * `shlib`, `scan`, `status`, `history` and `diff` exist today; `shell`, `watch`, `wiki`, `doctor` and `serve` are planned.

## Quickstart

```bash
git clone https://github.com/frankausberlin/lion.git
cd lion
uv sync
uv run lion scan     # capture the first state
uv run lion status   # compare the current state with the latest stored one
```

See the [getting-started tutorial](docs/tutorials/getting-started.md) for the
guided walkthrough.

## Documentation

The full documentation lives in [`docs/`](docs/index.md), organized by
[Diátaxis](https://diataxis.fr/):

- [Tutorials](docs/tutorials/getting-started.md) — learn by doing.
- [How-to guides](docs/how-to/manage-shell-config.md) — one task at a time.
- [Reference](docs/reference/cli.md) — CLI, data structures, storage and
  collectors.
- [Explanation](docs/explanation/architecture.md) — architecture, comparison
  model and the write boundary.
- [Decisions](docs/decisions/0001-no-root-write-boundary.md) — accepted ADRs.

Options are documented canonically in `lion <cmd> --help`.

## Development

```bash
just test       # tests and coverage; minimum 90%
just lint       # lint, formatting and type check
just fix        # auto-fix lint issues
just check      # full quality gate
just test-e2e   # opt-in Docker suite, excluded from just check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, workflow and conventions, and
the [E2E guide](tests/e2e/README.md) for the Docker suite.

## License

See [LICENSE](LICENSE).

See [AGENTS.md](AGENTS.md) for AI agent guidelines.
