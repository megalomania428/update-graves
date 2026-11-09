#!/usr/bin/env bash
# Creates the task branch and worktree from HEAD of the main working copy and prints
# the branch, the worktree path and HEAD, one per line. Fails when the working copy
# is dirty or HEAD is detached.
set -euo pipefail
[[ $# -eq 3 ]] || {
  echo "usage: ${0##*/} <main-dir> <repo-slug> <task-slug>" >&2
  exit 2
}
main_dir="$1"
repo_slug="$2"
task_slug="$3"
root="${WT_ROOT:-/workspace/jobs/wtr4sdd}"
if [[ -n "$(git -C "${main_dir}" status --short)" ]]; then
  echo 'main working copy is not clean' >&2
  exit 1
fi
branch="$(git -C "${main_dir}" symbolic-ref --quiet --short HEAD)" || {
  echo 'HEAD is detached' >&2
  exit 1
}
compact="${branch//-/}"
compact="${compact//\//}"
base_branch="${branch}-${task_slug}"
base_dir="${root}/${repo_slug}-${compact}-${task_slug}"
new_branch="${base_branch}"
wt_dir="${base_dir}"
n=1
while git -C "${main_dir}" show-ref --verify --quiet "refs/heads/${new_branch}" ||
  [[ -e "${wt_dir}" ]]; do
  n=$((n + 1))
  new_branch="${base_branch}-${n}"
  wt_dir="${base_dir}-${n}"
done
mkdir -p "${root}"
git -C "${main_dir}" worktree add --quiet -b "${new_branch}" "${wt_dir}" HEAD
echo "${new_branch}"
echo "${wt_dir}"
git -C "${main_dir}" rev-parse HEAD
