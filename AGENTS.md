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
  `packages.py`, `tooling.py`) keeps its frozen dataclass next to a private
  `_collect()` and exports `COLLECTOR`; `registry.py` exposes the ordered
  `COLLECTORS`; `model.py`
  defines the persisted `Snapshot` and its strict validation, the exact
  `canonical_collectors`, and the shared `collectors_equal`/`value_equal`
  comparison rules (including the RAM tolerance).
- `program/storage.py` owns the state history under `$XDG_DATA_HOME/lion/history`
  (`~/.local/share/lion/history`): `list_entries`/`resolve`/`load_latest`,
  `save_state` (`created`/`confirmed`/`appended`), `Entry`, and `SaveOutcome`.
- `program/diff.py` provides `diff_collectors`, `has_structural_change` and
  `render` (known lists share identities from `state/comparison.py` — PCI
  slots for GPUs and unique strings for package selections — and
  `added`/`removed` mark structural changes); `cli.py` keeps only the
  Typer decorators and delegates to `src/lion/command/<command>.py`, one
  module per command (`scan.py` writes; `status.py`, `history.py` and `diff.py`
  read only) with a `run` function and `--json` support. The English CLI help
  texts (epilogs and sub-app help) live in `main.py` and are imported by
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
- `doctor` is a read-only check framework: `program/checks.py` holds the shared
  `CheckStatus`/`Finding`/`DoctorContext`/`Check` types (a leaf module, so the
  owning check modules never import the aggregator and create a cycle); the
  checks are owned by `state/diagnosis.py` (`collector_checks`, `tool_checks`),
  `program/storage.py` (`history_checks`, `storage_checks`) and
  `program/shlib.py` (`shlib_checks`); `program/doctor.py` only runs, orders and
  renders them; `command/doctor.py` resolves the home, collects once and
  publishes at most one `reco.sh` under `$XDG_DATA_HOME/lion/recos` when any
  finding is `warn`/`error`. `doctor` writes nothing when everything is
  `ok`/`skip`, and LION never executes a reco (see ADR 0007).
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
- `doctor` is read-only except for at most one recommended script under
  `$XDG_DATA_HOME/lion/recos` (published with `os.link`, never overwritten,
  mode `700`, created only when a finding is `warn`/`error`); it never executes
  the script and never stores a snapshot. Findings use `ok`/`warn`/`error`/`skip`
  (`skip` neutral); the exit code is `1` only on `error`.
- `collectors_equal` and displayed diffs share list rules in `state/comparison.py`:
  unique string package selections and GPU lists with unique, nonempty `pci_id`
  ignore order. Other lists, duplicates and malformed identities remain atomic.
  Only `hardware.memory_total_bytes` has the 1 MiB tolerance; timestamps are
  excluded, statuses/errors included, and scalar types compare exactly.
- GPU vendor identity comes from PCI metadata independently of the active driver.
  `compute_platform` is a driver-derived hint, not verified compute support or
  runtime installation. The `tools` collector records PATH visibility only.
  PCI ids identify slots: a replacement in the same slot is a field change.
  Any `added`/`removed` is structural (`struktur_geaendert`).
- New history entries are published with `os.link` and never overwrite; only the
  latest entry's `zuletzt_bestaetigt` refresh uses `os.replace`. Collision
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
- External tools (`nvidia-smi`, `lspci`, `apt-mark`, Dpkg) are environment-dependent and
  are exercised through fixtures/mocks in ordinary tests. The opt-in
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
  package detection, persisted CLI lifecycles, shlib, the `doctor` checks or the
  reco paths, or the E2E runner, also run
  `just test-e2e`. Never run the real package lifecycle on a workstation.
- Read [tests/e2e/README.md](tests/e2e/README.md) before running or extending
  the E2E suite. It owns setup, isolation, diagnostics and scenario guidance.
- Report the checks actually run and their results; name any unavailable
  prerequisites or skipped checks explicitly.

### Tooling Notes

- `pyproject.toml` sets ruff `extend-exclude = [".kilo"]` so agent artifacts
  (plans, worktrees) are not linted or formatted.

### Git Workflow (Agents)

- Agents never commit or push directly to `main`. Create a branch
  (`feat/...`, `fix/...`, `docs/...`), commit and push it, and open a pull
  request; the maintainer reviews and merges.
- Direct pushes to `main` are reserved for the maintainer. This is an agent
  workflow rule in this file, not a server-side branch rule; `main` is only
  protected against deletion and non-fast-forward pushes.
- Before finishing git work, inspect `git status`, `git diff` and
  `git log --oneline -10`, and stage only the intended files.

### Language and Project Documents

- Code, docstrings, README, `docs/`, `--help` texts and commit messages stay
  English. The conversation with the maintainer and the two maintainer-local
  planning documents are German; this is the only deliberate exception to the
  English rule.
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
