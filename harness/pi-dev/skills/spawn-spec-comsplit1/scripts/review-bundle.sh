#!/usr/bin/env bash
# Writes the review bundle between two tree snapshots of a working tree.
set -euo pipefail
[[ $# -eq 4 ]] || {
  echo "usage: ${0##*/} <dir> <from-tree> <to-tree> <out-file>" >&2
  exit 2
}
{
  git -C "$1" diff --stat "$2" "$3"
  git -C "$1" diff -U10 "$2" "$3"
} >"$4"
