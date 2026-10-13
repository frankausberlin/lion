# AGENTS.md - lion

Generated once by `pyinit` from `stack-contract-v1`. Existing projects are not updated automatically; keep local guidance in the project-specific section.

## Project Profile

- Project name: `lion`
- Repository name: `lion`
- Package name: `lion`
- Project type: app
- Python version: `3.12`
- Source path: `src/lion/`
- Tests: `tests/`
- Entry point: `src/lion/main.py:main` (run with `just run`).

<!-- AGENTS_MANAGED_BEGIN: stack-contract-v1 -->
## Project Agent Contract

### Quality Commands

```bash
just check       # full gate: lint, formatting, type checking, and tests
just fix         # auto-fix lint and formatting
just test        # pytest with coverage
just lint        # ruff and basedpyright
just typecheck   # basedpyright only
just audit       # dependency vulnerability scan
```

### Project Layout

- `src/<package>/` contains the package source.
- `tests/` contains the pytest suite and coverage checks.
- Applications expose their command through `src/<package>/main.py`.
- Libraries expose their public API through the package `__init__.py` and keep `py.typed`.

### Code Style

- Use the project's configured Python version and strict basedpyright checking.
- Add Google-style docstrings to public functions and classes.
- Keep lines at or below 120 characters.
- Let ruff sort imports and enforce the configured lint rules.
- Comments explain non-obvious decisions, not obvious code.

### Quality Rules

- Keep `just check` green; do not weaken tests, lint rules, or type checking to hide failures.
- Keep code free of dead code and unused imports.
- Chain exceptions with `raise ... from exc` when preserving the cause.
- Put tests in `tests/` and maintain meaningful coverage.

### Session Workflow

1. Read `AGENTS.md`, then read `SESSION.md` if it exists.
2. Run `uv sync` and `git status` before making changes.
3. Work in small, tested changes and run `just check` before finishing.
4. Overwrite `SESSION.md` with the current state summary.
5. Append a dated entry to `JOURNAL.md`; never overwrite its history.

### Debugging Protocol

1. Isolate or write a failing test first: `uv run pytest -k <name> -s`.
2. Read the complete traceback and identify whether the failure originates in `src/` or a dependency.
3. Use logging for complex state and `breakpoint()` for local inspection.

### Troubleshooting

| Symptom | Solution |
|---------|----------|
| `ModuleNotFoundError` | Run `uv sync` or use `uv run`. |
| `just check` fails | Run `just fix`, then resolve remaining failures manually. |
| `bump-my-version` fails | Ensure the working tree is clean. |
<!-- AGENTS_MANAGED_END: stack-contract-v1 -->

## Project-Specific Rules

<!-- Add project-local rules below. Keep the managed contract unchanged. -->

### LION Architecture

- `src/lion/state/` holds the collector framework: `collector.py` defines the
  shared `CollectorStatus`, `CollectorResult`, `Collector`, and `collect_state`
  (no collector imports, to avoid cycles); `tools.py` provides the shared
  external-tool runner `run_tool`; `tooling.py` is the separate `tools`
  collector of tool availability; each collector (`host.py`, `hardware.py`,
  `network.py`, `packages.py`, `services.py`, `containers.py`, `tooling.py`,
  `dash.py`)
  keeps its frozen dataclass next to a private
  `_collect()` and exports `COLLECTOR`; `registry.py` exposes the ordered
  `COLLECTORS` (`host`, `hardware`, `network`, `packages`, `services`,
  `containers`, `tools`, `dash`); `model.py`
  defines the persisted `Snapshot` and its strict validation, the exact
  `canonical_collectors`, and the shared `collectors_equal`/`value_equal`
  comparison rules (including the RAM tolerance).
- `program/storage.py` owns the state history under `$XDG_DATA_HOME/lion/history`
  (`~/.local/share/lion/history`): `list_entries`/`resolve`/`load_latest`,
  `save_state` (`created`/`confirmed`/`appended`), `Entry`, `SaveOutcome` and
  the public `load_head_for_write` (used by `save_state` and as the scan
  baseline; no behavior change from its former private form).
