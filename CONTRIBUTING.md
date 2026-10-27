# Contributing to lion

## Development Setup

```bash
git clone <repo-url>
cd lion
direnv allow      # auto-activates .venv (or run: source .venv/bin/activate)
uv sync           # install all dependencies
```

## Workflow

1. Create a branch, preferably in a separate worktree:
   `git worktree add ../lion-feature -b feat/my-feature`
2. Make your changes
3. Run the quality gate: `just check`
4. Commit: `git add -A && git commit -m "feat: ..."`
5. From any checkout, merge locally: `just merge feat/my-feature`
   (fast-forward only; it removes a clean source worktree and deletes the
   branch)
6. Publishing is a maintainer step: review local `main` with
   `git log --oneline origin/main..main` and `git diff origin/main..main`, then
   `git push origin main`

## Journal

Session notes live under `journal/` (one file per session, committed with the
change). They are a chronicle, not a normative source; durable decisions belong
in `docs/adr/` and attention points in `AGENTS.md`.

## Quality Gate

```bash
just lint         # ruff + basedpyright
just typecheck    # type checking only
just test         # pytest with coverage
just clean        # remove caches and generated artifacts
just rate-design run             # design metrics → .refactor/
just rate-design save <name>     # save an immutable baseline
just rate-design compare <name>  # compare the current run with a baseline
just refactor                    # prepare a refactoring session
just docs         # regenerate docs/reference/cli.md
just docs-check   # verify the generated CLI reference is current
just check        # full gate (lint + typecheck + docs + tests)
just test-e2e     # Docker E2E suite locally (not part of just check)
just test-agent   # provider suite manually (not part of just check)
just fix          # auto-fix formatting and lint issues
```

`just check` runs unit and smoke tests only. The E2E suite is not part of the
gate and runs in CI automatically once tests exist; agent tests are manual-only
and never run in CI. An empty suite prints a hint and exits 0.

## Conventions

- **Python version:** 3.12
- **Typing:** Strict — public functions must have type annotations
- **Docstrings:** Google style
- **Imports:** Sorted by ruff (isort-compatible)
- **Line length:** 120 characters

## Versioning

Never edit version numbers manually. Use:

```bash
just bump patch   # bugfix: 0.1.0 → 0.1.1
just bump minor   # feature: 0.1.1 → 0.2.0
just bump major   # breaking: 0.2.0 → 1.0.0
```

See [AGENTS.md](AGENTS.md) for AI agent guidelines.
