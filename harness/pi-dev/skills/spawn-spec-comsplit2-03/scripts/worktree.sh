#!/usr/bin/env bash
# cspell:ignore toplevel
# Create a run branch and worktree from HEAD of the main work tree; a dirty main work
# tree is refused unless --allow-dirty is given.
set -euo pipefail

fail() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

allow_dirty=0
if [[ "${1:-}" == '--allow-dirty' ]]; then
  allow_dirty=1
  shift
fi
if [[ "$#" -ne 3 ]]; then
  echo 'usage: worktree.sh [--allow-dirty] <main-dir> <repo-syllable>' \
    '<task-syllable>' >&2
  exit 2
fi
main="$1"
repo="$2"
task="$3"
root="${WT_ROOT:-/workspace/jobs/wtr4sdd}"
if [[ "${main}" != /* || ! -d "${main}" ]]; then
  fail "not an absolute directory: ${main}" 2
fi
for syllable in "${repo}" "${task}"; do
  if [[ ! "${syllable}" =~ ^[a-z0-9]+$ ]]; then
    fail "bad syllable: ${syllable}" 2
  fi
done
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
branch="$(git -C "${main}" symbolic-ref --quiet --short HEAD)" ||
  fail "detached HEAD: ${main}" 2
state="$(git -C "${main}" status --porcelain)"
dirty=0
if [[ -n "${state}" ]]; then
  if ((!allow_dirty)); then
    fail "main work tree is not clean: ${main}" 3
  fi
  dirty="$(wc -l <<<"${state}")"
fi
head="$(git -C "${main}" rev-parse --verify 'HEAD^{commit}')"
flat="${branch//[-\/]/}"
mkdir -p -- "${root}"
exec 9<"${root}"
flock 9
for ((i = 1; i <= 99; i++)); do
  suffix=''
  if ((i > 1)); then
    printf -v suffix '%s%d' '-' "${i}"
  fi
  new_branch="${branch}-${task}${suffix}"
  dir="${root}/${repo}-${flat}-${task}${suffix}"
  if git -C "${main}" show-ref --verify --quiet "refs/heads/${new_branch}"; then
    continue
  fi
  if [[ -e "${dir}" || -L "${dir}" ]]; then
    continue
  fi
  git -C "${main}" worktree add --quiet -b "${new_branch}" "${dir}" "${head}" >&2
  printf 'BRANCH=%s\nWT_DIR=%s\nHEAD=%s\n' "${new_branch}" "${dir}" "${head}"
  if ((dirty)); then
    echo "warning: created from HEAD without uncommitted changes of ${main}:" >&2
    printf '%s\n' "${state}" >&2
    printf 'DIRTY=%s\n' "${dirty}"
  fi
  exit 0
done
fail "no free branch and worktree name for ${branch}-${task}"