- `program/wiki.py` owns the derived OKF v0.2 wiki under
  `$XDG_DATA_HOME/lion/wiki`: `get_wiki_dir()`, the pure
  `render_pages(snapshot, ref)` (one page per registry collector plus
  `index.md`, fixed frontmatter key order, identity palette
  `PALETTE`/`IDENTITY_COLORS`), the atomic writer `write_pages(pages)` (temp
  file + `fsync` + `os.replace` per page, collector pages first, `index.md`
  last, no cleanup in v1) and the doctor check `wiki_checks`
  (`wiki.freshness`). `command/overview.py` renders the shared TTY-aware
  index (`render_index(state, diff, wiki_dir)`) for `scan`/`status` with the
  testable `_is_tty` indirection, the four semantic ANSI colors and OSC-8
  hyperlinks; non-TTY/`NO_COLOR` output carries no escapes, and the `--json`
  contracts never use it. `scan` is the only wiki writer (collect → save →
  render → write); `status` and `doctor` only read it.
- `program/diff.py` provides `diff_collectors`, `has_structural_change` and
  `render` (known lists share identities from `state/comparison.py` — PCI
  slots for GPUs, `name` for interfaces/units/containers/volumes/networks, the
  computed image reference, and unique strings for package selections — and
  `added`/`removed` mark structural changes); `cli.py` keeps only the
  Typer decorators and delegates to `src/lion/command/<command>.py`, one
  module per command with a `run` function (`scan.py` writes the history and
  regenerates the wiki;
  `status.py`, `history.py`, `diff.py` and `classify.py` read only; `doctor.py`
  only publishes the reco; `shlib.py` manages `~/.shlib` and takes `--json` for
  `status` only), plus the shared `diagnostics.py` stderr-warning helper. The English CLI
  help texts (epilogs and sub-app help) live in `main.py` and are imported by
  `cli.py`. `shlib` is a Typer
  sub-app with `status`/`install`/`uninstall`. A command group with a `status`
  subcommand (`lion`, `lion shlib`) runs it when invoked without arguments;
  groups without one show their help. Naming: `status` is the
  command, `state` is the internal representation (collected mapping / persisted
  `Snapshot`). The former `scan.py` and `scans/` directory are gone.
- `program/shlib.py` owns the shlib business logic (install, uninstall, status,
  backups, atomic file writes, zsh syntax validation) and a `mutation_lock`
  process lock (`.shlib.lock`) shared by install/uninstall; it must not import
  Typer. `command/shlib.py` only calls it, prints the messages/status and maps
  errors to CLI failures. `lion --version` prints the single `__version__` from
  `lion/__init__.py` eagerly and without collecting; `lion history --limit N`
  limits the already-validated listing to the newest N entries without
  renumbering them.
- `program/zshsyntax.py` provides the shared `validate_zsh` (`zsh -n`, never
  executes); `program/shlib.py` delegates to it, and `program/shell.py` uses it
  for the watch artifact. `program/shell.py` owns the watch shell integration:
  `insert`/`remove`/`status` plus the delivered zsh script. It inserts exactly
  one artifact — `~/.shlib/shlibs/90-lion-watch.sh` when shlib is installed,
  otherwise a marked `# >>>>>> LION watch` block in `~/.zshrc` — never
  overwrites (timestamped backup, `zsh -n` check, marker line required by
  `remove`), and `status` is read-only (mode, path, `inserted`, `modified`,
  warnings). `command/shell.py` and `command/watch.py` follow the command
  pattern; `shell` and `watch` are Typer sub-apps whose bare invocation runs
  their `status`. `command/watch.py` collects at most once per operation
  (`status`, `start`, `stop`); `list`/`show` never collect.
- `program/recording.py` owns the watch recordings under
  `$XDG_DATA_HOME/lion/recordings`: `start_recording`/`stop_recording` are the
  only writers (clock guard, never overwriting, `active` pointer via `os.link`),
  everything else is read-only (`active_recording`, `list_recordings`,
  `resolve_recording`, `load_recording`, `read_events`, `load_events`,
  `unquote_zsh`). `meta.toml` (schema version 1) plus `state-start.toml` /
  `state-end.toml` reuse the `Snapshot` model; events live in per-shell segment
  files. Segment liveness is derived at read time from the kernel `boot_id`
  plus the PID and start ticks; a missing `exit` event is never success
  (`reboot`/`gone`/`reused`/`unknown` end reasons). Comment lines are input,
  not noise: with `interactive_comments` set (the Deen Lupysta precondition) a
  standalone comment becomes one `comment` event and a mixed pasted block stays
  one `cmd`; `zshaddhistory` records only comment-only input, so nothing is
  captured twice.
