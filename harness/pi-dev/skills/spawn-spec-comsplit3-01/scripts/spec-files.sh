#!/usr/bin/env bash
# Manifests, base hashes and diffs of spec attachments for files of a work tree.
set -euo pipefail
set -o noclobber

usage() {
  echo 'usage: spec-files.sh manifest <files-dir>' >&2
  echo '       spec-files.sh base <work-tree> <files-dir>' >&2
  echo '       spec-files.sh replace-diff <work-tree> <files-dir> <out-file>' >&2
  echo '       spec-files.sh changes <run-dir> <old-NN> <new-NN> <out-file>' >&2
  exit 2
}

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

check_dir() {
  if [[ "$1" != /* || ! -d "$1" || -L "$1" ]]; then
    fail "not an absolute directory: $1" 2
  fi
}

check_out() {
  if [[ "$1" != /* || -e "$1" || -L "$1" || ! -d "$(dirname "$1")" ]]; then
    fail "output must be a new absolute path: $1" 2
  fi
}

# Fail when a parent of a work tree path is a symlink or not a directory.
check_parents() {
  local path="$1" rel="$2" comp
  local -a comps
  if [[ "${rel}" != */* ]]; then
    return 0
  fi
  IFS=/ read -r -a comps <<<"${rel%/*}"
  for comp in "${comps[@]}"; do
    path="${path}/${comp}"
    if [[ -L "${path}" ]] || [[ -e "${path}" && ! -d "${path}" ]]; then
      fail "not a real directory in work tree: ${path}" 2
    fi
  done
}

sha256() {
  local sum
  sum="$(sha256sum <"$1")"
  printf '%s\n' "${sum%% *}"
}

checksum_line() {
  local sum="$1" rel="$2" prefix=''
  if [[ "${rel}" == *\\* || "${rel}" == *$'\r'* ]]; then
    prefix=$'\\'
    rel="${rel//\\/\\\\}"
    rel="${rel//$'\r'/\\r}"
  fi
  printf '%s%s  %s\n' "${prefix}" "${sum}" "${rel}"
}

# Print paths of all files under a directory relative to it, sorted byte wise.
list_files() {
  local dir="$1" odd
  check_dir "${dir}"
  odd="$(find "${dir}" -mindepth 1 ! -type f ! -type d -print -quit)"
  if [[ -n "${odd}" ]]; then
    fail "not a regular file: ${odd}" 2
  fi
  odd="$(find "${dir}" -mindepth 1 \( -type d -empty -o -name $'*\n*' \) -print -quit)"
  if [[ -n "${odd}" ]]; then
    fail "empty directory or bad name: ${odd}" 2
  fi
  if [[ -z "$(find "${dir}" -type f -print -quit)" ]]; then
    fail "no files in ${dir}" 2
  fi
  (cd "${dir}" && find . -type f -printf '%P\n' | LC_ALL=C sort)
}

manifest() {
  local dir="$1" files rel sum
  files="$(list_files "${dir}")"
  while IFS= read -r rel; do
    sum="$(sha256 "${dir}/${rel}")"
    checksum_line "${sum}" "${rel}"
  done <<<"${files}"
}

base() {
  local tree="$1" dir="$2" files rel target sum
  check_dir "${tree}"
  files="$(list_files "${dir}")"
  while IFS= read -r rel; do
    check_parents "${tree}" "${rel}"
    target="${tree}/${rel}"
    if [[ -L "${target}" ]] || [[ -e "${target}" && ! -f "${target}" ]]; then
      fail "not a regular file in work tree: ${target}" 2
    fi
    if [[ -f "${target}" ]]; then
      sum="$(sha256 "${target}")"
      checksum_line "${sum}" "${rel}"
    else
      printf 'absent  %s\n' "${rel}"
    fi
  done <<<"${files}"
}

replace_diff() {
  local tree="$1" dir="$2" out="$3" files rel rc count=0
  check_dir "${tree}"
  check_out "${out}"
  files="$(list_files "${dir}")"
  exec 3>"${out}"
  while IFS= read -r rel; do
    if [[ ! -f "${tree}/${rel}" ]]; then
      continue
    fi
    count=$((count + 1))
    rc=0
    diff -u --label "a/${rel}" --label "b/${rel}" -- "${tree}/${rel}" \
      "${dir}/${rel}" >&3 || rc=$?
    if [[ "${rc}" -gt 1 ]]; then
      exec 3>&-
      rm -f -- "${out}"
      fail "diff failed for ${rel}"
    fi
  done <<<"${files}"
  exec 3>&-
  if [[ "${count}" -eq 0 ]]; then
    rm -f -- "${out}"
    echo 'no full replacements' >&2
  fi
}

changes() {
  local run="$1" old="$2" new="$3" out="$4" part a b rc
  check_dir "${run}"
  if [[ ! "${old}" =~ ^[0-9]{2}$ || ! "${new}" =~ ^[0-9]{2}$ ]]; then
    fail 'versions must be two-digit numbers' 2
  fi
  if [[ ! -f "${run}/spec-v${old}.md" || ! -f "${run}/spec-v${new}.md" ]]; then
    fail "missing spec version in ${run}" 2
  fi
  check_out "${out}"
  empty="$(mktemp -d)"
  trap '[[ -z "${empty:-}" ]] || rm -rf -- "${empty}"' EXIT
  exec 3>"${out}"
  for part in '.md' '-replace.diff' '-files' '-edits'; do
    a="${run}/spec-v${old}${part}"
    b="${run}/spec-v${new}${part}"
    if [[ ! -e "${a}" && ! -e "${b}" ]]; then
      continue
    fi
    if [[ "${part}" == -files || "${part}" == -edits ]]; then
      [[ -e "${a}" ]] || a="${empty}"
      [[ -e "${b}" ]] || b="${empty}"
    else
      [[ -e "${a}" ]] || a=/dev/null
      [[ -e "${b}" ]] || b=/dev/null
    fi
    rc=0
    diff -ruN -- "${a}" "${b}" >&3 || rc=$?
    if [[ "${rc}" -gt 1 ]]; then
      exec 3>&-
      rm -f -- "${out}"
      fail "diff failed for spec-v${old}${part}"
    fi
  done
  exec 3>&-
  printf '%s\n' "${out}"
}

if [[ "$#" -lt 2 ]]; then
  usage
fi
mode="$1"
shift
case "${mode}" in
manifest)
  [[ "$#" -eq 1 ]] || usage
  manifest "$1"
  ;;
base)
  [[ "$#" -eq 2 ]] || usage
  base "$1" "$2"
  ;;
replace-diff)
  [[ "$#" -eq 3 ]] || usage
  replace_diff "$1" "$2" "$3"
  ;;
changes)
  [[ "$#" -eq 4 ]] || usage
  changes "$1" "$2" "$3" "$4"
  ;;
*)
  usage
  ;;
esac
