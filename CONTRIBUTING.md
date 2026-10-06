# Contributing to repolion

## Development Setup

```bash
git clone <repo-url>
cd repolion
direnv allow      # auto-activates .venv (or run: source .venv/bin/activate)
uv sync           # install all dependencies
```

## Workflow

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make your changes
3. Run the quality gate: `just check`
4. Commit: `git add -A && git commit -m "feat: ..."`
5. Push and open a pull request

## Quality Gate

```bash
just lint         # ruff + basedpyright
just typecheck    # type checking only
just test         # pytest with coverage
just check        # full gate (lint + typecheck + tests)
just fix          # auto-fix formatting and lint issues
```

## Architecture

- `src/repolion/state/collector.py` — shared `CollectorStatus`, `CollectorResult`,
  `Collector`, and `collect_state`; keep it free of collector imports.
- `src/repolion/state/tools.py` — shared `run_tool` helper for external tools.
- `src/repolion/state/<collector>.py` — one collector per file (`host`,
  `hardware`, `packages`), each with its frozen dataclass, a private `_collect()`,
  and a `COLLECTOR` export.
- `src/repolion/state/registry.py` — ordered `COLLECTORS` tuple.
- `src/repolion/state/model.py` — `Snapshot`, strict validation, and
  `canonical_collectors` used for state comparison.
- `src/repolion/program/storage.py` — history persistence (`load_latest`, `save_state`).
- `src/repolion/program/diff.py` — `diff_collectors` and terminal `render`.
- `src/repolion/command/<command>.py` — one module per command (`scan` writes,
  `status` reads only); `cli.py` keeps the Typer decorators.

New collectors must not abort a capture: report `unavailable` or `error` with a
message and return their data keys regardless. Never persist volatile fields.

## Testing

External tools (`nvidia-smi`, `apt-mark`, Dpkg) are exercised through fixtures and
mocks so the suite passes without them. Keep `just check` green, which includes
coverage of at least 90%.

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
