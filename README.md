# Update graves

<!-- cspell:ignore LZMA pipefail GOTOOLCHAIN GOPATH -->
<!-- markdownlint-disable MD013 -->

A declarative application builder for upstream projects with local patches. Go handles source selection, cloning, patching, build steps, packaging, GitHub Releases and optional Telegram posts. Adding an application requires only a new `sources/<name>/build.yaml`, documentation, patches and scripts; no Go changes are needed.

## Layout

- `sources/<name>/build.yaml` – upstream ref, patch number, build targets and release destinations.
- `sources/<name>/README.md` – application and patch descriptions used for release notes.
- `sources/<name>/patches/` and `scripts/` – application-specific implementation.
- `_shared/` – optional shared assets declared by each consuming recipe; `_shared/_all/prepare.sh` installs the packages common to every build.
- `golang/cmd/graves/` – the `prepare`, `fetch`, `build`, `upload` and `telegram` commands.
- `.graves/` – ignored upstream clone, published assets and release notes.

The first recipes build opencode CLI and Electron desktop for Linux/Windows x64, and the patched `@raven428/pi-web` npm tarball. Heavy application builds run only in the build workflow.

## Recipe schema

Paths in a target are relative to `sources/<name>`. A ref must be a tag or branch accepted by `git clone --branch`. Unknown YAML fields are errors.

```yaml
---
upstream:
  url: https://github.com/owner/repo.git
  ref: v1.2.3
  # Required local patch number of this recipe.
  patch: 0
# Optional paths or globs, only under _shared/ at the repository root.
shared: [_shared/_all/prepare.sh, _shared/helpers/*.sh]
release:
  # Optional; an empty or absent list defaults to self only.
  targets:
    - type: self
    - type: repo
      repo: private-releases
    - type: telegram
      chat: "@channel"
  # Unicode characters, header included; default 4096, must exceed 200.
  notes_limit: 4096
targets:
  - name: linux
    # Optional; defaults to ubuntu-24.04.
    runner: ubuntu-24.04
    # Optional; without an image, steps run directly on the runner.
    image: ""
    # Optional; directories applied in order, files sorted by name.
    patches: [patches/common, patches/linux]
    # Optional; no partial restore keys and no automatic npm cache.
    cache:
      paths: ["~/.cache/electron", ".graves/upstream/packages/desktop"]
      key_files: ["scripts/**", "patches/common/**", "patches/linux/**"]
    steps:
      - bash ../../_shared/_all/prepare.sh
      - bash scripts/build.sh
```

Target names must match `[a-z0-9][a-z0-9_-]*` and be unique. At least one target and step are required, `upstream.patch` is a required non-negative integer, and patch paths cannot escape the source root. Recipes declare no artifacts: every regular file a target leaves in `GRAVES_OUT_DIR` is published as is. An empty output directory and any non-regular entry are errors, while a file name without the version only logs a warning. Steps own all renaming and archiving, so bare Linux binaries use `.txz` with `tar` and `xz -T0 -9e` and Windows binaries use `.7z` with LZMA2 at maximum compression, both carrying the version on the archive and on the entry inside it. No separate SHA256SUMS are generated.

Cache keys include the application, target, upstream ref, the first 16 hex characters of SHA256 over sorted unique paths of `build.yaml` and matching `key_files`, NUL separators and contents, and the upstream commit SHA resolved by fetch, so a moved branch never restores stale sources. `key_files` supports recursive `**`; cache paths are passed as a multiline string to Actions. Fetch happens before restoring the exact cache key.

## Build step environment

Each step runs as `bash -euo pipefail -c <command>` from the absolute source directory, inheriting the builder's environment. An `image` runs the same command in Podman with host networking, the repository mounted at the same absolute path, `GRAVES_*` values and configured proxy variables.

- `GRAVES_NAME` – application directory name.
- `GRAVES_TARGET` – target name.
- `GRAVES_SOURCE_DIR` – absolute `sources/<name>` directory.
- `GRAVES_UPSTREAM_DIR` – absolute `.graves/upstream` clone with patches.
- `GRAVES_OUT_DIR` – absolute `.graves/out`, where steps place outputs.
- `GRAVES_UPSTREAM_REF` – the ref from `build.yaml`.
- `GRAVES_MODE` – `release` or `draft`.
- `GRAVES_TAG` – release tag, empty in draft builds.
- `GRAVES_VERSION` – `<upstream ref without v>-p<patch>`, plus `.dev.<run>.<attempt>` in drafts.
- `GRAVES_FILE_VERSION` – `GRAVES_VERSION` with dots and slashes replaced by underscores, for file names.
- `GRAVES_RUN` – `<GITHUB_RUN_NUMBER>.<GITHUB_RUN_ATTEMPT>`, locally `0.0`.

Versions come only from the recipe: `upstream.ref` without its leading `v` plus `-p<upstream.patch>`, so `v1.202609.0` with `patch: 1` releases `1.202609.0-p1` and drafts build `1.202609.0-p1.dev.<run>.<attempt>`. The tag is only a label: it does not choose or validate the upstream ref, it does not set the version, and it need not be on master. Steps embed `GRAVES_VERSION` inside the build and `GRAVES_FILE_VERSION` in file names.

## Releases and drafts

A pushed `<name>-<label>` tag releases only that application, with one matrix job per target, and the release title is the tag itself. The build workflow reacts to every `*-*` tag, existing source names are matched by the `<name>-` prefix and the longest match wins, so a tag whose prefix matches no recipe fails `prepare` instead of being ignored. Release versions still come from `build.yaml` and never from the tag, so bump `upstream.ref` or `upstream.patch` in the same pull request as the tag.

