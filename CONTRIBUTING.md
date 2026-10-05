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
