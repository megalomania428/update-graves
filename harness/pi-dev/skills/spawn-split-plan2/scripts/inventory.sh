#!/usr/bin/env bash
# Inventory a source file in a throwaway podman container with pinned tree-sitter wheels.
# cspell:ignore PYTHONDONTWRITEBYTECODE PYTHONPATH
set -euo pipefail

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

if [[ "$#" -lt 3 || "$#" -gt 4 ]]; then
  echo 'usage: inventory.sh <file> <language> <display-name> [<grammar>]' >&2
  exit 2
fi
file="$1"
language="$2"
display="$3"
grammar=()
if [[ "$#" -eq 4 ]]; then
  grammar=(--grammar "$4")
fi
# mirror.gcr.io/library/python:3.13.18-slim pinned by the digest of its index.
default_image='mirror.gcr.io/library/python'
default_image+='@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90'
image="${INVENTORY_IMAGE:-${default_image}}"
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "${file}" == /* && -f "${file}" ]] || fail "not an absolute file: ${file}" 2
[[ "${language}" =~ ^[a-z][a-z0-9_]*$ ]] || fail "bad language: ${language}" 2
[[ -n "${display}" && "${display}" != *$'\n'* ]] || fail 'bad display name' 2
[[ -f "${here}/inventory.py" ]] || fail "missing ${here}/inventory.py" 2
[[ "${file}${here}" != *[:,]* ]] || fail 'paths with ":" or "," cannot be mounted' 2
command -v podman >/dev/null || fail 'podman not found' 2
python3 "${here}/inventory.py" --requirements "${language}" "${grammar[@]}" >/dev/null ||
  fail "unsupported language or grammar: ${language} ${grammar[*]}" 2
echo "image: ${image}" >&2
# shellcheck disable=SC2016
script='set -eu
language="$1"
display="$2"
shift 2
python -m pip install --quiet --disable-pip-version-check --no-cache-dir \
  --only-binary=:all: --target /tmp/site \
  $(python /skill/inventory.py --requirements "${language}" "$@") >&2
PYTHONPATH=/tmp/site exec python /skill/inventory.py --display "${display}" "$@" \
  /src/input "${language}"'
podman run --rm --pull=missing \
  --env PYTHONDONTWRITEBYTECODE=1 --env PIP_ROOT_USER_ACTION=ignore \
  --volume "${here}:/skill:ro" --volume "${file}:/src/input:ro" \
  "${image}" sh -c "${script}" inventory "${language}" "${display}" "${grammar[@]}"