When a pull request changes an application's upstream ref in `build.yaml` or its patches, update that application's `TAG4REL` below in the same pull request.

`opencode`:

```bash
git checkout master && git pull; TAG4REL='opencode-v1.15.13p0'
git tag -fm $(git branch --sho) ${TAG4REL} && git push --force origin ${TAG4REL}
```

`pi-web`:

```bash
git checkout master && git pull; TAG4REL='pi-web-v1.202609.0p1'
git tag -fm $(git branch --sho) ${TAG4REL} && git push --force origin ${TAG4REL}
```

Internal pull requests build only applications changed under `sources/<name>/` or their declared shared assets, using `git diff --name-only origin/<base>...HEAD`. Deleted recipes are skipped. A change only to common Go/workflow code builds nothing. Manual workflow `build` accepts a `sources` input containing space- or comma-separated names and builds a draft. Branch pushes never build applications. Fork/dependabot pull requests run neither builds nor repository checks. The repository test schedule runs tests and linters, never application rebuilds.

All draft builds share the current repository's `v999` draft. Identical asset names are replaced, while unrelated and older assets remain until manually removed. Drafts never publish to external repositories or Telegram. Each successful matrix job uploads its own artifacts; a failed job uploads nothing, other successful jobs remain published and the workflow stays red. Release notes do not add a partial-build notice.

## Destinations and secrets

Tagged release destinations are any combination of `self`, `repo` (a repository name under the current owner) and `telegram`. A telegram-only release is supported. Prepare generates notes and creates/updates all GitHub destinations before the matrix; a final job posts the notes and successful assets to Telegram, even after partial matrix failure.

- `GITHUB_TOKEN` – current-repository GitHub write token, exposed to graves as `GH_TOKEN`.
- `RELEASES_TOKEN` – fine-grained PAT with `contents: write` on each external repository. External releases keep the same tag and use the destination's default branch when creating that tag.
- `TELEGRAM_BOT_TOKEN` – bot token; the bot must be a channel administrator with permission to post.
- `LLM_API_URL`, `LLM_API_MDL2PI`, `LLM_API_KEY` – exposed as `LLM_URL`, `LLM_NAME`, `LLM_KEY` to the notes generator.
- `SEARCH_MCP_URL`, `SEARCH_MCP_KEY`, `FETCH_MCP_URL`, `FETCH_MCP_KEY` – optional search/fetch endpoints. A server with a missing URL or key is excluded.

LLM notes run `pi -p` in `ghcr.io/raven428/review-pi_dev:latest`, using the MCP gateway without warm-up and only the MCP tool: context files are attached to the prompt, so the model has no file tools that could read the mounted credentials. Context includes the application README/recipe, applied patches, changes since the previous application tag and, when available, upstream history between refs. The first release has no invented comparison. Stdout is the answer; diagnostics stay on stderr. Notes are in English and must fit `notes_limit` minus the Telegram header, counted in Unicode characters. Empty, oversized or failed answers get up to four calls separated by 3/9/15-second delays; missing credentials or exhausted retries use deterministic fallback notes. Secrets are kept in separate temporary configuration files, not context files or container arguments.

Optional notes environment overrides: `LLM_API=openai-responses`, `LLM_EFF=true`, `LLM_RSN=true`, `LLM_CTX=1000000`, `LLM_MAX=131072`, `LLM_LVL=max` and `NOTES_IMAGE=ghcr.io/raven428/review-pi_dev:latest`.

Telegram uses one Rich Message with Markdown and embedded documents, not links to release assets. Each file up to 50 MiB is attached; larger files and files beyond the 50-document limit are logged for manual upload. If no target produced assets, no post is sent and the job fails. Permanent API rejection gets one retry with fallback text and the same documents; network/5xx/429 errors use the client's normal retries. Any final failure keeps the job red. Telegram and real GitHub publication require live credentials; local checks use dry-run or mock servers.

## Local development

Go 1.25.13 or newer is required. Application builds are intentionally not part of the following lightweight checks.

```bash
export PATH="/home/coder/.local/go/bin:${PATH}"
export GOTOOLCHAIN=auto
export PATH="$(go env GOPATH)/bin:${PATH}"
go -C golang mod verify
go -C golang build ./...
go -C golang vet ./...
go -C golang test -race -count=1 ./...
GRAVES_DRY_RUN=1 GITHUB_EVENT_NAME=workflow_dispatch \
  GRAVES_SOURCES='opencode pi-web' GITHUB_REF_NAME=updates-001 \
  GITHUB_REPOSITORY=megalomania428/update-graves \
  GITHUB_OUTPUT=/tmp/graves-out go -C golang run ./cmd/graves prepare
```

`GRAVES_DRY_RUN=1` prints external GitHub/Telegram writes instead of performing them and uses fallback instead of calling the LLM. It does not disable cloning or build steps. Selection outputs are appended to `GITHUB_OUTPUT`, or printed to stdout outside Actions. Fetch/build/upload read `GRAVES_SOURCE`, `GRAVES_TARGET`, `GRAVES_MODE` and `GRAVES_TAG`; draft upload also requires `GRAVES_RELEASE_ID`. Telegram reads `GRAVES_TAG` and files in `.graves/notes` and `.graves/out`. The repository root is resolved from the current directory with Git, including when invoked through `go -C golang run`. Missing/unknown subcommands return 2; runtime errors return 1.
