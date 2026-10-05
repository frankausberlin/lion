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
