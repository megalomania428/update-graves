#!/usr/bin/env bash
# cspell:ignore toplevel
# Creates the next free run directory of the day in the specs repository and prints
# its path relative to the repository root.
set -euo pipefail
[[ $# -eq 3 || $# -eq 4 ]] || {
  echo "usage: ${0##*/} <main-dir> <specs-repo> <slug> [topic]" >&2
  exit 2
}
main_dir="$1"
specs_repo="$2"
slug="$3"
topic="${4:-}"
name_re='^[a-z0-9]+(-[a-z0-9]+)*$'
[[ ${slug} =~ ${name_re} ]] || {
  echo "error: bad short name: ${slug}" >&2
  exit 2
}
[[ -z ${topic} || ${topic} =~ ${name_re} ]] || {
  echo "error: bad topic: ${topic}" >&2
  exit 2
}
top="$(git -C "${main_dir}" rev-parse --show-toplevel)"
git_dir="$(git -C "${main_dir}" rev-parse --path-format=absolute --git-dir)"
common="$(git -C "${main_dir}" rev-parse --path-format=absolute --git-common-dir)"
[[ ${git_dir} == "${common}" ]] || {
  echo "error: not the main work tree: ${main_dir}" >&2
  exit 2
}
parent="$(basename "$(dirname "${top}")")/$(basename "${top}")"
[[ -z "${topic}" ]] || parent="${parent}/${topic}"
day="$(date +%Y%m%d)"
last=0
for dir in "${specs_repo}/${parent}/${day}-"[0-9][0-9]-*/; do
  [[ -d "${dir}" ]] || continue
  num="$(basename "${dir}")"
  num="${num#"${day}"-}"
  num="${num%%-*}"
  if ((10#${num} > last)); then
    last=$((10#${num}))
  fi
done
run_rel="$(printf '%s/%s-%02d-%s' "${parent}" "${day}" "$((last + 1))" "${slug}")"
mkdir -p "${specs_repo}/${run_rel}"
echo "${run_rel}"
