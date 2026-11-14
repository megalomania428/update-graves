#!/usr/bin/env bash
# Export a read-only copy of a project revision for scouts and reviewers of a split plan.
set -euo pipefail

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

if [[ "$#" -ne 3 ]]; then
  echo 'usage: src-snapshot.sh <repo> <rev> <out-dir>' >&2
  exit 2
fi
repo="$1"
rev="$2"
out="$3"
if [[ "${repo}" != /* || ! -d "${repo}" ]]; then
  fail "not an absolute directory: ${repo}" 2
fi
if [[ "${out}" != /* ]]; then
  fail "output must be an absolute path: ${out}" 2
fi
if [[ -e "${out}" || -L "${out}" ]]; then
  fail "output exists: ${out}" 2
fi
sha="$(git -C "${repo}" rev-parse --verify --quiet "${rev}^{commit}")" ||
  fail "unknown revision: ${rev}" 2
mkdir -p -- "${out}"
trap 'rm -rf -- "${out}"' EXIT
git -C "${repo}" archive --format=tar "${sha}" | tar -x -C "${out}"
chmod -R a-w -- "${out}"
trap - EXIT
printf 'REV=%s\nSRC_DIR=%s\n' "${sha}" "${out}"
