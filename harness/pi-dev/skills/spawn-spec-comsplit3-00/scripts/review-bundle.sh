#!/usr/bin/env bash
# Writes the review bundle between two tree snapshots of a working tree.
set -euo pipefail
[[ $# -eq 4 ]] || {
  echo "usage: ${0##*/} <dir> <from-tree> <to-tree> <out-file>" >&2
  exit 2
}
[[ $1 == /* && $4 == /* ]] || {
  echo 'dir and out-file must be absolute' >&2
  exit 2
}
[[ ! -e $4 && ! -L $4 ]] || {
  echo 'out-file already exists; choose a new artifact path' >&2
  exit 2
}
tmp="$(mktemp "${4}.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT
{
  git -C "$1" diff --stat "$2" "$3" --
  git -C "$1" diff -U10 "$2" "$3" --
} >"${tmp}"
mv -n -- "${tmp}" "$4"
[[ ! -e ${tmp} ]] || {
  echo 'out-file appeared concurrently' >&2
  exit 1
}
