#!/usr/bin/env bash
# List topics of a project in the specs repository or create a new run directory.
set -euo pipefail

usage() {
  echo 'usage: run-dir.sh topics <main-dir>' >&2
  echo '       run-dir.sh create <main-dir> <short-name> [<topic>]' >&2
  exit 2
}

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

specs="${SPECS_REPO:-/workspace/vscode/raven/man4llm}"

# Print the project directory of the specs repository for a main work tree.
project_dir() {
  local main="$1" top specs_top git_dir common
  if [[ "${main}" != /* || ! -d "${main}" ]]; then
    fail "not an absolute directory: ${main}" 2
  fi
  top="$(git -C "${main}" rev-parse --show-toplevel)" ||
    fail "not a git work tree: ${main}" 2
  if [[ "${top}" != "$(cd "${main}" && pwd -P)" ]]; then
    fail "not a work tree root: ${main}" 2
  fi
  git_dir="$(git -C "${main}" rev-parse --path-format=absolute --git-dir)" ||
    fail "cannot resolve git dir: ${main}" 2
  common="$(git -C "${main}" rev-parse --path-format=absolute --git-common-dir)" ||
    fail "cannot resolve git common dir: ${main}" 2
  if [[ -z "${git_dir}" || "${git_dir}" != "${common}" ]]; then
    fail "not the main work tree: ${main}" 2
  fi
  specs_top="$(git -C "${specs}" rev-parse --show-toplevel)" ||
    fail "not a specs repository: ${specs}" 2
  if [[ "${specs_top}" != "$(cd "${specs}" && pwd -P)" ]]; then
    fail "not a specs repository root: ${specs}" 2
  fi
  printf '%s/%s/%s\n' "${specs}" "$(basename "$(dirname "${top}")")" \
    "$(basename "${top}")"
}

list_topics() {
  local base="$1" entry name
  if [[ ! -d "${base}" ]]; then
    return 0
  fi
  for entry in "${base}"/*/; do
    if [[ ! -d "${entry}" ]]; then
      continue
    fi
    name="$(basename "${entry}")"
    if [[ ! "${name}" =~ ^[0-9]{8}-[0-9]{2}- ]]; then
      printf '%s\n' "${name}"
    fi
  done
}

create_run() {
  local base="$1" name="$2" topic="$3" day nn dir
  local -a taken
  if [[ ! "${name}" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
    fail "bad short name: ${name}" 2
  fi
  if [[ -n "${topic}" ]]; then
    if [[ ! "${topic}" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
      fail "bad topic: ${topic}" 2
    fi
    base="${base}/${topic}"
  fi
  mkdir -p -- "${base}"
  exec 9<"${base}"
  flock 9
  day="$(date +%Y%m%d)"
  for nn in $(seq -w 1 99); do
    taken=("${base}/${day}-${nn}-"*)
    if [[ -e "${taken[0]}" || -L "${taken[0]}" ]]; then
      continue
    fi
    dir="${base}/${day}-${nn}-${name}"
    mkdir -- "${dir}"
    printf 'RUN_REL=%s\nRUN_DIR=%s\n' "${dir#"${specs}/"}" "${dir}"
    return 0
  done
  fail "no free run number for ${day} in ${base}"
}

if [[ "$#" -lt 2 ]]; then
  usage
fi
case "$1" in
topics)
  if [[ "$#" -ne 2 ]]; then
    usage
  fi
  base="$(project_dir "$2")"
  list_topics "${base}"
  ;;
create)
  if [[ "$#" -ne 3 && "$#" -ne 4 ]]; then
    usage
  fi
  base="$(project_dir "$2")"
  create_run "${base}" "$3" "${4:-}"
  ;;
*)
  usage
  ;;
esac
