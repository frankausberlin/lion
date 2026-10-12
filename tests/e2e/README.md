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
test container.

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
3. Creates a fresh container with networking disabled and
   `LION_E2E_CONTAINER=1`, without host mounts or privileged mode.
4. Runs pytest against `tests/e2e` with the `e2e` marker, temporary files
   under `/artifacts/work` and a JUnit report at `/artifacts/junit.xml`.
5. Copies `/artifacts` to the host diagnostics directory and removes the
   container on exit, including after test failures when a container exists.

The image remains available for subsequent builds. Re-run `just test-e2e`
after changing code or tests so the image contains the current files.

## Isolation

Package installation and removal happen only in the container's package
database. The package scenario requires root **inside the disposable
container**; LION itself does not require root. The permissions scenarios
switch to the dedicated unprivileged `lion-e2e` user
using `runuser --preserve-environment`; only their temporary directories are
owned by that user. Package mutation remains root-only. Shlib and the
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
| [Watch lifecycle](test_watch_lifecycle.py) | Drives real interactive zsh sessions behind a pty (`watchutil.py`) with an isolated `HOME`/`ZDOTDIR` and reads results only from recording files. Checks the full lifecycle (commands, comment input, failure status, duration, secret masking, multiline paste), that a mixed pasted block stays one `cmd` and comment-only input one `comment` (never doubled), `exec zsh` continuity (same PID, new segment, a missing exit is never success), SIGHUP, two interleaved shells without mixing, the silent load and the p10k pointer segment, the loud failure paths, read-only preservation of home and data, and the `shell`/`watch` doctor findings. |
| [Doctor shlib](test_doctor_shlib.py) | Installs the real shell library and inspects the resulting `shlib.*` findings: clean install, missing reference copy, drifted `.zshrc`, missing directory, `dash/` defects, ambiguous markers and a foreign `ZDOTDIR`. |

The package scenario retains four distinct history entries even when removal
restores the initial collector data. The shlib scenario compares shell behavior
before and after flattening. Read-only scenarios compare an `lstat`-based
filesystem manifest, so the permanently retained `lion/.history.lock` is the
only path a successful `scan` may add and a `doctor` run may add only its
`recos/` directory and one script.

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

Read the pytest summary in the terminal and the final
`E2E diagnostics: <path>` line. Within that directory:

| Artifact | Purpose |
| --- | --- |
| `junit.xml` | Test results and assertion failures. |
| `work/**/commands.log` | Package-, shlib- and doctor-scenario commands, exit codes, stdout and stderr. |
| `work/**/user-commands.log` | Non-root doctor and shlib commands and output. |
| `work/**/data/lion/history/*.toml` | Persisted package-scenario snapshots. |
| Other files under `work/` | Temporary shell configuration, `recos/`, and package fixture files. |

If the build fails before container creation, the diagnostics directory may be
empty; use the terminal build output. For Docker connection errors, verify the
daemon and your access to it. For dependency download failures, check build-time
network access. Do not enable runtime networking to fix a build failure.

For assertion failures, start with the failing assertion and corresponding
command log, then inspect the saved snapshots or shell files. Fix the cause and
rerun the runner; do not remove isolation checks to make the test pass.

GitHub Actions runs this suite in a separate job on pull requests and pushes to
`main`, and uploads `e2e-artifacts.*` on failure. Locally, diagnostics are
gitignored and can be deleted after inspection.

## Test scenarios for humans

The automated suite runs in a bare container with no Powerlevel10k and no real
terminal. A few watch properties can only be accepted on a real machine: the
p10k segment, the instant-prompt silence, a real terminal close and the everyday
paste workflow. Run this walkthrough manually after watch-related changes; it
complements, not replaces, `just test-e2e`.

The scenarios are independent in intent but share one installed integration;
`H1`–`H3` cover the core path, `H4` continuity, `H5`–`H6` failure and teardown.
Every `watch start` uses a `--note` so the test recordings are recognizable in
`watch list`.

### Before you start

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
  configuration before judging the comment scenario.
- `shell insert` writes exactly one artifact: `~/.shlib/shlibs/90-lion-watch.sh`
  when the shell library is installed, otherwise a marked `# >>>>>> LION watch`
  block in `~/.zshrc`. It backs `~/.zshrc` up to `.before-lion-watch` and never
  overwrites; `shell remove` restores it.
- Keep the default data directory, or export `XDG_DATA_HOME` **in every shell
  that participates** before it starts: the integration resolves the recording
  path when the shell loads. E2 has no command that deletes a recording; test
  records stay under `~/.local/share/lion/recordings/<ref>` until removed by
  hand (see H6).

### H1 — Installation, silent load and instant prompt

- [ ] A new terminal starts with no LION output and no p10k instant-prompt
      warning about the integration.

In a **new** terminal:

