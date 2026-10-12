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

# Remove local caches and generated artifacts (keeps .venv, sources, docs and .refactor/baselines)
clean:
    #!/usr/bin/env bash
    set -euo pipefail
    rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov site
    if [ -d .refactor ]; then
        find .refactor -mindepth 1 -maxdepth 1 ! -name baselines -exec rm -rf {} +
    fi
    rm -rf build dist ./*.egg-info
    rm -rf e2e-artifacts.*
    find . -path ./.venv -prune -o -path ./.git -prune -o -type d -name __pycache__ -print -exec rm -rf {} +
    find . -path ./.venv -prune -o -path ./.git -prune -o -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -exec rm -f {} +
    echo "Cleaned caches and generated artifacts (kept .venv, sources, .refactor/baselines, SESSION.md and JOURNAL.md)."

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

# Run the E2E suite and keep the container so a live tmux session can be watched
test-e2e-attach:
    bash scripts/test-e2e.sh --attach

# Design report to .refactor/ (run | save <name> | compare <name>; advisory, never fails on findings)
rate-design *args:
    uv run python scripts/design_report.py {{args}}

# Prepare a refactoring session: preflight, baseline report, new branch. Changes no code.
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
    branch="refactor/${slug}"
    if git show-ref --verify --quiet "refs/heads/$branch"; then
        echo "Error: branch '$branch' already exists." >&2
        exit 1
    fi
    just rate-design save "$slug"
    git switch -c "$branch"
    echo "Branch '$branch' created. Baseline '$slug' saved under .refactor/baselines/ (survives 'just clean')."
    echo "After edits: just rate-design compare $slug"
    echo "Proceed with the 'refactoring' skill; this recipe does not refactor."

# Fast-forward main to <branch> (auto-detected when omitted), then clean up its worktree and branch
merge branch="" target="main":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "$(git branch --show-current)" != "{{target}}" ]; then
        echo "merge: run this from '{{target}}'" >&2
        exit 1
    fi
    if [ -n "$(git status --porcelain)" ]; then
        echo "merge: working tree is dirty" >&2
        exit 1
    fi

    branch="{{branch}}"
    if [ -z "$branch" ]; then
        # The feature branch is the one checked out in another worktree.
        mapfile -t candidates < <(
            git worktree list --porcelain \
                | awk '/^branch / { print substr($2, 12) }' \
                | grep -vx '{{target}}' || true
        )
        # No worktree: fall back to the only local branch not yet merged.
        if [ "${#candidates[@]}" -eq 0 ]; then
            mapfile -t candidates < <(
                git branch --no-merged '{{target}}' --format='%(refname:short)' || true
            )
        fi
        if [ "${#candidates[@]}" -eq 0 ]; then
            echo "merge: nothing to merge" >&2
            exit 1
        fi
        if [ "${#candidates[@]}" -gt 1 ]; then
            echo "merge: several branches to choose from:" >&2
            printf '  %s\n' "${candidates[@]}" >&2
            echo "merge: run 'just merge <branch>'" >&2
            exit 1
        fi
        branch="${candidates[0]}"
    fi
    if [ "$branch" = "{{target}}" ]; then
        echo "merge: refusing to merge '{{target}}' into itself" >&2
        exit 1
    fi

    if git remote get-url origin >/dev/null 2>&1; then
        git fetch origin
        # Refuse now when publishing would later be rejected as non-fast-forward.
        if git rev-parse --verify --quiet "refs/remotes/origin/{{target}}" >/dev/null; then
            if ! git merge-base --is-ancestor "refs/remotes/origin/{{target}}" HEAD; then
                echo "merge: origin/{{target}} is not an ancestor of {{target}}; reconcile before merging" >&2
                exit 1
            fi
        fi
    fi
    if ! git merge --ff-only "$branch"; then
        echo "merge: '$branch' cannot be fast-forwarded; '{{target}}' has diverged" >&2
        exit 1
    fi
    worktree_path="$(
        git worktree list --porcelain | awk -v ref="refs/heads/$branch" \
            '/^worktree / { wt = substr($0, 10) } /^branch / && $2 == ref { print wt; exit }'
    )"
    if [ -n "$worktree_path" ]; then
        git worktree remove "$worktree_path"
    fi
    git branch -d "$branch"
    echo "merge: '$branch' merged into '{{target}}' and cleaned up; publish with 'git push origin {{target}}'"
