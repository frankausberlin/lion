# AGENTS.md - lion

Generated once by `pyinit` from `stack-contract-v1`. Existing projects are not updated automatically; keep local guidance in the project-specific section.

## Project Profile

- Project name: `lion`
- Repository name: `repolion`
- Package name: `repolion`
- Project type: app
- Python version: `3.12`
- Source path: `src/repolion/`
- Tests: `tests/`
- Entry point: `src/repolion/main.py:main` (run with `just run`).

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

- `src/repolion/state/` holds the collector framework: `collector.py` defines the
  shared `CollectorStatus`, `CollectorResult`, `Collector`, and `collect_state`
  (no collector imports, to avoid cycles); `tools.py` provides the shared
  external-tool runner `run_tool`; each collector (`host.py`, `hardware.py`,
  `packages.py`) keeps its frozen dataclass next to a private `_collect()` and
  exports `COLLECTOR`; `registry.py` exposes the ordered `COLLECTORS`; `model.py`
  defines the persisted `Snapshot` and its strict validation, the exact
  `canonical_collectors`, and the shared `collectors_equal`/`value_equal`
  comparison rules (including the RAM tolerance).
- `program/storage.py` owns the state history under `$XDG_DATA_HOME/lion/history`
  (`~/.local/share/lion/history`): `list_entries`/`resolve`/`load_latest`,
  `save_state` (`created`/`confirmed`/`appended`), `Entry`, and `SaveOutcome`.
- `program/diff.py` provides `diff_collectors` and `render`; `cli.py` keeps only the
  Typer decorators and delegates to `src/repolion/command/<command>.py`, one
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
  `repolion/__init__.py` eagerly and without collecting; `lion history --limit N`
  limits the already-validated listing to the newest N entries without
  renumbering them.
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
- Two states are equal when `collectors_equal` holds: either the exact
  `canonical_collectors` match, or the only difference is
  `hardware.memory_total_bytes` within `MEMORY_TOTAL_TOLERANCE_BYTES` (1 MiB).
  Timestamps are excluded; each collector's `status`/`error` is included. Only
  this one field has a tolerance; GPU memory and all other values compare
  exactly, and a valid reading never equals `0`.
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

### Tooling Notes

- `pyproject.toml` sets ruff `extend-exclude = [".kilo"]` so agent artifacts
  (plans, worktrees) are not linted or formatted.
