# End-to-end tests

This suite exercises the installed `lion` executable with real external tools
in a disposable Ubuntu 24.04 Docker container. General testing rules live in
[AGENTS.md](../../AGENTS.md#testing).

## Prerequisites and execution

Run from the repository root with Bash, `just`, Docker and an accessible,
running Docker daemon. The image build needs network access to fetch base
images, OS packages and the locked Python dependencies. Python and Zsh for
these tests are installed inside the image.

The build requires BuildKit (the default since Docker 23) for its cache
mounts; the legacy builder is not supported (`DOCKER_BUILDKIT=0` fails on the
`RUN --mount` steps). The image installs only the `e2e` dependency group
(`pytest`), not the `dev` group, so the heavy development tools never enter the
test container. A separate build stage clones [Powerlevel10k][p10k] at the
pinned tag and copies it into the runtime image, so the runtime container stays
offline and needs no `git`.

[p10k]: https://github.com/romkatv/powerlevel10k

```bash
just test-e2e
```

No separate manual image setup is needed. Each invocation runs
[scripts/test-e2e.sh](../../scripts/test-e2e.sh), which:

1. Creates a unique `e2e-artifacts.*` directory in the repository root.
2. Builds the `lion-e2e` image using the local source,
   [Dockerfile](Dockerfile) and `uv.lock`; Docker may reuse cached layers. The
   dependency layer is copied and installed before `src/`, so a source change
   only rebuilds the project and test layers.
3. Splits `tests/e2e/test_*.py` into balanced shards
   ([scripts/e2e_shards.py](../../scripts/e2e_shards.py), greedy LPT packing
   over measured per-file durations) and prints the assignment.
4. Creates one fresh container per shard with networking disabled and
   `LION_E2E_CONTAINER=1`, without host mounts or privileged mode, and starts
   them in parallel.
5. Runs pytest in every container with the `e2e` marker, temporary files
   under `/artifacts/work` and a JUnit report at `/artifacts/junit.xml`.
6. Copies each `/artifacts` to `e2e-artifacts.*/shard-<n>/`, removes the
   containers on exit, and prints a per-shard summary with the tail of every
   failed shard's log. A shard's full output stays in `shard-<n>/run.log`.

The image remains available for subsequent builds. Re-run `just test-e2e`
after changing code or tests so the image contains the current files.

### Sharding

By default the suite runs in four parallel containers
(`LION_E2E_SHARDS`, default 4; override with
`just test-e2e --shards 8`). The measured serial baseline is about 95 s; four
shards finish in roughly 26–27 s wall clock because the largest file
(`test_watch_lifecycle.py`, ~19 s) sets the floor. Each container is fully
isolated: its own tmux server, its own package database, its own `/artifacts`.
`just test-e2e --shards 1` runs one container with the whole file list (the
pre-sharding path).

More shards than files collapse to one shard per file, so no container ever
collects the whole `tests/e2e` directory by accident; a new or unknown test
file always gets scheduled with a fallback duration. The weight table in
`scripts/e2e_shards.py` carries the measured per-file durations (2026-10-13);
refresh the numbers from the newest `junit.xml` when a file changes
significantly.

`just test-e2e-attach` always runs a single container and cannot be combined
with `--shards > 1`.

### Watching a run live

The interactive scenarios drive zsh inside tmux (see [tmux.conf](tmux.conf) and
[watchutil.py](watchutil.py)). To watch a run as it happens, use the attach
variant:

```bash
just test-e2e-attach
```

It runs the same suite but keeps the container afterwards and prints how to
reach the running tmux session:

```bash
docker exec -it lion-e2e-attach tmux ls
docker exec -it lion-e2e-attach tmux attach -t <session>
```

A session exists only while a watch scenario is running, so retry the commands
if the list is empty. This mode is for observation only; the default
`just test-e2e` stays headless and removes the container, so it is the CI path.

## Isolation

Package installation and removal happen only in the container's package
database. The package scenario requires root **inside the disposable
container**; LION itself does not require root. The permissions scenarios
switch to the dedicated unprivileged `lion-e2e` user
using `runuser --preserve-environment`; only their temporary directories are
owned by that user. Package mutation remains root-only. The unprivileged watch
scenario gives the user its own tmux server through a private `TMUX_TMPDIR`
socket directory, so the interactive shell and every file it writes belong to
`lion-e2e`. Shlib and the
`doctor` scenarios
use a temporary `HOME` and `ZDOTDIR`, and package-history and `doctor` tests use
a temporary `XDG_DATA_HOME`.

Run this suite only through `just test-e2e`. Do not enable the opt-in flag
manually on a workstation or execute the package mutation steps there.
The tests check the opt-in flag and Docker marker; the package test also
checks the effective user. These checks prevent accidental execution and
must remain in new scenarios.

Ordinary `pytest`, `just test` and `just check` exclude the `e2e` marker.
The Docker command explicitly selects it. The opted-in container run fails
if any scenario is skipped; missing root
privileges or a missing Docker marker are errors. Outside the runner, guards
skip the suite to prevent accidental system mutations.

## Existing scenarios

| Test | Behavior checked |
| --- | --- |
| [Package lifecycle](test_package_lifecycle.py) | Builds two local dependency-free package versions without maintainer scripts; scans the baseline, installs, upgrades, purges and scans each state. Checks structural changes for install/removal and a nonstructural version change for upgrade. Checks exact package changes, create/confirm/append behavior, persisted TOML, stable history references, diff resolution and unchanged files after read-only commands. A teardown fixture proves a clean package state before the scenario and re-unholds and purges after it, even after a mid-scenario failure. |
| [Collector v2](test_collectors_v2.py) | Scans and asserts the eight collector sections with deterministic bare-profile data (`network` empty, `containers` `runtime=none`), confirmation without volatile fields, a clean read-only `status`, the additive schema-2 contract (an old snapshot without the v2 sections stays readable and is appended on rescan), the v2 and dash `doctor` findings, preserved history bytes after all read-only commands, and a loud failure on a legacy German entry. |
| [Collector dash](test_collector_dash.py) | Scans a real `~/.shlib/dash`: a missing directory is `ok`/empty; a new `LIONSTATE_` link is structural; a content change, a change/revert pair and a link retargeting are nonstructural hash changes; removing a link is structural in `status` and the stored `diff`; a regular file at the dash path, an unlistable directory and, as the unprivileged `lion-e2e` user, unreadable targets yield `unavailable` with a stable, leak-free error and recover; unprefixed/regular/directory/broken links stay ignored. A synthetic secret never appears in any output, stored TOML or generated reco, an `lstat`-based manifest proves read-only preservation, and a scan leaves only its stored entry and the persistent lock. |
| [History contract](test_history_contract.py) | A fresh environment leaves no data path or lock after every bare/read-only command; a one-state `diff` exits 1 without writes. An unwritable history fails `scan` loudly without touching the stored bytes or leaving staging files; an unreadable newest or older entry fails `status`, `history`, `diff` and `scan` with the affected path; a syntactically broken older entry is never skipped, not even by `history --limit 1`; the history manifest is preserved. |
| [Shlib lifecycle](test_shlib_lifecycle.py) | Installs and uninstalls with real Zsh in a temporary home. Checks literal export values, linked scripts, load order, installer additions, syntax-failure handling, file permissions and repeated uninstall. |
| [Doctor environment](test_doctor_environment.py) | Runs `doctor` over a real filesystem: the deterministic bare profile, pure JSON, read-only preservation, reco permissions/content, hostile-filename safety, no-overwrite on a second run, damaged history and legacy `scans/`, and an unusable data path. |
| [Doctor permissions](test_doctor_permissions.py) | Runs scan, shlib installation and doctor as the unprivileged `lion-e2e` user. Checks preservation of existing history, shell files, exports and permissions; denied reco publication returns JSON/exit 1 without partial files. |
| [Runner diagnostics](test_runner.py) | A real subprocess timeout retains the command and partial stdout/stderr; an expected non-zero exit returns both streams separately without raising. |
| [Watch lifecycle](test_watch_lifecycle.py) | Drives real interactive zsh sessions inside tmux (`watchutil.py`) with an isolated `HOME`/`ZDOTDIR` and reads results only from recording files. Checks the full lifecycle (commands, comment input, failure status, duration, secret masking, multiline paste), that a mixed pasted block stays one `cmd` and comment-only input one `comment` (never doubled), `exec zsh` continuity (same PID, new segment, a missing exit is never success), a closed terminal window, two interleaved shells without mixing, the direct `~/.zshrc` block path without shlib (insert, record, remove restores the file byte-for-byte), adoption and record switching across shells (the documented pointer lag applies at the next prompt), foreign `preexec`/`precmd` hooks before and after the integration with late p10k element registration, the silent load and the p10k pointer segment, the loud failure paths, read-only preservation of home and data, and the `shell`/`watch` doctor findings. |
| [Watch exit paths](test_watch_exit_paths.py) | Pins what the recording says about endings: `exit 7` records the exit status and no result for the exit line, SIGKILL leaves no exit event and the segment ends as `gone`, Ctrl-C leaves a non-zero result, a background job's result is the prompt return while the job still runs (proved with a marker file), and pipelines and compound lines carry the whole line's status. |
| [Watch masking](test_watch_masking.py) | Runs the pane under `C.UTF-8` and checks the masking variants (bare, single- and double-quoted values, `Authorization:` headers, `LION_WATCH_MASK=0` opt-out) plus exact UTF-8 roundtrip of command text and a masked UTF-8 secret, and the documented 4096-character cap on a 5000-character command. |
| [Watch races](test_watch_races.py) | Two shells really overlap: while one runs `sleep 2`, the other completes a command and its result is already recorded (the slow result follows only at its prompt, duration ≥ 1 s). Two simultaneous `watch start` (and later `watch stop`) processes produce exactly one winner, a loud loser, no second record directory and no staging leftovers; the one remaining record ends `stopped`. |
| [Watch user](test_watch_user.py) | Runs a full watch lifecycle as the unprivileged `lion-e2e` user inside its own tmux server (private `TMUX_TMPDIR`): the recording space and every file stay user-owned and record directories stay `0700`. A denied overlay write (`records edit` into an unwritable record directory) fails loudly without `adr.toml` or a draft file, while `records adrs` still reads. |
| [Watch p10k](test_watch_p10k.py) | Loads the pinned Powerlevel10k and the committed minimal config before the user dotfiles, then asserts on the prompt itself: the `●` segment appears while a recording is active (with no instant-prompt startup warning), vanishes at the prompt after `stop`, and reappears in an `exec`'d shell because the pointer is read at load. The main scenario asserts the cold start generated the instant-prompt cache and deletes the readiness marker before `exec`, so the warm-run silence assertion cannot pass on a cached prompt or a stale marker. |
| [Records ADR](test_records_adr.py) | Drives real interactive zsh sessions, annotates them with typed `#-LION:`/`#+LION:`/`#.LION:` comment lines plus a mixed paste whose here-document body holds a decoy marker and a partially quoted delimiter (`<<'EO'F`, which closes on `EOF`), and reads results only from recording files via `lion records`. Checks the folded ADR (title/component/decision/status, anchor, `uses` unknown), that neither decoy becomes an annotation, the `records` JSON views, read-only preservation of the data directory, and a scripted-`$EDITOR` `records edit` roundtrip whose overlay `doctor` accepts. |
| [Doctor shlib](test_doctor_shlib.py) | Installs the real shell library and inspects the resulting `shlib.*` findings: clean install, missing reference copy, drifted `.zshrc`, missing directory, `dash/` defects, ambiguous markers and a foreign `ZDOTDIR`. |
| [Records classifier](test_classify_recording.py) | Classifies real recordings through the installed CLI: block order and the silent filter, segment- and block-scope matches, the JSON and text contracts, the terse in-session `lion watch R`/`R-` reads, a user rule file (merge/override by `id`, merged version and hash, doctor `classify.ruleset`) with a loud invalid-rules failure, and the no-record path staying read-only. |
| [Wiki on scan](test_wiki_on_scan.py) | One fresh-home journey through real permissions: `scan` writes `index.md` plus the eight collector pages with relative links and a `snapshot_ref` matching the stored entry, and prints the escape-free index with the `wiki:` line; `status` shows the unchanged index read-only; `doctor` reports `wiki.freshness` ok; `lstat` manifests prove that `status` and `doctor` leave the history and wiki byte-identical. |
| [MCP serve](test_mcp_serve.py) | Drives a real `lion serve` subprocess over stdin/stdout with the official MCP client: the read journey checks history and snapshot against the stored scan entry, the wiki, registry and classifier resources and a prompt, and an `lstat` manifest proves the read phase writes nothing; the write journey creates an ADR through `lion_adr_add`, checks mode `600` and reads it back with `lion adr show`. |

The package scenario retains four distinct history entries even when removal
restores the initial collector data. The shlib scenario compares shell behavior
before and after flattening. Read-only scenarios compare an `lstat`-based
filesystem manifest, so a successful `scan` may add only the permanently
retained `lion/.history.lock` and its derived `lion/wiki/` pages, and a
`doctor` run may add only its `recos/` directory and one script. The classifier scenario asserts on a terse command's output as
the feature channel — the in-session `lion watch R`/`R-` reads — while every
persisted fact is read from the recording files; other scenarios use the
terminal text only for prompt, silence and readiness checks.

Physical GPU discovery is outside this suite's coverage. The image deliberately
installs no `lspci`, no `nvidia-smi` and no container engine, which keeps the
`doctor` hardware, `tools.lspci` and `containers` (`runtime=none`) findings
deterministic; the environment scenario asserts that those tools are absent, so
a base-image change fails loudly. Whether `systemctl` exists drives the
`services` finding, so the scenario asserts the container reality instead of
forcing `ok`. One consequence is that a clean `ok`/`skip`-only `doctor` run is
not reachable in this image, since a missing `lspci` is always at least a
`warn`; the "clean run writes nothing" invariant therefore stays a unit test in
`tests/test_doctor.py`.

The collector v2 scenario shares those image limits deliberately. Networking is
disabled for the container, and `lo`/`eth0` are virtual interfaces without a
`device` sysfs symlink, so the `network` collector reports `status ok` with an
empty `interfaces` list; a physical NIC cannot appear. `containers` runs with
`runtime` `none` and empty lists because no engine exists. `services` is
environment-dependent: the test branches on whether `systemctl` is present and
never forces `ok`. A real engine or NIC scenario belongs on hardware, not in
this image.

## Results and troubleshooting

Read the per-shard summary and the tail of every failed shard's log in the
terminal, plus the final `E2E diagnostics: <path>` line. Within that directory:

| Artifact | Purpose |
| --- | --- |
| `shard-<n>/junit.xml` | Test results and assertion failures of that shard. |
| `shard-<n>/run.log` | Full pytest output of that shard. |
| `shard-<n>/work/**/commands.log` | Package-, shlib- and doctor-scenario commands, exit codes, stdout and stderr. |
| `shard-<n>/work/**/user-commands.log` | Non-root doctor, shlib and watch commands and output. |
| `shard-<n>/work/**/shell-diagnostics/*.txt` | Per-shell failure diagnostics from interactive scenarios: session and pane state, every input step, and the last screen including scrollback. Written only when a scenario fails and never an assertion source. |
| `shard-<n>/work/**/data/lion/history/*.toml` | Persisted package-scenario snapshots. |
| Other files under `shard-<n>/work/` | Temporary shell configuration, `recos/`, and package fixture files. |

If the build fails before container creation, the diagnostics directory may be
empty; use the terminal build output. For Docker connection errors, verify the
daemon and your access to it. For dependency download failures, check build-time
network access. Do not enable runtime networking to fix a build failure.

For assertion failures, start with the failing assertion and corresponding
command log, then inspect the saved snapshots or shell files. Fix the cause and
rerun the runner; do not remove isolation checks to make the test pass.

GitHub Actions runs this suite in a separate job on pull requests and pushes to
`main`, sets `LION_E2E_SHARDS: "4"` explicitly (easy to lower for a slow
runner) and uploads `e2e-artifacts.*` on failure. Locally, diagnostics are
gitignored and can be deleted after inspection.

## Alpha testing

An **alpha test** is a guided, manual pass on a real machine with the
maintainer's own shell, prompt and hardware — the environment the container
deliberately cannot reproduce (see the
[testing strategy](../../docs/explanation/testing-strategy.md) for why). It has
two parts: the **guided scenarios** below, which are repeatable checklists, and
**exploratory scenarios**, free probing beyond them. An alpha test is not a
smoke test, and the word is not used for this phase.

The container already exercises the p10k segment, the instant-prompt silence, the
silent load and the `exec zsh` continuity automatically (see
[Watch p10k](test_watch_p10k.py)); those properties are no longer manual. What
remains is the real journey: look at the dot once, `exec zsh`, close a window,
re-check `doctor`. Run the alpha test after watch-related changes; it
complements, not replaces, `just test-e2e`.

### Guided scenarios

#### Before you start

Run this in the zsh you actually use and keep the answers for the report:

```bash
lion --version
zsh --version
printf 'term=%s\n' "$TERM"
[[ -o interactive_comments ]] && echo "interactive_comments=set" || echo "interactive_comments=MISSING"
printf 'p10k instant prompt=%s\n' "${POWERLEVEL9K_INSTANT_PROMPT:-unset}"
lion shell status
lion shell insert
lion shell status
```

Notes:

- `interactive_comments` must be `set` for comment input to be recorded as
  comments (the Deen Lupysta setup sets it early). If it is `MISSING`, `#` lines
  are executed as commands and show up as failing `cmd` events; fix the shell
  configuration before judging comment input.
- `shell insert` writes exactly one artifact: `~/.shlib/shlibs/90-lion-watch.sh`
  when the shell library is installed, otherwise a marked `# >>>>>> LION watch`
  block in `~/.zshrc`. It backs `~/.zshrc` up to `.before-lion-watch` and never
  overwrites; `shell remove` restores it.
- Keep the default data directory, or export `XDG_DATA_HOME` **in every shell
  that participates** before it starts: the integration resolves the recording
  path when the shell loads. E2 has no command that deletes a recording; test
  records stay under `~/.local/share/lion/recordings/<ref>` until removed by
  hand.

#### 1-minute guided scenario

* copy / paste line after line and execute in zsh
* After the run, copy the complete shell output and analyze it (have it analyzed)

```bash
lion shell insert
lion shell status --json
lion watch start --note "manual 1-minute alpha"
exec zsh
lion watch status
lion watch stop
lion doctor
lion shell remove
lion shell status --json
lion watch list
ls ~/.local/share/lion/recordings
```

#### 10-minute guided scenario

Use one installed integration and `--note` so the test recording is
recognizable. In a **new** terminal:

```bash
lion shell status --json
lion watch start --note "manual 10-minutes alpha"
exec zsh
lion watch status
```

- [ ] A new terminal starts with no LION output before the prompt; the prompt
      appears as fast as usual. Note any output verbatim.
- [ ] `shell status --json` reports `inserted: true` and `modified: false`.
- [ ] After `start`, a red dot (`●`) appears in the right prompt at the next
      prompt; `exec zsh` keeps it. `watch status` shows the active reference and
      the note while running.

Now stop and tidy up:

```bash
lion watch stop
lion doctor
lion shell remove
lion shell status --json
```

- [ ] `stop` removes the dot at the next prompt and `watch status` reports
      `{"active": false}`.
- [ ] `doctor` reports `shell.inserted` and the `watch.*` findings coherently and
      exits `0` (other topics may warn on your machine).
- [ ] After `remove`, `inserted` is `false` and doctor reports an integration
      skip.

Optional real-terminal check: close the window once mid-recording instead of
`stop`. The record stays readable with `lion watch show latest`, the segment is
reported with a derived end, and no LION process is left behind. Comment input,
masking and the everyday paste workflow are covered by the automated
[Watch lifecycle](test_watch_lifecycle.py) scenario.

Finally remove the test recording by hand (there is no delete command in E2):

```bash
lion watch list
ls ~/.local/share/lion/recordings
rm -rf ~/.local/share/lion/recordings/<ref-of-the-alpha-record>
```

Do not blanket-delete the directory if it contains real recordings. If you used
an isolated `XDG_DATA_HOME`, remove the whole directory instead.

### Exploratory scenarios

Beyond the checklists, probe the integration freely on your real configuration.
The point is to find what the scripted scenarios did not anticipate; note
anything surprising with its exact output.

- **The annotation grammar.** Try the `#-LION:`, `#+LION:` and `#.LION:` forms
  in unusual places and shapes: mixed with commands in one paste, inside a
  here-document body, split across a continuation line, with unknown field
  names or values, or with a marker that is not a full comment line. Check what
  `lion records adrs` reports and whether `doctor` flags an unresolved input.
- **Paste and comment workflows.** Paste real snippets (multi-line commands,
  comment blocks, a mix of both) and confirm the events in `lion watch show`.
  Exercise your own `interactive_comments` and paste settings.
- **The real prompt.** Confirm the `●` appears and disappears in *your* p10k
  (or other prompt) setup, survives `exec zsh`, and never prints before the
  first prompt. Note your terminal, multiplexer and font.
- **Your own configuration.** Repeat the journey with the shlib installed and
  with the inline `~/.zshrc` block, and with any `XDG_DATA_HOME` override you
  actually use.

### Reporting back

Send the versions and option values from "Before you start", the PASS/FAIL
verdict per checklist item, and every deviation with its exact output —
especially anything printed before the first prompt. This compact shape keeps
reports comparable:

```text
report: guided|exploratory | <date> | lion <version>
env: zsh <version> | TERM=<term> | terminal/multiplexer=<...> |
     interactive_comments=<set|MISSING> | p10k instant prompt=<mode> | XDG_DATA_HOME=<default|override>
1-minute: PASS|FAIL — <one line>
10-minute: PASS|FAIL — <per checklist item, e.g. "dot after start: PASS">
deviations:
- step <n>: expected <x>, saw <y>; output: <verbatim>
surprises:
- <anything unexpected, verbatim>
```

| Symptom | Likely cause to check |
| --- | --- |
| `#` lines run as commands (status 127) | `interactive_comments` not set. |
| No dot anywhere | shlib/`shell insert` not active, or p10k loaded after the integration with a fixed element list. |
| Dot missing only in old shells | Those shells loaded the integration before the pointer existed; check the next prompt after `start`. |
| `exec zsh` loses the dot | p10k config order; report the exact prompt output. |
| An already-running shell keeps recording after `shell remove` | Expected: loaded hooks stay until stop/exit. |
| `shell status` reports `modified: true` | The artifact predates an update; `shell remove` and `shell insert` again. |

## Adding a scenario

- Add a `test_*.py` file here with `pytestmark = pytest.mark.e2e`. The shared
  [conftest.py](conftest.py) guards every test with the container check and
  provides the `runner` (logged subprocess) and `env` (isolated child
  environment) fixtures; no extra imports are needed. An interactive-zsh
  scenario additionally opts into the shared tmux server with
  `pytest.mark.usefixtures("tmux_server")` and drives the shell through
  [watchutil.py](watchutil.py). The shared
  [e2eutil.py](e2eutil.py) provides `Runner` (use `runner.invoke(...)` to get a
  typed `CommandResult` with separate `stdout`, `stderr` and `returncode` and an
  optional `cwd`), `manifest(root, exclude=...)` for `lstat`-based filesystem
  preservation checks, and the shared `dash_link` and `doctor_findings` helpers.
- Invoke the installed `lion` command as a subprocess. Use real tools for the
  lifecycle under test rather than mocking collectors or package-manager calls.
- Isolate writable state with `tmp_path` and pass environment overrides only
  to the relevant child processes. Use synthetic secret values, never real
  credentials.
- Keep fixtures self-contained and runnable without network access. Avoid
  dependencies between tests or assumptions about their execution order.
- Assert expected changes and preservation: exit codes, output, persisted
  state, permissions where relevant, and no writes from read-only commands.
- Give subprocesses timeouts and record their commands, exit codes and output
  below `tmp_path` so the runner retains useful diagnostics.
- If additional OS tools are required, add them to the Dockerfile's build step.
  Run `just check` and `just test-e2e`, then report the results and any
  remaining coverage limits.
