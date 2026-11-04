#!/usr/bin/env bash
# Prints the tree hash of the working tree in the given directory without touching
# its index: untracked files are included, ignored files are not.
set -euo pipefail
[[ $# -eq 1 ]] || {
  echo "usage: ${0##*/} <dir>" >&2
  exit 2
}
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
export GIT_INDEX_FILE="${tmp}/index"
cd "$1"
git read-tree HEAD
git add -A
git write-tree
