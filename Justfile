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

# Fix linting issues
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Remove local caches and generated artifacts (keeps baselines, .venv, sources and the journal)
clean:
    #!/usr/bin/env bash
    set -euo pipefail
    rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov site
    rm -rf build dist ./*.egg-info
    rm -rf e2e-artifacts.* agent-artifacts.*
    if [ -d .refactor ]; then
        find .refactor -mindepth 1 -maxdepth 1 ! -name baselines -exec rm -rf {} +
    fi
    find . -path ./.venv -prune -o -path ./.git -prune -o -type d -name __pycache__ -print -exec rm -rf {} +
    find . -path ./.venv -prune -o -path ./.git -prune -o -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -exec rm -f {} +
    echo "Cleaned caches and generated artifacts, including e2e-artifacts.* and agent-artifacts.* (kept .refactor/baselines, .venv and journal/)."

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


# Full local quality gate (lint + typecheck + docs + tests)
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run basedpyright

    just docs-check

    uv run pytest --cov=src --cov-report=term-missing

# Run the installed command
run:
    uv run lion hello


# Fast-forward target to <branch> (auto-detected when omitted) and clean up safely.
merge branch="" target="main":
    bash scripts/merge.sh "{{branch}}" "{{target}}"

# Regenerate docs/reference/cli.md from the Typer app
docs:
    uv run python scripts/gen_cli_docs.py

# Verify docs/reference/cli.md matches the Typer app (no write; part of `just check`)
docs-check:
    uv run python scripts/gen_cli_docs.py --check

# Open the MCP server in the pinned MCP Inspector on loopback (requires Node.js and npx)
mcp-dev:
    HOST=127.0.0.1 npx @modelcontextprotocol/inspector@2.12.0 uv run lion mcp

# Docker E2E suite
test-e2e:
    bash scripts/test-e2e.sh

# Agent provider suite
test-agent:
    bash scripts/test-agent.sh


# Write or compare design metrics (run | save <name> | compare <name>)
rate-design *args:
    uv run python scripts/design_report.py {{args}}

# Prepare a refactoring session: clean tree, quality gate, baseline, branch. Changes no code.
refactor name="":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "$(git status --porcelain)" ]; then
        echo "Error: working tree is dirty; commit or stash first." >&2
        exit 1
    fi
    just check
    slug="{{name}}"
    slug="${slug:-$(date -u +%Y%m%d-%H%M%S)}"
    slug="$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9._-' '-')"
    slug="${slug#-}"
    slug="${slug%-}"
    if [ -z "$slug" ]; then
        slug="session"
    fi
    branch="refactor/${slug}"
    if git show-ref --verify --quiet "refs/heads/${branch}"; then
        echo "Error: branch '$branch' already exists." >&2
        exit 1
    fi
    just rate-design save "$slug"
    git switch -c "$branch"
    echo "Branch '$branch' created. Baseline in .refactor/baselines/$slug/."
    echo "Proceed with the lupysta refactoring recipe; this recipe does not refactor."
