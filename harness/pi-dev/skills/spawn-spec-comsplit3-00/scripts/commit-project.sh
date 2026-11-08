#!/usr/bin/env bash
# cspell:ignore toplevel pathspec pathspecs
# Creates the single project commit in the worktree as the orchestrator: without
# paths everything is staged, otherwise only the given paths.
set -euo pipefail
[[ $# -ge 2 ]] || {
  echo "usage: ${0##*/} <wt-dir> <message> [path...]" >&2
  exit 2
}
[[ $1 == /* && -n $2 && $2 != *$'\n'* ]] || {
  echo 'absolute wt-dir and a single-line message are required' >&2
  exit 2
}
wt_dir="$(realpath -e "$1")"
message="$2"
[[ $(git -C "${wt_dir}" rev-parse --show-toplevel) == "${wt_dir}" ]] || {
  echo 'wt-dir must be the repository root' >&2
  exit 2
}
shift 2
name="${ORCH_NAME:-Moderator}"
email="${ORCH_EMAIL:-moderator@models.dev}"
[[ ${email} != *'<'* && ${email} != *'>'* ]] || {
  echo 'ORCH_EMAIL must not contain angle brackets' >&2
  exit 2
}
options=()
if [[ $# -eq 0 ]]; then
  git -C "${wt_dir}" add -A
else
  for path in "$@"; do
    [[ -n ${path} && ${path} != /* && ${path} != :* &&
      /${path}/ != */../* ]] || {
      echo 'approved paths must be relative literal paths inside wt-dir' >&2
      exit 2
    }
  done
  git --literal-pathspecs -C "${wt_dir}" add -A -- "$@"
  options=(--only -- "$@")
fi
GIT_AUTHOR_NAME="${name}" GIT_AUTHOR_EMAIL="${email}" \
  GIT_COMMITTER_NAME="${name}" GIT_COMMITTER_EMAIL="${email}" \
  git --literal-pathspecs -C "${wt_dir}" commit --quiet --no-gpg-sign \
  -m "${message}" "${options[@]}"
git -C "${wt_dir}" log -1 --format='%H %s'