```bash
lion shell status --json
lion watch status --json
```

PASS: `inserted` is `true` and `modified` is `false`; `watch status` reports
`{"active": false}` and no dot is shown. Before the first prompt, the terminal
shows nothing about watch, the integration or a scan. Note any such output
verbatim; measure whether the prompt appears as fast as before.

### H2 — The dot follows start and stop from every shell

Shell A (already open) and shell B (new terminal):

```bash
lion watch start --note "manual H2"
```

- [ ] A red dot (`●`) appears in the right prompt of A and B at their next
      prompt (p10k loaded).
- [ ] `exec zsh` in A keeps the dot (the pointer is read at load).
- [ ] `lion watch stop` removes the dot at the next prompt in both shells.

Shell A:

```bash
lion watch status
exec zsh
lion watch stop
```

PASS: the dot exists only while a recording is active; `watch status` shows the
active reference, the note and an alive segment while running. If you configured
a custom right prompt, note whether `lion_watch` was appended as expected.

### H3 — Captured content, info typing and masking

Start in shell A:

```bash
lion watch start --note "manual H3"
```

Paste this mixed block as **one** paste (comments as information, commands in
between), then a standalone comment line, a failing command and a fake secret:

```bash
# manual H3: this info line is part of the pasted block
echo "mixed block ran"
# a second info line inside the same block
echo "second command ran"
```

```bash
# standalone info: this line must be captured as its own comment
```

```bash
false
export API_KEY=not-a-real-secret-h3
```

Inspect while active, then stop:

```bash
lion watch status
lion watch show --json
lion watch stop
```

PASS:

- the mixed paste is **one** `cmd` event whose text contains both `#` info lines
  (it was executed as a block);
- the standalone comment is **one** `comment` event and was not executed (no
  `command not found`);
- `false` produced a result with status `1`; every result has a duration;
- the fake secret appears nowhere: the assignment shows as `API_KEY=***`;
- LION's own commands typed inside the recorded shell (including
  `lion watch status`) are recorded too — they are input and that is expected.

### H4 — Continuity across `exec zsh` and a terminal close

Start in shell A:

```bash
lion watch start --note "manual H4"
```

- [ ] `exec zsh` mid-recording keeps the PID, starts a second segment and loses
      nothing.
- [ ] A closed terminal window (SIGHUP) leaves a readable record with derived
      segment ends; a missing `exit` is never shown as success.

Shell A:

```bash
echo "before exec"
exec zsh
echo "after exec"
```

Then check from any shell:

```bash
lion watch status --json
```

PASS: two segments with the same PID, both alive while the shell runs. Close the
window (or run `exit`) and check again:

```bash
lion watch show latest --json
lion watch stop
```

PASS: both segments report `alive: false` with reason `gone`; the first segment
has no `exit` event at all (exec skips `zshexit`) and the record is still
readable — a missing end is reported, never success.

### H5 — Loud failures and removal while active

```bash
lion watch start --note "manual H5"
lion watch start
```

PASS: the second start exits `1` and names the existing pointer.

```bash
lion shell remove
```

PASS: removal warns on stderr that a recording is active and still succeeds.
Then open a **new** terminal and run:

```bash
echo "fresh shell after remove"
```

PASS: a fresh shell loads no hooks, so that line never appears in the record.
Shells that had already loaded the integration keep recording until the
recording is stopped or they exit (documented behavior).

```bash
lion watch show latest --json
lion watch stop
lion watch stop
```

PASS: `fresh shell after remove` is absent; the first stop succeeds, the second
exits `1` with `No active recording`.

### H6 — Reinstall, doctor and cleanup

```bash
lion shell insert
lion shell status --json
lion watch start --note "manual H6"
lion doctor
lion watch stop
lion shell remove
lion shell status --json
```

PASS: insert re-creates the artifact; `doctor` reports `shell.inserted` and
`watch.active` as `ok` and exits `0` (other topics may warn on your machine);
after `remove`, `inserted` is `false` and doctor reports an integration skip.
Finally remove the test recordings by hand (there is no delete command in E2):

```bash
lion watch list
```

Then delete only the `manual H1…H6` references you just created:

```bash
ls ~/.local/share/lion/recordings
rm -rf ~/.local/share/lion/recordings/<ref-of-a-manual-test>
```

Do not blanket-delete the directory if it contains real recordings. If you used
an isolated `XDG_DATA_HOME`, remove the whole directory instead.

### Reporting back

Send the complete terminal transcript (or the relevant excerpts) with:

- the versions and option values from "Before you start";
- the PASS/FAIL verdict per scenario and every deviation with its exact output;
- for H1, the exact text printed before the first prompt, if any;
- `lion watch list --json` and `lion watch show latest --json` for the test
  recordings, if they still exist.

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
  environment) fixtures; no extra imports are needed. The shared
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
