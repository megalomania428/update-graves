#!/usr/bin/env bash
# cspell:ignore toplevel
# Print the tree hash of a work tree state without touching its index or HEAD.
set -euo pipefail

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

if [[ "$#" -ne 1 ]]; then
  echo 'usage: snapshot.sh <work-tree>' >&2
  exit 2
fi
tree="$1"
if [[ "${tree}" != /* || ! -d "${tree}" ]]; then
  fail "not an absolute directory: ${tree}" 2
fi
top="$(git -C "${tree}" rev-parse --show-toplevel)" ||
  fail "not a git work tree: ${tree}" 2
if [[ "${top}" != "$(cd "${tree}" && pwd -P)" ]]; then
  fail "not a work tree root: ${tree}" 2
fi
tmp="$(mktemp -d /tmp/pi-snapshot.XXXXXXXXXX)"
trap 'rm -rf -- "${tmp}"' EXIT
GIT_INDEX_FILE="${tmp}/index" git -C "${tree}" read-tree HEAD
GIT_INDEX_FILE="${tmp}/index" git -C "${tree}" add -A
hash="$(GIT_INDEX_FILE="${tmp}/index" git -C "${tree}" write-tree)"
if [[ ! "${hash}" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  fail "bad tree hash: ${hash}"
fi
rm -rf -- "${tmp}"
trap - EXIT
printf '%s\n' "${hash}"