- `program/annotations.py` owns the ADR layer over recordings: the flat
  `#-LION:`/`#+LION:`/`#.LION:` grammar, the bounded zsh mini-parser, the fold
  over `recording.load_events` (last-wins; at most one ADR per record and the
  record ref is the ADR id; `title` + `decision` ⇒ `complete`) and the
  `uses`/`supersedes` resolution at read time. `command/records.py` implements
  `lion records` (`status`/`list`/`adrs`; `records` is a Typer sub-app whose
  bare invocation runs `status`); the read commands never collect and never
  write, and the `scan`/`status`/`watch` JSON contracts stay unchanged.
  `watch list`/`watch show` remain untouched.
- `src/lion/classify/` is the read-time classifier (E4): `parser.py` is the
  bounded shell parser (`parse_command`, `strip_prefixes`, stable reason codes,
  `PARSER_VERSION = "classify/1"`); `rules.py` loads, validates, merges and
  hashes the declarative JSON rules (bundled `default_rules.json` plus the
  optional user file `$XDG_CONFIG_HOME/lion/classifier.json`, override by `id`)
  and validates the POSIX-ERE subset, translating `[[:…:]]` classes for
  Python `re`; `model.py` holds `Match`/`Block`/`Classification` with their
  JSON shapes; `engine.py` builds the blocks (non-empty, non-silent `cmd`
  events only), matches segment scope anchored after the prefix strip and
  block scope on masked lines, deduplicates by `(group, rule_id, fields)` and
  orders deterministically; `checks.py` owns the doctor check
  `classify.ruleset`. `command/classify.py` implements `lion classify` and
  shares `preview`/`matches_text` with the terse `watch R`/`R-` reads in
  `command/watch.py` (both text-only, read-only, never collect).
- `doctor` is a read-only check framework: `program/checks.py` holds the shared
  `CheckStatus`/`Finding`/`DoctorContext`/`Check` types (a leaf module, so the
  owning check modules never import the aggregator and create a cycle); the
  checks are owned by `state/diagnosis.py` (`collector_checks`, `tool_checks`),
  `program/storage.py` (`history_checks`, `storage_checks`),
  `program/wiki.py` (`wiki_checks`),
  `program/shlib.py` (`shlib_checks`), `program/shell.py` (`shell_checks`),
  `program/recording.py` (`watch_checks`), `program/annotations.py`
  (`annotations_checks`) and `classify/checks.py` (`classifier_checks`);
  `program/doctor.py` only runs, orders and renders them (`TOPIC_ORDER`
  includes `wiki`, `shell`, `watch` and `classify`; `CHECKS` is an ordered tuple
  of `(topic, check)` pairs, so the `watch` topic carries two);
  `command/doctor.py` resolves the home, collects once and publishes at most one
  `reco.sh` under `$XDG_DATA_HOME/lion/recos` when any finding is
  `warn`/`error`. `doctor` writes nothing when everything is `ok`/`skip`, and
  LION never executes a reco (see ADR 0007).
- Collector contract: never let one collector abort the capture, use `status`
  `unavailable`/`error` plus an `error` message instead, and never store
  volatile fields (clocks, temperatures, uptime).

### LION Invariants

- `scan` writes; `status`, `history` and `diff` never write. `status` compares
  the current collection with the latest stored state; `diff` compares two
  stored states. `history` and `diff` identify a state by its compact reference
  (the file name without `.toml`); `resolve` accepts aliases, 1-based indices,
  exact references, unique prefixes and ISO timestamps, validates every entry,
  and fails loudly on ambiguous or unknown references.
- The wiki is derived, never a source of truth: `scan` regenerates it on every
  run (including `confirmed`) from the just-stored snapshot; `status`, `doctor`
  and every other command only read it. Rendering is a pure function of the
  snapshot and the reference, so the same snapshot yields byte-identical pages
  (no generation timestamp); `write_pages` replaces each page atomically (temp
  file + `fsync` + `os.replace`, collector pages first, `index.md` last) and
  removes nothing in v1. A render or write failure exits `1` after the history
  save: the entry stays valid and the next `scan` heals. The text index colors
  only in a TTY without a non-empty `NO_COLOR` (error red, unavailable yellow,
  ok+changed/new blue, ok+unchanged green, OSC-8 link to the page file); the
  `scan --json`/`status --json` contracts stay unchanged. `doctor`'s
  `wiki.freshness` compares the newest history reference with the index
  frontmatter (`skip` without or with unreadable history, `warn` when the wiki
  is missing, unreadable or stale) and never writes; warnings carry the
  `'lion scan' regenerates it.` hint.
