# Agent instructions

<!-- cspell:ignore mktemp worktrees -->
<!-- markdownlint-disable MD013 -->

## Upstream sources

Upstream repositories are studied and patched through local clones in `/workspace/github`, never through fresh clones in `/tmp` or other temporary directories, while worktrees of these clones for patch generation live in `/tmp`; the only exception is a repository larger than a gigabyte, described below.

- The clone of `https://github.com/<owner>/<repo>` is looked up first in `/workspace/github/<repo>`, then in subdirectories of `/workspace/github` by the remote URL: `grep -lis 'github.com[:/]<owner>/<repo>' /workspace/github/*/.git/config /workspace/github/*/*/.git/config`.
- Only when no clone exists, it is created with `git -C /workspace/github clone https://github.com/<owner>/<repo>`, but not for repositories larger than a gigabyte. Before cloning, the size in kilobytes is checked with `curl -s https://api.github.com/repos/<owner>/<repo> | jq .size`: above `1048576` the repository is not cloned and is studied remotely through the GitHub MCP or the `snomcy` search MCP instead. When a patch is needed for such a repository, the way to get a working tree is agreed with the user first.
- A found clone is updated only with `git fetch`; an ssh remote is switched to https first with `git -C <clone> remote set-url origin https://github.com/<owner>/<repo>.git`.
- The checkout of the clone itself – branch, index and working tree – is shared with other agents and people and stays untouched: no `checkout`, `pull`, `reset`, `commit` or edits in it. All patch work happens in temporary detached worktrees of the clone under `/tmp`. `/workspace/github/_wt` is reserved for SDD worktrees and is never used for patch generation.

## Generating patches

Graves applies patches with `patch -d <upstream> -p1 --batch --forward -i <file>` (`ApplyPatches` of `go-lib-ci`): directories in the order of the target's `patches` list, files of a directory sorted by name, only `.patch` and `.diff`. New and regenerated patches are plain unified diffs from `diff -ruN -x .git a b` with `a/` and `b/` path prefixes. `git diff`, `git format-patch`, `git apply` and `git stash` are not used for patches: `git diff` adds `index` lines and git-only extensions that `patch(1)` ignores or misreads. Existing patches in other formats are not rewritten unless they are regenerated for a real change.

### Workflow for patch N

Commands run from the repository root with these variables:

```bash
NAME='<recipe name>'
UP='/workspace/github/<repo>'
REF='<upstream.ref from sources/<name>/build.yaml>'
PATCH="${PWD}/sources/${NAME}/patches/NNN-<description>.patch"
```

- Resolve the pinned commit without touching the checkout of the clone. A single `fetch` covers tags, branches and commit SHAs, and `FETCH_HEAD` then points to what graves would clone:

  ```bash
  git -C "${UP}" fetch origin "${REF}"
  COMMIT="$(git -C "${UP}" rev-parse --verify 'FETCH_HEAD^{commit}')"
  ```

- Create a collision-free work directory with three worktrees: `a` – the state before patch N, `b` – the state after it, `c` – verification and tests:

  ```bash
  W="$(mktemp -d -p /tmp "${NAME}-XXXXXX")"
  for tree in a b c; do
    git -C "${UP}" worktree add --detach "${W}/${tree}" "${COMMIT}"
  done
  ```

- Apply every patch before N to `a` and `b` in the graves order. Patch paths are absolute because `patch -d` changes the directory before reading `-i`:

  ```bash
  PREV=("${PWD}/sources/${NAME}/patches/001-<description>.patch")
  for tree in a b; do
    for p in "${PREV[@]}"; do
      patch -d "${W}/${tree}" -p1 --batch --forward -i "${p}"
    done
  done
  ```

- When an existing patch N is changed rather than added, apply it to `b` too before editing.
- Edit files only in `${W}/b`. Builds, dependency installs and tests never run in `a` or `b`, otherwise generated files end up in the diff; they run in `c`.
- Generate the patch. `diff` exits with 1 when the trees differ, 0 means no changes and 2 means a failure:

  ```bash
  rc=0
  (cd "${W}" && diff -ruN -x .git a b) >"${PATCH}" || rc=$?
  [[ "${rc}" -eq 1 ]]
  [[ -z "$(grep -n '^Binary files' "${PATCH}")" ]]
  ```

- Verify in `c` with all patches of the target in the graves order, including N and every later one. No `.rej` or `.orig` files and no `fuzz` in the `patch` output are allowed; a later patch that needs fuzz is regenerated with this workflow as well. For a target with several patch directories `ALL` lists them in the order of its `patches` list, as `PREV` does:

  ```bash
  ALL=("${PWD}/sources/${NAME}/patches/"*.patch)
  for p in "${ALL[@]}"; do
    patch -d "${W}/c" -p1 --batch --forward -i "${p}"
  done
  [[ -z "$(find "${W}/c" -name '*.rej' -o -name '*.orig')" ]]
  ```

- When N is the last patch, `diff -ruN -x .git "${W}/b" "${W}/c"` must print nothing: the patch reproduces the edited tree exactly. Tests of the patched tree run in `c`, within the limits of the current task.
- Remove the worktrees and the work directory when done, also after a failed step, and prune stale worktree records of the clone:

  ```bash
  for tree in a b c; do
    git -C "${UP}" worktree remove --force "${W}/${tree}"
  done
  rm -rf "${W}"
  git -C "${UP}" worktree prune
  ```

### Rules

- Patch files are named `NNN-<description>.patch` with a three-digit `NNN` in the apply order.
- Each patch applies on top of all previous patches of the target in order and contains only its own changes, never hunks already present in earlier patches.
- Context lines match the upstream at the pinned ref. Hunk headers and line counts are never edited by hand: after any manual change the patch is regenerated with the workflow above.
- Changes outside the scope of the task are not allowed in patches.
- Every patch is described in the list of patches of `sources/<name>/README.md`, and a changed patch set follows the `TAG4REL` and version rules of `README.md`.
- Temporary worktrees in `/tmp` never outlive the task; `git -C "${UP}" worktree list` shows leftovers, which are removed the same way.
