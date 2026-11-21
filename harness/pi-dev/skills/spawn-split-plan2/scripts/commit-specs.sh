#!/usr/bin/env bash
# cspell:ignore toplevel
# Commit only the run directory to the specs repository on behalf of the orchestrator.
set -euo pipefail

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

if [[ "$#" -ne 1 ]]; then
  echo 'usage: commit-specs.sh <run-dir>' >&2
  exit 2
fi
specs="${SPECS_REPO:-/workspace/vscode/raven/man4llm}"
run="${1%/}"
name="${ORCH_NAME:-Moderator}"
email="${ORCH_EMAIL:-moderator@models.dev}"
if [[ -z "${name}" || "${name}" == *$'\n'* ]]; then
  fail 'bad author name' 2
fi
if [[ ! "${email}" =~ ^[^[:space:]\<\>@]+@[^[:space:]\<\>@]+$ ]]; then
  fail "bad email: ${email}" 2
fi
top="$(git -C "${specs}" rev-parse --show-toplevel)" ||
  fail "not a specs repository: ${specs}" 2
if [[ "${top}" != "$(cd "${specs}" && pwd -P)" ]]; then
  fail "not a specs repository root: ${specs}" 2
fi
if [[ "${run}" != "${specs}"/* || ! -d "${run}" || -L "${run}" ]]; then
  fail "not a run directory in ${specs}: ${run}" 2
fi
rel="${run#"${specs}/"}"
if [[ ! "${rel}" =~ ^([^/]+/){2}([^/]+/)?[0-9]{8}-[0-9]{2}-[a-z0-9-]+$ ]]; then
  fail "not a run directory path: ${rel}" 2
fi
git -C "${specs}" add -- "${rel}"
if git -C "${specs}" diff --cached --quiet -- "${rel}"; then
  echo 'nothing to commit' >&2
  exit 0
fi
kind='add'
if [[ -n "$(git -C "${specs}" log -1 --format=%H -- "${rel}")" ]]; then
  kind='update'
fi
GIT_AUTHOR_NAME="${name}" GIT_AUTHOR_EMAIL="${email}" \
  GIT_COMMITTER_NAME="${name}" GIT_COMMITTER_EMAIL="${email}" \
  git -C "${specs}" commit --quiet --no-gpg-sign -m "${kind} ${rel} spec" -- "${rel}"
git -C "${specs}" rev-parse HEAD
