#!/usr/bin/env bash
# Lists earlier runs of the specs repository with decisions.md or task.md, newest first
# by the date in the run directory name, and marks runs of the given project with "*".
# With keywords prints the case-insensitive matching lines of these files instead.
set -euo pipefail
[[ $# -ge 2 ]] || {
  echo "usage: ${0##*/} <specs-repo> <owner>/<repo> [keyword…]" >&2
  exit 2
}
repo="${1%/}"
project="${2%/}"
shift 2
pattern=''
if [[ $# -gt 0 ]]; then
  pattern="$(
    IFS='|'
    echo "$*"
  )"
fi
find "${repo}" -path "${repo}/.git" -prune -o -type f \
  \( -name decisions.md -o -name task.md \) -printf '%h\n' | LC_ALL=C sort -u |
  while read -r dir; do
    run="$(basename "${dir}")"
    if [[ ${run} =~ ^[0-9]{8}-[0-9]{2}- ]]; then
      printf '%s\t%s\n' "${run:0:11}" "${dir#"${repo}"/}"
    fi
  done | LC_ALL=C sort -r -s -t $'\t' -k1,1 | while IFS=$'\t' read -r stamp rel; do
  mark=' '
  if [[ ${rel} == "${project}"/* ]]; then
    mark='*'
  fi
  if [[ -z ${pattern} ]]; then
    printf '%s %s %s\n' "${stamp}" "${mark}" "${rel}"
    continue
  fi
  matches="$(
    cd "${repo}/${rel}"
    grep -H -n -i -E -- "${pattern}" decisions.md task.md 2>/dev/null
  )" || true
  if [[ -n ${matches} ]]; then
    printf '== %s %s %s\n%s\n' "${stamp}" "${mark}" "${rel}" "${matches}"
  fi
done
