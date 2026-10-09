![](https://lh3.googleusercontent.com/d/16oJMjpAhsFov-BWYSM1P7JZASx2hIPc5)

# Lion - The Linux Assisten

*(🚧 WIP)* The project is in an early stage of development.


**LION (Linux Operator Nerd)** is your Linux assistant. It tracks the machine status with collectors, enables structured system individualization with the shell library (shlib), optimizes agentic work on the system with an mcp server, enables the storage and management of shell recordings, offers a monitoring mechanism for commands with regex-based qualification and creates an OKF v0.2 compliant wiki about the system.

***Lion requires no root and modifies no system files. Its writes are confined to Lion's own directories and the shell startup files it manages.***

## Base mechanics

* It's a **command**: `lion [command [subcommand]] [parameter]`
* The **scan creates** a current **system status** and adds it to the **history**.
* **Status** shows a summary of the current status and the difference from the previous one.
* The **Shlib system** monitors changes to the **.zshrc** and **organizes** its contents into individual files.
* The **Shell** command integrates an **optional recording** and description mechanism with a **warning function**.
* With **Wiki**, an **OKF v0.2** (Open Knowledge Format) compliant wiki is created from the **current state**.
* The **Serve** command starts an **MCP server** with tools, resources and prompts, it is used **exclusively by harnesses**.


## Commands overwiev

### realized

|command|sub|params|description|
|-|-|-|-|
|||--help\|--version|<li>Shows the the help / version|
|**shlib**<br><br><br>|[*status*]<br>*install*<br>*uninstall*||<li>Shows the status of the Shlib system<br><li>Installs the Shlib system<br><li>Removes the Shlib system|
|**scan**||--json|<li>collect the current state and save it|
|[**status**]||--json|<li>compare the current state with the latest saved one|
|**history**||--json\|--limit &lt;nr>|<li>list every stored state and its stable reference|
|**diff**||&lt;nr>\|&lt;nr> &lt;nr>\|previous|<li>compare two stored states|

### planned

|command|sub|params|description|
|-|-|-|-|
|**shell**<br><br><br>|[*status*]<br>*insert*<br>*remove*||<li>Shows whether the watch hook is inserted in zsh<br><li>Inserts the watch hook into zsh<br><li>Removes the watch hook from zsh|
|**watch**<br><br><br><br>|[*status*]<br>*start*<br>*stop*<br>*dog*||<li>Shows the status of lion watch<br><li>start watching<br><li>stop watching and offer the option to enter a description of the recording<br><li>Start in watch-dog-mode (requires confirmation of critical orders)|
|**wiki**<br><br>|[*status*]<br>*sync*||<li>Shows the status of the wiki<br>Rebuild the wiki with the current status and recording list|
|**doctor**|||<li>makes doctor stuff (under construction)|
|**serve**<br><br><br>|*tools*<br>*resources*<br>*prompts*||<li>Tools for agents to access Lion functions<br><li>The lion states and the recordings<br><li>Short recipes for agents to work optimally|

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
