#!/usr/bin/env bash
# Fast-forward a target branch to a task branch and clean up safely.
#
# Usage: merge.sh [branch] [target]
#
#   branch   Task branch to merge; auto-detected when omitted.
#   target   Branch to fast-forward; defaults to 'main'.
#
# Called by the 'merge' recipe; this script never pushes.
set -euo pipefail
branch="${1:-}"
target="${2:-main}"
if ! git rev-parse --verify --quiet "refs/heads/$target" >/dev/null; then
    echo "merge: target branch '$target' does not exist" >&2
    exit 1
fi

target_path="$(
    git worktree list --porcelain | awk -v ref="refs/heads/$target" '
        /^worktree / { path = substr($0, 10) }
        /^branch / && $2 == ref { print path; exit }
    '
)"
current_path="$(git rev-parse --show-toplevel)"
current_branch="$(git branch --show-current)"
switched_current=0

if [ -z "$target_path" ]; then
    if [ -n "$(git status --porcelain)" ]; then
        echo "merge: no worktree has '$target' checked out and the current tree is dirty" >&2
        exit 1
    fi
    if [ -z "$branch" ] && [ "$current_branch" != "$target" ]; then
        branch="$current_branch"
    fi
    git switch "$target"
    target_path="$current_path"
    switched_current=1
fi

if [ -z "$branch" ]; then
    candidates="$(
        # 'worktree list --porcelain' prints full refs; the rest of the script
        # (worktree lookup, 'git branch -d') works with short branch names.
        git worktree list --porcelain | awk -v ref="refs/heads/$target" '
            /^branch / {
                b = substr($0, 8)
                sub(/^refs\/heads\//, "", b)
                if ("refs/heads/" b != ref) print b
            }
        '
    )"
    if [ -z "$candidates" ]; then
        candidates="$(git branch --no-merged "$target" --format='%(refname:short)')"
    fi
    count="$(printf '%s\n' "$candidates" | awk 'NF { n++ } END { print n + 0 }')"
    if [ "$count" -eq 0 ]; then
        echo "merge: nothing to merge" >&2
        exit 1
    fi
    if [ "$count" -gt 1 ]; then
        echo "merge: several branches to choose from:" >&2
        printf '%s\n' "$candidates" | while IFS= read -r candidate; do
            printf '  %s\n' "$candidate" >&2
        done
        echo "merge: run 'just merge <branch>'" >&2
        exit 1
    fi
    branch="$(printf '%s\n' "$candidates" | awk 'NF { print; exit }')"
fi
if [ "$branch" = "$target" ]; then
    echo "merge: refusing to merge '$target' into itself" >&2
    exit 1
fi

if [ -n "$(git -C "$target_path" status --porcelain)" ]; then
    echo "merge: target worktree '$target_path' is dirty" >&2
    exit 1
fi
if git -C "$target_path" remote get-url origin >/dev/null 2>&1; then
    git -C "$target_path" fetch origin
    if git -C "$target_path" rev-parse --verify --quiet "refs/remotes/origin/$target" >/dev/null; then
        if ! git -C "$target_path" merge-base --is-ancestor "refs/remotes/origin/$target" HEAD; then
            echo "merge: origin/$target is not an ancestor of $target; reconcile before merging" >&2
            exit 1
        fi
    fi
fi
if ! git -C "$target_path" merge --ff-only "$branch"; then
    echo "merge: '$branch' cannot be fast-forwarded; '$target' has diverged" >&2
    exit 1
fi

source_path="$(
    git worktree list --porcelain | awk -v ref="refs/heads/$branch" '
        /^worktree / { path = substr($0, 10) }
        /^branch / && $2 == ref { print path; exit }
    '
)"
if [ -n "$source_path" ] && [ "$source_path" != "$target_path" ]; then
    if [ "$source_path" = "$current_path" ]; then
        echo "merge: '$branch' merged into '$target'."
        echo "merge: source worktree is the current checkout; switch to '$target'"
        echo "       and remove the worktree and branch manually if you no longer need them."
    elif [ -n "$(git -C "$source_path" status --porcelain)" ]; then
        echo "merge: '$branch' merged into '$target'."
        echo "merge: worktree '$source_path' left in place because it is dirty."
    elif git worktree remove "$source_path"; then
        git branch -d "$branch"
        echo "merge: '$branch' merged into '$target'; worktree removed and branch deleted."
    else
        echo "merge: '$branch' merged into '$target'."
        echo "merge: worktree '$source_path' could not be removed; clean it up manually."
    fi
else
    if [ "$source_path" = "$target_path" ] && [ "$switched_current" -eq 0 ]; then
        echo "merge: '$branch' merged into '$target'; source worktree is the target, leaving it in place."
    else
        git branch -d "$branch"
        echo "merge: '$branch' merged into '$target'; branch deleted."
    fi
fi
echo "merge: publish with 'git push origin $target'"
