#!/usr/bin/env bash
# List past runs of the specs repository newest first with matching lines.
set -euo pipefail

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

if [[ "$#" -lt 2 ]]; then
  echo 'usage: precedents.sh <owner/repo> <exclude-run-rel|-> [<keyword>...]' >&2
  exit 2
fi
specs="${SPECS_REPO:-/workspace/vscode/raven/man4llm}"
project="${1%/}"
exclude="${2%/}"
shift 2
if [[ ! "${project}" =~ ^[^/]+/[^/]+$ ]]; then
  fail "project must be owner/repo: ${project}" 2
fi
if [[ -z "${exclude}" || "${exclude}" == /* ]]; then
  fail "exclude must be a run path relative to ${specs} or -" 2
fi
if [[ ! -d "${specs}" ]]; then
  fail "no specs repository: ${specs}" 2
fi
patterns=()
for keyword in "$@"; do
  if [[ -z "${keyword}" ]]; then
    fail 'empty keyword' 2
  fi
  patterns+=(-e "${keyword}")
done
if ! LC_ALL=C.UTF-8 grep -qiF 'тесты' <<<'Тесты'; then
  fail 'C.UTF-8 locale unavailable or lacks Cyrillic case folding' 2
fi
run_re='^[0-9]{8}-[0-9]{2}-'
tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT
find "${specs}" -path "${specs}/.git" -prune -o -type f \
  \( -name task.md -o -name decisions.md \) -printf '%h\0' >"${tmp}/found"
sort -zu "${tmp}/found" >"${tmp}/dirs"
: >"${tmp}/runs"
while IFS= read -r -d '' dir; do
  rel="${dir#"${specs}/"}"
  name="${rel##*/}"
  if [[ "${rel}" != */* || ! "${name}" =~ ^([0-9]{8})-([0-9]{2})- ]]; then
    continue
  fi
  key="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
  nested=0
  IFS=/ read -r -a parts <<<"${rel%/*}"
  for part in "${parts[@]}"; do
    if [[ "${part}" =~ ${run_re} ]]; then
      nested=1
    fi
  done
  if ((nested)); then
    continue
  fi
  if [[ "${exclude}" != '-' ]]; then
    if [[ "${rel}" == "${exclude}" || "${rel}" == "${exclude}"/* ]]; then
      continue
    fi
  fi
  mark=' '
  if [[ "${rel}" == "${project}"/* ]]; then
    mark='*'
  fi
  printf '%s\t%s\t%s\n' "${key}" "${mark}" "${rel}" >>"${tmp}/runs"
done <"${tmp}/dirs"
LC_ALL=C sort -t $'\t' -k1,1r -k3,3 "${tmp}/runs" >"${tmp}/sorted"
while IFS=$'\t' read -r key mark rel; do
  header="${mark} ${key:0:8}-${key:8:2} ${rel}"
  if [[ "${#patterns[@]}" -eq 0 ]]; then
    printf '%s\n' "${header}"
    continue
  fi
  lines=''
  for file in task.md decisions.md; do
    if [[ ! -f "${specs}/${rel}/${file}" ]]; then
      continue
    fi
    rc=0
    found="$(LC_ALL=C.UTF-8 grep -a -n -i -F "${patterns[@]}" \
      -- "${specs}/${rel}/${file}")" || rc=$?
    if [[ "${rc}" -gt 1 ]]; then
      fail "grep failed: ${rel}/${file}"
    fi
    if [[ -z "${found}" ]]; then
      continue
    fi
    while IFS= read -r line; do
      lines+="    ${file}:${line}"$'\n'
    done <<<"${found}"
  done
  if [[ -n "${lines}" ]]; then
    printf '%s\n%s' "${header}" "${lines}"
  fi
done <"${tmp}/sorted"
