#!/usr/bin/env bash
# Commits the run directory into the specs repository as the orchestrator.
set -euo pipefail
[[ $# -eq 2 ]] || {
  echo "usage: ${0##*/} <specs-repo> <run-dir>" >&2
  exit 2
}
repo="$1"
run_dir="$2"
name="${ORCH_NAME:-Moderator}"
email="${ORCH_EMAIL:-moderator@models.dev}"
run_rel="$(realpath --relative-to="${repo}" "${run_dir}")"
git -C "${repo}" add -- "${run_dir}"
if [[ -z "$(git -C "${repo}" status --porcelain -- "${run_dir}")" ]]; then
  echo 'nothing to commit'
  exit 0
fi
if [[ -z "$(git -C "${repo}" log -1 --format=%h -- "${run_dir}" 2>/dev/null)" ]]; then
  verb='add'
else
  verb='update'
fi
GIT_AUTHOR_NAME="${name}" GIT_AUTHOR_EMAIL="${email}" \
  GIT_COMMITTER_NAME="${name}" GIT_COMMITTER_EMAIL="${email}" \
  git -C "${repo}" commit --quiet --no-gpg-sign -m "${verb} ${run_rel} spec" \
  -- "${run_dir}"
git -C "${repo}" log -1 --format='%h %s'
