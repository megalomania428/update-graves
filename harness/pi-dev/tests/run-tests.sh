#!/usr/bin/env bash
# cspell:ignore trixie
set -euo pipefail
# Runs every run-*.mjs runner and type-checks every tsconfig*.json under this directory
# inside a disposable Node container; the pi runtime is installed afresh on each run.
tests_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ "${1:-}" != '--inside' ]]; then
  repo_dir="$(cd "${tests_dir}/../../.." && pwd -P)"
  exec podman run --rm --pull=newer --security-opt label=disable \
    -v "${repo_dir}:${repo_dir}:ro" -w "${tests_dir}" \
    mirror.gcr.io/library/node:24-trixie-slim bash "${tests_dir}/run-tests.sh" --inside
fi
cd "${tests_dir}"
mapfile -t runners < <(find . -name node_modules -prune -o -type f -name 'run-*.mjs' \
  -print | sort)
mapfile -t configs < <(find . -name node_modules -prune -o -type f \
  -name 'tsconfig*.json' -print | sort)
if ((${#runners[@]} + ${#configs[@]} == 0)); then
  echo "no run-*.mjs or tsconfig*.json found under ${tests_dir}" >&2
  exit 1
fi
runtime='/home/coder/.local/pi-dev'
mkdir -p "${runtime}"
printf '%s\n' '{"overrides":{"@earendil-works/pi-agent-core":"0.85.1",' \
  '"@earendil-works/pi-ai":"0.85.1"}}' >"${runtime}/package.json"
npm install --no-audit --no-fund --install-strategy=nested --prefix "${runtime}" \
  @earendil-works/pi-coding-agent@0.85.1 @types/node@24 typescript@5.9.3
failed=()
for runner in "${runners[@]}"; do
  echo "==> node ${runner}"
  PI_OFFLINE=1 PI_TELEMETRY=0 node "${runner}" || failed+=("${runner}")
done
for config in "${configs[@]}"; do
  echo "==> tsc --noEmit -p ${config}"
  "${runtime}/node_modules/.bin/tsc" --noEmit -p "${config}" || failed+=("${config}")
done
if ((${#failed[@]} > 0)); then
  printf 'FAILED: %s\n' "${failed[@]}" >&2
  exit 1
fi
