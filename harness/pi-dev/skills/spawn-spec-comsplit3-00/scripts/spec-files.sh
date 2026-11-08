#!/usr/bin/env bash
# Prints the SHA256 manifest of spec attachments: one "<hash>  <path>" line per file,
# paths relative to the attachments directory.
set -euo pipefail
[[ $# -eq 1 ]] || {
  echo "usage: ${0##*/} <files-dir>" >&2
  exit 2
}
[[ $1 == /* ]] || {
  echo 'files-dir must be absolute' >&2
  exit 2
}
lexical="$(realpath -ms -- "$1")"
physical="$(realpath -e -- "$1")"
[[ ${lexical} == "${physical}" && -d ${physical} ]] || {
  echo 'files-dir and its ancestors must be directories without symbolic links' >&2
  exit 2
}
cd "${physical}"
unsupported="$(find . ! -type d ! -type f -print -quit)"
[[ -z ${unsupported} ]] || {
  echo "unsupported attachment type: ${unsupported}" >&2
  exit 2
}
find . -type f -printf '%P\0' | LC_ALL=C sort -z | xargs -0 -r sha256sum --