- `doctor` is read-only except for at most one recommended script under
  `$XDG_DATA_HOME/lion/recos` (published with `os.link`, never overwritten,
  mode `700`, created only when a finding is `warn`/`error`); it never executes
  the script and never stores a snapshot. Findings use `ok`/`warn`/`error`/`skip`
  (`skip` neutral); the exit code is `1` only on `error`.
- Watch recordings never mix with the scan history: `watch status/list/show` are
  read-only and never collect; `watch start`/`stop` collect exactly once and
  refuse to mutate before the newest known recording timestamp (clock guard) or
  over an existing `active` pointer/`state-end.toml`. Records, states and the
  pointer are published via `os.link` (`~NNNN` collision suffix); `stop`
  publishes `state-end.toml`, then updates `meta.toml` in place (`os.replace`),
  then removes the pointer. Segment liveness derives from `boot_id` + PID +
  start ticks; a missing `exit` is never success. Damaged records fail loudly
  with file and line; a damaged space warns (stderr) in `lion status` text mode
  without breaking the JSON contract, which stays byte-stable.
- The watch shell integration: at most one artifact, never overwritten
  (timestamped backup + `zsh -n` before writing `~/.zshrc`), `remove` requires
  the marker line, `status` is read-only, and the zsh side loads silently
  (instant-prompt safe) with no process and no scan in the input path. Masking
  is best-effort (`LION_WATCH_MASK=0` opts out) and documented as such; the
  dog guard is not part of E2.
- ADRs live inside their recording and are derived from the raw events on every
  read (no new event writer): marker lines count only as full comment lines
  outside strings and here-documents; unreadable annotation input is a visible
  `Unresolved:` finding, never a guess; a record without a field has no ADR;
  unknown `uses`/`supersedes` refs display `(unknown)` and are never errors.
  `records status/list/adrs` are read-only and never collect; a damaged record
  fails loudly with its path. `records edit` is the only ADR writer: it writes
  exactly one overlay file `<record>/adr.toml` (atomic `os.replace`, mode
  `600`) for stopped records and never touches the raw events; overlay fields
  replace the event-derived field set and set the `edited` flag.
- Classifications are derived from the raw recording events on every read (no
  writer, no new event type, nothing persisted); `classify` and the terse
  `watch R`/`R-` never collect and never write. Blocks are the non-empty,
  non-silent `cmd` events; unsupported syntax yields no matches (never a
  guess); segment scope anchors after the `sudo`/`NAME=value` strip, block
  scope searches masked lines (a removed quote becomes a separator that can
  neither join words nor fake a segment start), matches deduplicate by
  `(group, rule_id, fields)` and order deterministically, and terminal output
  strips control characters from recorded text. Rules are data in a closed
  group vocabulary with a POSIX-ERE subset (ADR 0015); the merged rule list
  carries a deterministic hash, invalid files fail loudly with their path,
  the doctor topic `classify` reports `classify.ruleset`, and the
  `scan`/`status`/`watch` JSON contracts stay unchanged.
- `collectors_equal` and displayed diffs share list rules in `state/comparison.py`:
  `STRING_LISTS` (package selections) and an `IDENTITY_KEYS` map (`hardware.gpu`
  by `pci_id`; `network.interfaces`, `services.units`, `containers.containers`,
  `containers.volumes`, `containers.networks`, `dash.files` by `name`) plus the
  computed `containers.images` reference ignore order when every identity is
  unique and nonempty. Other lists, duplicates and malformed identities remain
  atomic. Only `hardware.memory_total_bytes` has the 1 MiB tolerance; timestamps
  are excluded, statuses/errors included, and scalar types compare exactly.
- `dash` monitors only `LIONSTATE_`-prefixed symlinks in `~/.shlib/dash` that
  resolve to a regular file, storing the SHA-256 content hash only, never the
  file content, target, size or mtime; the list identity is `name`. A missing
  directory is `ok` with an empty list; a non-directory or unlistable path is
  `unavailable`, and an opted-in file that cannot be read is `unavailable` with
  `Unreadable dash entry: <name>` while the other hashes are kept. Unprefixed
  links, regular files and directory/broken links stay unmonitored (doctor's
  shlib dash checks report those defects).
