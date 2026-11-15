#!/usr/bin/env bash
# Manifests, digests and version changes of plan directories in the run directory.
set -euo pipefail

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

usage() {
  echo 'usage: plan-files.sh manifest|digest <dir>' >&2
  echo '       plan-files.sh changes <run-dir> <old> <new> <out>' >&2
  exit 2
}

manifest() {
  local dir="$1" path rel
  local -a files=()
  [[ "${dir}" == /* && -d "${dir}" && ! -L "${dir}" ]] ||
    fail "not an absolute directory: ${dir}" 2
  while IFS= read -r -d '' path; do
    rel="${path#"${dir}/"}"
    if [[ "${rel}" == *$'\n'* || "${rel}" == *\\* ]]; then
      fail "unsupported file name: ${rel}" 2
    fi
    if [[ -L "${path}" || ! -f "${path}" ]]; then
      fail "not a regular file: ${rel}" 2
    fi
    files+=("${rel}")
  done < <(find "${dir}" -mindepth 1 ! -type d -print0)
  [[ "${#files[@]}" -gt 0 ]] || fail "empty directory: ${dir}" 2
  printf '%s\0' "${files[@]}" | LC_ALL=C sort -z |
    (cd "${dir}" && xargs -0 sha256sum --)
}

changes() {
  local run="$1" old="$2" new="$3" out="$4" rc=0
  [[ "${run}" == /* && -d "${run}" ]] || fail "not an absolute directory: ${run}" 2
  [[ "${old}" =~ ^[0-9]{2}$ && "${new}" =~ ^[0-9]{2}$ ]] ||
    fail 'versions must be two-digit numbers' 2
  [[ -d "${run}/plan-v${old}" && -d "${run}/plan-v${new}" ]] ||
    fail "missing plan-v${old} or plan-v${new} in ${run}" 2
  [[ "${out}" == /* ]] || fail "output must be absolute: ${out}" 2
  if [[ -e "${out}" || -L "${out}" ]]; then
    fail "output exists: ${out}" 2
  fi
  (cd "${run}" && diff -ruN "plan-v${old}" "plan-v${new}") >"${out}" || rc=$?
  if [[ "${rc}" -gt 1 ]]; then
    rm -f -- "${out}"
    fail "diff failed with ${rc}"
  fi
  printf '%s\n' "${out}"
}

[[ "$#" -ge 1 ]] || usage
case "$1" in
manifest)
  [[ "$#" -eq 2 ]] || usage
  manifest "$2"
  ;;
digest)
  [[ "$#" -eq 2 ]] || usage
  sums="$(manifest "$2")"
  hash="$(printf '%s\n' "${sums}" | sha256sum)"
  printf '%s\n' "${hash%% *}"
  ;;
changes)
  [[ "$#" -eq 5 ]] || usage
  changes "$2" "$3" "$4" "$5"
  ;;
*) usage ;;
esac
