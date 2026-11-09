#!/usr/bin/env bash
# Creates the single project commit in the worktree as the orchestrator: without
# paths everything is staged, otherwise only the given paths.
set -euo pipefail
[[ $# -ge 2 ]] || {
  echo "usage: ${0##*/} <wt-dir> <message> [path...]" >&2
  exit 2
}
wt_dir="$1"
message="$2"
shift 2
name="${ORCH_NAME:-Moderator}"
email="${ORCH_EMAIL:-moderator@models.dev}"
if [[ $# -eq 0 ]]; then
  git -C "${wt_dir}" add -A
else
  git -C "${wt_dir}" add -- "$@"
fi
GIT_AUTHOR_NAME="${name}" GIT_AUTHOR_EMAIL="${email}" \
  GIT_COMMITTER_NAME="${name}" GIT_COMMITTER_EMAIL="${email}" \
  git -C "${wt_dir}" commit --quiet --no-gpg-sign -m "${message}"
git -C "${wt_dir}" log -1 --format='%H %s'