- GPU vendor identity comes from PCI metadata independently of the active driver.
  `compute_platform` is a driver-derived hint, not verified compute support or
  runtime installation. The `tools` collector records PATH visibility only; the
  `containers` collector records engine usability separately (`runtime`
  `docker`/`podman`/`none`, a present-but-broken engine is `unavailable`).
  `network` records physical sysfs interfaces only (no virtual interfaces, no
  IPs/link state, permanent MACs only); `services` records unit-file states as
  systemd reports them and skips `transient` runtime-API units (no unit file).
  PCI ids identify slots: a replacement in the same slot is a field change.
  Any `added`/`removed` is structural (`structure_changed`).
- New history entries are published with `os.link` and never overwrite; only the
  latest entry's `confirmed_at` refresh uses `os.replace`. Collision
  filenames use a `~NNNN` suffix so they sort after the base name and the
  newest-entry tie-break stays correct.
- `load_latest`/`status` validate every entry and fail loudly with the file path;
  `scan` parses every entry but validates only the newest head, so an unreadable
  or syntactically invalid file still fails. Never skip entries silently.
- `history --limit N` only trims the already-validated list at command level: the
  full history is loaded first, the selection stays oldest-first with global
  indices and stable references, and text and JSON show the same entries. `N`
  must be positive; zero and negatives are CLI usage errors.
- Shlib install and uninstall share an exclusive `.shlib.lock` process lock
  (separate from the `.zshrc.lock` reference copy) that covers prechecks and
  publication, fails a concurrent call with a clear message, is released on
  error or process exit, and is never created by `shlib status`. The retained
  lock file must not count as an installation remnant.
- Shlib reinstall after uninstall stays fail-loud: `install` refuses (exit `1`)
  while artifacts of a previous installation remain and never adopts or removes
  them — the user reconciles first (ADR 0008). The config file, if ever needed,
  lives at `$XDG_CONFIG_HOME/lion/config.toml` (TOML, integer `schema_version`);
  a missing file means built-in defaults, an unreadable or invalid file is a
  loud error; there is no config code in Phase 1 (ADR 0009).
- External tools (`nvidia-smi`, `lspci`, `apt-mark`, `systemctl`, `docker`,
  `podman`, Dpkg) are environment-dependent and
  are exercised through fixtures/mocks in ordinary tests; `network` uses a
  temporary sysfs tree. The opt-in
  `tests/e2e/` suite exercises real Dpkg and apt-mark in a disposable Docker
  container; run it only through `just test-e2e`.

### Testing

- Test observable behavior and the LION invariants above, not implementation
  details. For a bug fix, add a focused regression test that fails before the fix.
- Keep ordinary tests deterministic: use fixtures/mocks for external collector
  tools and temporary paths for history and shell configuration. Never depend
  on the developer's packages, GPUs, home directory or existing snapshots.
- Cover relevant success and failure paths, text/JSON output and exit codes;
  verify that read-only commands leave persisted data unchanged.
- Use `uv run pytest tests/test_<area>.py` for focused feedback and `just check`
  for the full quality gate. Keep the configured coverage minimum of 90%;
  coverage does not replace assertions about behavior.
- Ordinary pytest runs and `just check` exclude the `e2e` marker. When changing
  package detection, the collector set, persisted CLI lifecycles, shlib, the
  watch capture/recordings/shell integration, the wiki renderer or the
  `scan`/`status` text index, the `doctor` checks or the reco
  paths, or the E2E runner, also run `just test-e2e`. The watch E2E scenarios
  drive a real interactive zsh inside tmux in the disposable container
  (`tests/e2e/watchutil.py`) and read results only from spool/record files,
  never from the terminal text. Never run the real package lifecycle on a
  workstation.
- Read [tests/e2e/README.md](tests/e2e/README.md) before running or extending
  the E2E suite. It owns setup, isolation, diagnostics, the alpha-testing
  checklists and scenario guidance; [docs/explanation/testing-strategy.md](docs/explanation/testing-strategy.md)
  explains the test levels and why the container cannot replace a real machine.
- Report the checks actually run and their results; name any unavailable
  prerequisites or skipped checks explicitly.

### Tooling Notes

- `pyproject.toml` sets ruff `extend-exclude = [".kilo"]` so agent artifacts
  (plans, worktrees) are not linted or formatted.

### Refactoring

- `just rate-design` writes the advisory design report to `.refactor/`
  (gitignored): `report.md` plus the versioned `report.json` and the raw
  radon/vulture output, with ranked complexity and function-structure metrics,
  the internal import graph (fan-in/out, eager cycles, TYPE_CHECKING/deferred
  edges) and advisory architecture hints. Findings never fail the run; tool or
  baseline errors exit non-zero.
