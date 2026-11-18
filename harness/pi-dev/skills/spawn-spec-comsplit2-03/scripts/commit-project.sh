#!/usr/bin/env bash
# cspell:ignore toplevel
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
git -C "${tree}" commit --quiet --no-gpg-sign -m "${message}" -- "$@"
git -C "${tree}" rev-parse HEAD
