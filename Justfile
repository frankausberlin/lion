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

# Remove local caches and generated artifacts (keeps .venv, sources and docs)
clean:
    #!/usr/bin/env bash
    set -euo pipefail
    rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov .refactor site
    rm -rf build dist ./*.egg-info
    rm -rf e2e-artifacts.*
    find . -path ./.venv -prune -o -path ./.git -prune -o -type d -name __pycache__ -print -exec rm -rf {} +
    find . -path ./.venv -prune -o -path ./.git -prune -o -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -exec rm -f {} +
    echo "Cleaned caches and generated artifacts (kept .venv, SESSION.md and JOURNAL.md)."

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

# Write design metrics to .refactor/ (report only; never fails on findings)
rate-design:
    #!/usr/bin/env bash
    set -uo pipefail
    mkdir -p .refactor
    uv run radon cc  src --json > .refactor/radon-cc.json  || { echo "radon cc failed"  >&2; exit 2; }
    uv run radon mi  src --json > .refactor/radon-mi.json  || { echo "radon mi failed"  >&2; exit 2; }
    uv run radon raw src --json > .refactor/radon-raw.json || { echo "radon raw failed" >&2; exit 2; }
    vrc=0
    uv run vulture src tests --min-confidence 60 > .refactor/vulture.txt || vrc=$?
    # vulture exit code 3 == findings (expected); 1/2 == error
    if [ "$vrc" -ne 0 ] && [ "$vrc" -ne 3 ]; then echo "vulture failed ($vrc)" >&2; exit 2; fi
    {
        echo "# Design report"
        echo
        echo "- revision: $(git rev-parse --short HEAD 2>/dev/null || echo n/a)"
        echo "- date: $(date -u +%FT%TZ)"
        echo "- python: $(uv run python -V 2>/dev/null)"
        echo
        echo "## Vulture (advisory – NOT proof of dead code)"
        echo '```'
        cat .refactor/vulture.txt
        echo '```'
        echo
        echo "## Radon cc / mi / raw"
        echo "Raw JSON: .refactor/radon-cc.json, radon-mi.json, radon-raw.json"
    } > .refactor/report.md
    echo "Report: .refactor/report.md"

# Prepare a refactoring session: preflight, baseline report, new branch. Changes no code.
refactor name="":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "$(git status --porcelain)" ]; then
        echo "Error: working tree is dirty; commit or stash first." >&2
        exit 1
    fi
    just check
    just rate-design
    slug="{{name}}"
    slug="${slug:-$(date -u +%Y%m%d-%H%M%S)}"
    branch="refactor/${slug}"
    git switch -c "$branch"
    echo "Branch '$branch' created. Baseline in .refactor/."
    echo "Proceed with the 'refactoring' skill; this recipe does not refactor."
