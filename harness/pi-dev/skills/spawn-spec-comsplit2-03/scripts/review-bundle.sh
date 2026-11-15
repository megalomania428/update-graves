#!/usr/bin/env bash
# Write a review bundle with the diff stat and a ten-line context diff of two trees.
# cspell:ignore submodules textconv noclobber mktemp NOSYSTEM ATTR cntrl
set -euo pipefail
set -o noclobber
for name in "${!GIT_@}"; do
  unset "${name}"
done

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

if [[ "$#" -ne 4 ]]; then
  echo 'usage: review-bundle.sh <work-tree> <from-tree> <to-tree> <out-file>' >&2
  exit 2
fi
tree="$1"
from="$2"
to="$3"
out="$4"
if [[ "${tree}" != /* || ! -d "${tree}" ]]; then
  fail "not an absolute directory: ${tree}" 2
fi
for hash in "${from}" "${to}"; do
  if [[ ! "${hash}" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
    fail "not a full hash: ${hash}" 2
  fi
  git -C "${tree}" cat-file -e "${hash}^{tree}" || fail "not a tree: ${hash}" 2
done
if [[ "${out}" != /* || -e "${out}" || -L "${out}" ]]; then
  fail "output must be a new absolute path: ${out}" 2
fi
tmp=''
created=0
complete=0
cleanup() {
  if [[ -n "${tmp}" ]]; then
    rm -rf -- "${tmp}"
  fi
  if [[ "${created}" -eq 1 && "${complete}" -eq 0 ]]; then
    if [[ ! -L "${out}" && "${out}" -ef /dev/fd/3 ]]; then
      rm -f -- "${out}"
    else
      echo "error: output replaced and left untouched: ${out}" >&2
    fi
  fi
}
trap cleanup EXIT
if ! exec 3>"${out}"; then
  fail "cannot create output: ${out}" 2
fi
created=1
tmp="$(mktemp -d)" || fail 'cannot create temporary repository directory'
common="$(git -C "${tree}" rev-parse --path-format=absolute --git-common-dir)" ||
  fail 'cannot locate shared object directory'
format="$(git -C "${tree}" rev-parse --show-object-format)" ||
  fail 'cannot determine object format'
quote_alternate() {
  local path="$1" char escaped i LC_ALL=C
  printf '"'
  for ((i = 0; i < ${#path}; i++)); do
    char="${path:i:1}"
    case "${char}" in
    \\ | '"') printf '\\%s' "${char}" ;;
    [[:cntrl:]])
      printf -v escaped '\\%03o' "'${char}"
      printf '%s' "${escaped}"
      ;;
    *) printf '%s' "${char}" ;;
    esac
  done
  printf '"\n'
}
if (
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_ATTR_NOSYSTEM=1
  git init -q --bare --template= --initial-branch=master \
    --object-format="${format}" "${tmp}" || exit 1
  quote_alternate "${common}/objects" >"${tmp}/objects/info/alternates" || exit 1
  mkdir -p -- "${tmp}/info" || exit 1
  : >"${tmp}/info/attributes" || exit 1
  for hash in "${from}" "${to}"; do
    git -C "${tmp}" cat-file -e "${hash}^{tree}" ||
      fail 'cannot read trees through C-quoted alternates' 2
  done
  git -C "${tmp}" -c core.attributesFile=/dev/null diff \
    --no-color --no-ext-diff --no-textconv --ignore-submodules=none \
    --stat "${from}" "${to}" >&3 || exit 1
  git -C "${tmp}" -c core.attributesFile=/dev/null diff \
    --no-color --no-ext-diff --no-textconv --ignore-submodules=none \
    -U10 "${from}" "${to}" >&3 || exit 1
); then
  :
else
  rc=$?
  fail 'isolated git diff failed' "${rc}"
fi
complete=1
exec 3>&-
printf '%s\n' "${out}"
