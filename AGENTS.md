# Agent instructions

<!-- cspell:ignore mktemp worktrees -->
<!-- markdownlint-disable MD013 -->

## Upstream sources

Upstream repositories are studied and patched through local clones in `/workspace/github`, never through fresh clones in temporary directories, while worktrees of these clones for patch generation live under `/var/tmp/wt4gh`; the only exception is a repository larger than a gigabyte, described below.

- The clone of `https://github.com/<owner>/<repo>` is looked up first in `/workspace/github/<repo>`, then in subdirectories of `/workspace/github` by the remote URL: `grep -lis 'github.com[:/]<owner>/<repo>' /workspace/github/*/.git/config /workspace/github/*/*/.git/config`.
- Only when no clone exists, it is created with `git -C /workspace/github clone https://github.com/<owner>/<repo>`, but not for repositories larger than a gigabyte. Before cloning, the size in kilobytes is checked with `curl -s https://api.github.com/repos/<owner>/<repo> | jq .size`: above `1048576` the repository is not cloned and is studied remotely through the GitHub MCP or the `snomcy` search MCP instead. When a patch is needed for such a repository, the way to get a working tree is agreed with the user first.
- A found clone is updated only with `git fetch`; an ssh remote is switched to https first with `git -C <clone> remote set-url origin https://github.com/<owner>/<repo>.git`.
- The checkout of the clone itself – branch, index and working tree – is shared with other agents and people and stays untouched: no `checkout`, `pull`, `reset`, `commit` or edits in it. Upstream patch work happens only in temporary detached worktrees of the clone under `/var/tmp/wt4gh`. Project changes are made in the current project working copy. Upstream patch worktrees remain separate from it, and patch cleanup removes only its own temporary worktrees, never the project working copy.

## Tests

- Explicit user instructions to create new tests or test cases, including instructions given during ongoing work, must be followed for both existing and new code; they take precedence over the default restrictions below.
- By default, when existing code is changed for the user's task, update existing tests to match the agreed new behavior without adding new tests or test cases, except for the mandatory regression coverage below. This applies to both builder code and patched upstream code.
- When a test fails, first thoroughly inspect the affected implementation, related call paths and failure cause for defects and deviations from the task. Fix implementation defects in the code. Change test expectations only after confirming that the agreed behavior change made them obsolete; never weaken assertions, delete tests or skip them merely to get a passing run.
- For new code, add tests when explicitly requested by the user, for especially complex cases at the model's discretion or for the mandatory regression coverage below. By default, do not add tests for other new-code cases.
- After fixing a bug found by a reviewer or expert, tests must cover all scenarios of that bug, including applicable edge cases. This requirement overrides the restriction on new tests for both existing and new code: add tests and test cases as needed, verifying that they fail before the fix and pass after it.
- For upstream patches, edit test sources only in `${W}/b` and include their changes in the corresponding generated patch; never edit test sources directly in `${W}/c` or the shared clone. Regenerate a changed patch through the workflow below, then apply the full patch set to a clean `c` and run the relevant tests there. For upstream work, dependency installs, builds and tests belong only in `c`, an additional working directory explicitly authorized by these project instructions. Patch files and recipe changes belong in the project worktree, not the shared upstream clone.
- Reports must state failure causes, implementation findings, reasons for changed expectations, covered regression scenarios and commands with results. Respect task limits on long, heavy runs; explicitly report checks that were not run rather than claiming they passed.

## Generating patches

### Working directories and delegated roles

- Patch worktrees `a`, `b` and `c` are additional working directories authorized by these project instructions, outside the project working copy. Read upstream sources in the local clone or its patch worktrees; edit upstream code and tests only in `b`. Generate patches by comparing `a` and `b`; install dependencies, build and test upstream only in `c` after applying the full patch set. Save generated patches and accompanying project files in the project working copy.
- When delegating patch work, the coordinating agent creates and removes the patch worktrees according to this workflow. Other agents use the prepared directories without creating branches, worktrees or commits. Without delegation, the agent performing the task manages the patch worktrees directly. Each assignment must include absolute paths to the clone, `a`, `b`, `c` and project output files, the operations allowed in each directory and the agent responsible for cleanup. Commands must explicitly select their working directory; these permissions never allow edits to the shared clone or unrelated project working copies.

Graves applies patches with `patch -d <upstream> -p1 --batch --forward -i <file>` (`ApplyPatches` of `go-lib-ci`): directories in the order of the target's `patches` list, files of a directory sorted by name, only `.patch` and `.diff`. New and regenerated patches are plain unified diffs from `diff -ruN -x .git a b` with `a/` and `b/` path prefixes. `git diff`, `git format-patch`, `git apply` and `git stash` are not used for patches: `git diff` adds `index` lines and git-only extensions that `patch(1)` ignores or misreads. Existing patches in other formats are not rewritten unless they are regenerated for a real change.

### Workflow for patch N

Commands run from the project repository root with these variables; upstream commands explicitly select their clone or patch worktree:

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
  mkdir -p /var/tmp/wt4gh
  W="$(mktemp -d -p /var/tmp/wt4gh "${NAME}-XXXXXX")"
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

- When N is the last patch, before installing dependencies, building or running tests in `c`, `diff -ruN -x .git "${W}/b" "${W}/c"` must print nothing: the patch reproduces the edited tree exactly. Tests of the patched tree run in `c`, within the limits of the current task.
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
- Temporary patch worktrees under `/var/tmp/wt4gh` never outlive the task; `git -C "${UP}" worktree list` shows leftovers, which are removed the same way.
