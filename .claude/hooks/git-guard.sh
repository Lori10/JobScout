#!/usr/bin/env bash
# PreToolUse guard for Bash: enforces JobScout's git workflow as a hard
# safety net alongside the same rules documented in CLAUDE.md.
#   1. No `git commit` while on main/master.
#   2. No "Co-Authored-By: Claude" trailer in a commit.
#   3. No --force/--force-with-lease push to main/master.
set -euo pipefail

input="$(cat)"
command="$(printf '%s' "$input" | jq -r '.tool_input.command // empty')"

[ -z "$command" ] && exit 0
printf '%s' "$command" | grep -qiP '\bgit\b' || exit 0

deny() {
  echo "git-guard: $1" >&2
  exit 2
}

current_branch() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null || printf ''
}

is_main_or_master() {
  case "$1" in
    main|master) return 0 ;;
    *) return 1 ;;
  esac
}

# --- Rule 1 & 2: git commit ---
if printf '%s' "$command" | grep -qP '(^|[;&|]|\s)git(\s+-\S+)*\s+commit\b(?!-)'; then
  branch="$(current_branch)"
  if is_main_or_master "$branch"; then
    deny "direct commits to '$branch' are not allowed. Create a feature/, fix/, or hotfix/ branch first, then merge into $branch with 'git merge --ff-only' once the work is done."
  fi
  if printf '%s' "$command" | grep -qiP 'co-authored-by' && printf '%s' "$command" | grep -qi 'claude'; then
    deny "commit message includes a Co-Authored-By: Claude trailer, which this project never wants. Redo the commit without that trailer."
  fi
fi

# --- Rule 3: forced push to main/master ---
if printf '%s' "$command" | grep -qP '(^|[;&|]|\s)git(\s+-\S+)*\s+push\b'; then
  if printf '%s' "$command" | grep -qP -- '(--force(-with-lease)?\b|(^|\s)-f(\s|$))'; then
    if printf '%s' "$command" | grep -qP '\b(main|master)\b'; then
      deny "force-push targeting main/master is blocked. If main has diverged, resolve it with a normal (non-force) merge instead of overwriting shared history."
    fi
    # No branch named explicitly (0-1 non-flag tokens after 'push') means
    # git falls back to the current branch, so that's what matters here.
    rest="$(printf '%s' "$command" | sed -E 's/^.*\bpush\b//')"
    nonflag="$(printf '%s\n' $rest | awk '!/^-/ && NF' | wc -l)"
    if [ "$nonflag" -le 1 ]; then
      branch="$(current_branch)"
      if is_main_or_master "$branch"; then
        deny "force-push while HEAD is on '$branch' is blocked. If main has diverged, resolve it with a normal (non-force) merge instead of overwriting shared history."
      fi
    fi
  fi
fi

exit 0