- Named baselines live in `.refactor/baselines/<name>/` and are never
  overwritten: `just rate-design save <name>` snapshots the current report and
  `just rate-design compare <name>` diffs a fresh run against it by
  repository-relative path plus qualified symbol (line shifts are not symbol
  changes). `just clean` preserves the baselines directory; an interrupted run
  leaves `.refactor/incomplete` behind.
- `just refactor [name]` prepares a session (refuses a dirty tree, runs
  `just check`, saves the baseline named after the slug, creates a
  `refactor/<slug>` branch) and changes no code.
- Follow the `refactoring` skill: measure first, one recipe per step, `just
  check` green before and after every change, no test weakening, no destructive
  git. `radon`/`vulture` are locked dev dependencies; the report script is
  `scripts/design_report.py` with tests in `tests/test_design_report.py`.

### Git Workflow (Agents)

- Agent work happens on a branch (`feat/...`, `fix/...`, `docs/...`), preferably
  in a separate `git worktree` (for example
  `git worktree add ../<name> -b <branch>`); the maintainer's main checkout
  stays on `main`.
- Keep `just check` green, then merge the branch locally from the main checkout
  with `just merge <branch>`: fast-forward only (it refuses when `main` has
  diverged, and when `origin/main` is not an ancestor of local `main`), then it
  removes the branch's worktree and deletes the branch.
- When `main` advanced while the branch was in progress the merge is refused:
  rebase onto `main`, re-run `just check` (required after a rebase), then merge
  again.
- Agents never push: no branch pushes and no pull requests. Publishing
  (`git push`) is reserved for the maintainer, who reviews local `main`
  (`git log --oneline origin/main..main`, `git diff origin/main..main`) and
  pushes. This is an agent workflow rule in this file, not a server-side branch
  rule; `main` is only protected against deletion and non-fast-forward pushes.
- Before finishing git work, inspect `git status`, `git diff` and
  `git log --oneline -10`, and stage only the intended files.

### Language and Project Documents

- Code, docstrings, README, `docs/`, `--help` texts and commit messages stay
  English. This includes JSON/TOML field names, CLI messages and any other
  user-visible strings (ADR 0010). The conversation with the maintainer and the
  two maintainer-local planning documents are German; this is the only
  deliberate exception to the English rule, and German appears in the repo
  only under `ignore/`.
- The German planning documents (`PROJECT_DEFINITION.de.md` for vision, scope,
  terminology and principles; `ROADMAP.de.md` for phases and open decisions)
  live maintainer-local and **untracked** under `ignore/`; they are absent in a
  fresh clone. Read them locally when present, never link to them from tracked
  files, and do not let them drift from the public README and `docs/`.
- Development follows the Luxurious Python Stack and the `luxuspythonstack`
  skill; workflow details stay here, not in the planning documents.

### Documentation (`docs/`)

- `docs/` follows Diátaxis. Place new content by intent: `tutorials/`
  (learning by doing), `how-to/` (one task), `reference/` (facts to look up),
  `explanation/` (concepts and why). Decisions live as ADRs under
  `docs/decisions/`, one decision per file.
- `README.md` is the entry door and `docs/index.md` is the hub; keep both
  links current.
- Command depth stays canonical in `lion <cmd> --help`. `docs/reference/cli.md`
  is generated from the Typer app by `just docs` (`scripts/gen_cli_docs.py`);
  never edit it by hand, and regenerate it after any CLI change. `just check`
  verifies it is current (`scripts/gen_cli_docs.py --check`), so CI fails on a
  stale page. The source of truth is `src/lion/cli.py` together with the help
  texts in `src/lion/main.py`.
- `docs/reference/wiki.md` is the home of the OKF v0.2 wiki format (files,
  frontmatter grammar, palette, determinism). Keep it in sync with
  `program/wiki.py`; the palette tokens and the frontmatter key order are
  public API.
- Every statement has exactly one home: move prose between README and `docs/`
  instead of copying it, and link rather than restate.

### Capture reliability

- External-tool diagnostics retain stable causes without output or arguments.
  Hardware retains available readings but marks incomplete discovery unavailable.
- Scan/status warnings go to stderr, including unchanged incomplete captures;
  preserve JSON stdout contracts.
- Reject scans whose clock precedes the latest confirmation before modifying
  history. Equal timestamps still use collision suffixes.
- Exact comparison includes scalar types inside nested collections; only
  MemTotal has the documented numeric tolerance.
