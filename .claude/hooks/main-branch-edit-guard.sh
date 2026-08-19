#!/usr/bin/env bash
# PreToolUse guard for Edit/Write: no source-code changes directly on
# main/master. Docs (*.md) and config (*.yaml/*.yml, .claude/**) are
# exempt so things like CLAUDE.md and .claude/settings.json can still be
# maintained without a branch.
set -euo pipefail

input="$(cat)"
file_path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')"

[ -z "$file_path" ] && exit 0

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf '')"
case "$branch" in
  main|master) ;;
  *) exit 0 ;;
esac

case "$file_path" in
  *.md|*.MD) exit 0 ;;
  *.yaml|*.yml) exit 0 ;;
  */.claude/*|.claude/*) exit 0 ;;
esac

echo "git-guard: editing '$file_path' directly on '$branch' is not allowed. Create a feature/, fix/, or hotfix/ branch first. (Docs and config — *.md, *.yaml/*.yml, .claude/** — are exempt.)" >&2
exit 2
