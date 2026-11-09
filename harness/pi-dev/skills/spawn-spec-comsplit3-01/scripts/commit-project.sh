#!/usr/bin/env bash
# Make the single final project commit in a run worktree on behalf of the orchestrator.
set -euo pipefail

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

if [[ "$#" -lt 2 ]]; then
  echo 'usage: commit-project.sh <work-tree> <message> [<path>...]' >&2
  exit 2
fi
tree="$1"
message="$2"
shift 2
name="${ORCH_NAME:-Moderator}"
email="${ORCH_EMAIL:-moderator@models.dev}"
if [[ -z "${name}" || "${name}" == *$'\n'* ]]; then
  fail 'bad author name' 2
fi
if [[ ! "${email}" =~ ^[^[:space:]\<\>@]+@[^[:space:]\<\>@]+$ ]]; then
  fail "bad email: ${email}" 2
fi
if [[ -z "${message//[[:space:]]/}" || "${message}" == *$'\n'* ||
  "${message}" == *$'\r'* ]]; then
  fail 'message must be a single non-empty line' 2
fi
if [[ "${tree}" != /* || ! -d "${tree}" ]]; then
  fail "not an absolute directory: ${tree}" 2
fi
top="$(git -C "${tree}" rev-parse --show-toplevel)" ||
  fail "not a git work tree: ${tree}" 2
if [[ "${top}" != "$(cd "${tree}" && pwd -P)" ]]; then
  fail "not a work tree root: ${tree}" 2
fi
for path in "$@"; do
  if [[ -z "${path}" || "${path}" == /* ]]; then
    fail "paths must be relative to the work tree: ${path}" 2
  fi
done
if [[ "$#" -gt 0 ]]; then
  git -C "${tree}" add -A -- "$@"
  if git -C "${tree}" diff --cached --quiet -- "$@"; then
    fail 'nothing to commit'
  fi
else
  git -C "${tree}" add -A
  if git -C "${tree}" diff --cached --quiet; then
    fail 'nothing to commit'
  fi
fi
GIT_AUTHOR_NAME="${name}" GIT_AUTHOR_EMAIL="${email}" \
  GIT_COMMITTER_NAME="${name}" GIT_COMMITTER_EMAIL="${email}" \
  git -C "${tree}" commit --quiet --no-gpg-sign -m "${message}" -- "$@"
git -C "${tree}" rev-parse HEAD
