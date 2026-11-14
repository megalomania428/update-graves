#!/usr/bin/env bash
# Check the file ownership manifest of a split plan, the files changed by the branch of
# one part and whether the branches of earlier parts are merged into the base. A file of
# wave 0 that one part of wave 1 fills later is reported as a handover, not an overlap.
set -euo pipefail

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

usage() {
  cat >&2 <<'EOF'
usage: scope.sh manifest <ownership.tsv>
      scope.sh branch <repo> <base-ref> <branch> <ownership.tsv> <part>
      scope.sh merged <repo> <target-ref> <branch>...
EOF
  exit 2
}

parts=()
waves=()
paths=()

read_manifest() {
  local file="$1" number=0 line part wave path extra
  [[ -f "${file}" ]] || fail "no manifest: ${file}" 2
  while IFS= read -r line || [[ -n "${line}" ]]; do
    number=$((number + 1))
    [[ -z "${line}" || "${line}" == '#'* ]] && continue
    IFS=$'\t' read -r part wave path extra <<<"${line}"
    if [[ -z "${path}" || -n "${extra}" ]]; then
      fail "${file}:${number}: expected part, wave and path" 2
    fi
    [[ "${part}" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] ||
      fail "${file}:${number}: bad part: ${part}" 2
    [[ "${wave}" =~ ^[012]$ ]] || fail "${file}:${number}: bad wave: ${wave}" 2
    if [[ "${path}" == /* || "${path}" == *'//'* || "/${path}/" == */../* ||
      "/${path}/" == */./* ]]; then
      fail "${file}:${number}: path must be relative and normalized: ${path}" 2
    fi
    parts+=("${part}")
    waves+=("${wave}")
    paths+=("${path}")
  done <"${file}"
  [[ "${#parts[@]}" -gt 0 ]] || fail "empty manifest: ${file}" 2
}

# A path ending with / owns the whole directory.
covers() {
  local owner="$1" path="$2"
  if [[ "${owner}" == */ ]]; then
    [[ "${path}" == "${owner}"* ]]
  else
    [[ "${path}" == "${owner}" ]]
  fi
}

overlap() {
  covers "$1" "$2" || covers "$2" "$1"
}

check_manifest() {
  local i j known errors=0
  declare -A wave_of=()
  for i in "${!parts[@]}"; do
    known="${wave_of[${parts[i]}]:-}"
    if [[ -n "${known}" && "${known}" != "${waves[i]}" ]]; then
      printf '%s\t%s\n' 'waves' "${parts[i]}"
      errors=$((errors + 1))
    fi
    wave_of[${parts[i]}]="${waves[i]}"
  done
  for i in "${!parts[@]}"; do
    [[ "${waves[i]}" == '1' ]] || continue
    for j in "${!parts[@]}"; do
      if [[ "${i}" -eq "${j}" || "${parts[i]}" == "${parts[j]}" ||
        "${waves[j]}" == '2' ]]; then
        continue
      fi
      if [[ "${waves[j]}" == '1' && "${j}" -lt "${i}" ]]; then
        continue
      fi
      if [[ "${waves[j]}" == '0' && "${paths[i]}" == "${paths[j]}" &&
        "${paths[i]}" != */ ]]; then
        printf '%s\t%s\t%s\n' 'handover' "${parts[j]}:${paths[j]}" "${parts[i]}"
      elif overlap "${paths[i]}" "${paths[j]}"; then
        printf '%s\t%s\t%s\t%s\n' 'overlap' "${parts[i]}:${paths[i]}" \
          "${parts[j]}:${paths[j]}" "wave ${waves[j]}"
        errors=$((errors + 1))
      fi
    done
  done
  [[ "${errors}" -eq 0 ]]
}

check_branch() {
  local repo="$1" base="$2" branch="$3" part="$4" i path owned errors=0 known=0
  for i in "${!parts[@]}"; do
    [[ "${parts[i]}" == "${part}" ]] && known=1
  done
  [[ "${known}" -eq 1 ]] || fail "no part ${part} in the manifest" 2
  git -C "${repo}" rev-parse --verify --quiet "${base}^{commit}" >/dev/null ||
    fail "unknown base: ${base}" 2
  git -C "${repo}" rev-parse --verify --quiet "${branch}^{commit}" >/dev/null ||
    fail "unknown branch: ${branch}" 2
  while IFS= read -r -d '' path; do
    owned=0
    for i in "${!parts[@]}"; do
      if [[ "${parts[i]}" == "${part}" ]] && covers "${paths[i]}" "${path}"; then
        owned=1
        break
      fi
    done
    if [[ "${owned}" -eq 0 ]]; then
      printf '%s\t%s\n' 'foreign' "${path}"
      errors=$((errors + 1))
    fi
  done < <(git -C "${repo}" -c core.quotePath=false diff --no-renames --name-only -z \
    "${base}...${branch}")
  [[ "${errors}" -eq 0 ]]
}

check_merged() {
  local repo="$1" target="$2" branch errors=0
  shift 2
  git -C "${repo}" rev-parse --verify --quiet "${target}^{commit}" >/dev/null ||
    fail "unknown target: ${target}" 2
  for branch in "$@"; do
    git -C "${repo}" rev-parse --verify --quiet "${branch}^{commit}" >/dev/null ||
      fail "unknown branch: ${branch}" 2
    if ! git -C "${repo}" merge-base --is-ancestor "${branch}" "${target}"; then
      printf '%s\t%s\n' 'unmerged' "${branch}"
      errors=$((errors + 1))
    fi
  done
  [[ "${errors}" -eq 0 ]]
}

[[ "$#" -ge 1 ]] || usage
command="$1"
shift
case "${command}" in
manifest)
  [[ "$#" -eq 1 ]] || usage
  read_manifest "$1"
  check_manifest
  ;;
branch)
  [[ "$#" -eq 5 ]] || usage
  read_manifest "$4"
  check_branch "$1" "$2" "$3" "$5"
  ;;
merged)
  [[ "$#" -ge 3 ]] || usage
  check_merged "$@"
  ;;
*)
  usage
  ;;
esac
