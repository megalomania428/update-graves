#!/usr/bin/env bash
# cspell:ignore toplevel
# Creates an exclusively reserved run directory and prints its relative path.
set -euo pipefail
[[ $# -eq 3 || $# -eq 4 ]] || {
  echo "usage: ${0##*/} <main-dir> <specs-repo> <slug> [topic]" >&2
  exit 2
}
[[ $1 == /* && $2 == /* && $3 =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || {
  echo 'absolute paths and a lowercase slug are required' >&2
  exit 2
}
top="$(git -C "$1" rev-parse --show-toplevel)"
repo="$(realpath -e "$2")"
[[ $(git -C "${repo}" rev-parse --show-toplevel) == "${repo}" ]] || {
  echo 'specs-repo must be the repository root' >&2
  exit 2
}
topic="${4:-}"
[[ -z ${topic} || ${topic} =~ ^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*$ ]] || {
  echo 'topic must be a safe relative path' >&2
  exit 2
}
parent="$(basename "$(dirname "${top}")")/$(basename "${top}")"
[[ -z ${topic} ]] || parent="${parent}/${topic}"
exec {lock}<"${repo}"
flock -x "${lock}"
base="${repo}"
IFS='/' read -r -a parts <<<"${parent}"
for part in "${parts[@]}"; do
  base="${base}/${part}"
  if [[ -L ${base} ]]; then
    echo 'run parent must not contain symbolic links' >&2
    exit 2
  fi
  if [[ ! -d ${base} ]]; then
    mkdir "${base}"
  fi
done
day="$(date +%Y%m%d)"
last=0
for dir in "${base}/${day}-"[0-9][0-9]-*/; do
  [[ -d ${dir} ]] || continue
  num="$(basename "${dir}")"
  num="${num#"${day}"-}"
  num="${num%%-*}"
  if ((10#${num} > last)); then
    last=$((10#${num}))
  fi
done
((last < 99)) || {
  echo 'daily two-digit run numbers exhausted' >&2
  exit 1
}
run_rel="$(printf '%s/%s-%02d-%s' "${parent}" "${day}" "$((last + 1))" "$3")"
mkdir "${repo}/${run_rel}"
printf '%s\n' "${run_rel}"
