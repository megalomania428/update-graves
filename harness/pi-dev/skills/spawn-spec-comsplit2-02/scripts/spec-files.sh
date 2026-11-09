#!/usr/bin/env bash
# Prints the SHA256 manifest of spec attachments: one "<hash>  <path>" line per file,
# paths relative to the attachments directory.
set -euo pipefail
[[ $# -eq 1 ]] || {
  echo "usage: ${0##*/} <files-dir>" >&2
  exit 2
}
cd "$1"
find . -type f -printf '%P\0' | LC_ALL=C sort -z | xargs -0 -r sha256sum
