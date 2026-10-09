set shell := ["bash", "-uc"]

# Run tests with coverage report
test:
    uv run pytest --cov=src --cov-report=term-missing

# Run linters and type checker (find errors)
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run basedpyright

# Type check only
typecheck:
    uv run basedpyright

# Full local quality gate (lint + typecheck + docs + tests)
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run basedpyright
    just docs-check
    uv run pytest --cov=src --cov-report=term-missing

# Fix linting issues
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Regenerate docs/reference/cli.md from the Typer app
docs:
    uv run python scripts/gen_cli_docs.py

# Verify docs/reference/cli.md matches the Typer app (no write)
docs-check:
    uv run python scripts/gen_cli_docs.py --check

# Audit dependencies for known security vulnerabilities
audit:
    uv run pip-audit

# Bump version — requires a clean working tree (patch | minor | major)
bump part="patch":
    #!/usr/bin/env bash
    if [ -n "$(git status --porcelain)" ]; then
        echo "Error: Working tree is dirty." >&2
        exit 1
    fi
    uv run bump-my-version bump {{part}}

# Run the installed command
run:
    uv run lion

# Real CLI/package lifecycle in a disposable Docker container
test-e2e:
    bash scripts/test-e2e.sh
