#!/usr/bin/env bash
# Lists dated runs newest first; keywords are literal, case-insensitive OR matches.
set -euo pipefail
[[ $# -ge 2 ]] || {
  echo "usage: ${0##*/} <specs-repo> <owner>/<repo> [keyword…]" >&2
  exit 2
}
[[ $1 == /* && $2 =~ ^[^/]+/[^/]+$ ]] || {
  echo 'absolute specs-repo and owner/repo are required' >&2
  exit 2
}
repo="$(realpath -e "$1")"
project="$2"
shift 2
patterns=()
for keyword in "$@"; do
  [[ -n ${keyword} && ${keyword} != *$'\n'* ]] || {
    echo 'keywords must be nonempty single-line strings' >&2
    exit 2
  }
  patterns+=(-e "${keyword}")
done
find "${repo}" -path "${repo}/.git" -prune -o -type f \
  \( -name decisions.md -o -name task.md \) -printf '%h\0' | LC_ALL=C sort -zu |
  while IFS= read -r -d '' dir; do
    run="${dir##*/}"
    if [[ ${run} =~ ^[0-9]{8}-[0-9]{2}- ]]; then
      printf '%s\t%s\n' "${run:0:11}" "${dir#"${repo}"/}"
    fi
  done | LC_ALL=C sort -r -s -t $'\t' -k1,1 |
  while IFS=$'\t' read -r stamp rel; do
    mark=' '
    [[ ${rel} != "${project}/"* ]] || mark='*'
    if [[ ${#patterns[@]} -eq 0 ]]; then
      printf '%s %s %s\n' "${stamp}" "${mark}" "${rel}"
      continue
    fi
    files=()
    for file in decisions.md task.md; do
      [[ ! -f ${repo}/${rel}/${file} ]] || files+=("${repo}/${rel}/${file}")
    done
    rc=0
    matches="$(LC_ALL=C.UTF-8 grep -H -n -i -F \
      "${patterns[@]}" -- "${files[@]}")" || rc=$?
    ((rc <= 1)) || exit "${rc}"
    if [[ -n ${matches} ]]; then
      printf '== %s %s %s\n%s\n' "${stamp}" "${mark}" "${rel}" "${matches}"
    fi
  done
