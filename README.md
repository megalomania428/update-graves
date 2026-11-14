# Update graves

<!-- cspell:ignore LZMA pipefail GOTOOLCHAIN GOPATH GOPROXY duplicacy unpadded PKCS subagents -->
<!-- markdownlint-disable MD013 -->

A declarative application builder for upstream projects with local patches. Go handles source selection, cloning, patching, build steps, packaging, GitHub Releases and optional Telegram posts. Adding an application usually requires only a new `sources/<name>/build.yaml`, documentation, patches and scripts; Go changes are needed only for new builder features, such as commit SHA pins, explicit release versions and zero-padded patch numbers.

## Layout

- `sources/<name>/build.yaml` – upstream ref or release asset, optional release version, patch number and its optional width, build targets with their downloads, secrets and variables, and release destinations.
- `sources/<name>/README.md` – application and patch descriptions used for release notes.
- `sources/<name>/patches/` and `scripts/` – application-specific implementation.
- `_shared/` – optional shared assets declared by each consuming recipe; `_shared/_all/prepare.sh` installs the packages common to every build.
- `golang/cmd/graves/` – the `prepare`, `fetch`, `build`, `upload` and `telegram` commands.
- `.graves/` – ignored upstream clone or release asset, target downloads, published assets and release notes.

The first recipes build opencode CLI and Electron desktop for Linux/Windows x64, the patched `@raven428/pi-web` and `@raven428/pi-subagents` npm tarballs, and `duplicacy` CLI binaries with a file-based GOPROXY of its Go module, and the `happ` Android APK with neutralized traps, rebuilt from a private release asset and published only to the private `megalomania428/update-bunker` repository, and the `donut` Tauri desktop application as a Windows x64 installer, a Windows x64 portable archive and a Linux x64 AppImage, cross-built in local Podman images and published only to the same private repository. Heavy application builds run only in the build workflow.

## Recipe schema

Paths in a target are relative to `sources/<name>`. A ref must be a tag or branch accepted by `git clone --branch`, or a full lowercase 40-character commit SHA, which also requires `upstream.version`. A ref of 7 to 39 hex characters is rejected as a short SHA and a 40-character hex ref with capitals as a non-lowercase SHA, so tags or branches such as `1234567` cannot be used. Unknown YAML fields are errors. Instead of `url` and `ref`, `upstream.release` pins one release asset and then requires `upstream.version`, while targets of such a recipe cannot have `patches`. A release asset in `upstream.release` and in target `downloads` has `repo` as `owner/name` of any owner, `tag`, `asset` – a file name without path separators – and `sha256` of 64 lowercase hex characters; both parts of `repo` consist of letters, digits, `.`, `_` and `-` and are not `.` or `..`, and `tag` consists of letters, digits, `.`, `_`, `+` and `-` and starts with a letter or digit, because both go unescaped into GitHub API paths.

```yaml
---
upstream:
  url: https://github.com/owner/repo.git
  # A tag, a branch or a full 40-character lowercase commit SHA.
  ref: v1.2.3
  # Or, instead of url and ref, one release asset; then version is required.
  # release:
  #   repo: owner/private-sources
  #   tag: sources-app
  #   asset: app-1_2_3.apk
  #   sha256: <64 lowercase hex characters>
  # Optional X.Y.Z release version used instead of the ref; required for a SHA.
  version: 1.2.3
  # Required local patch number of this recipe.
  patch: 0
  # Optional zero-padding width of the patch number, from 1 to 9.
  patch_width: 3
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
    # Optional release assets downloaded into GRAVES_DOWNLOADS_DIR, unique names.
    downloads:
      - repo: owner/tools
        tag: v1.0.0
        asset: tool.jar
        sha256: <64 lowercase hex characters>
    # Optional names of non-empty repository secrets and variables for the steps.
    secrets: [APK_SIGNING_KEY]
    vars: [APK_SIGNING_CERT]
    # Optional; no partial restore keys and no automatic npm cache.
    cache:
      paths: ["~/.cache/electron", ".graves/upstream/packages/desktop"]
      key_files: ["scripts/**", "patches/common/**", "patches/linux/**"]
    steps:
      - bash ../../_shared/_all/prepare.sh
      - bash scripts/build.sh
```

Target names must match `[a-z0-9][a-z0-9_-]*` and be unique. At least one target and step are required, `upstream.patch` is a required non-negative integer, `upstream.patch_width` is from 1 to 9 and must fit the patch number, `upstream.version` is `X.Y.Z` without `v` and leading zeros, and patch paths cannot escape the source root. Secret and variable names match `[A-Z][A-Z0-9_]*`, cannot start with `GRAVES_` or `GITHUB_` and are unique within a target, as are download asset names. Recipes declare no artifacts: every regular file a target leaves in `GRAVES_OUT_DIR` is published as is. An empty output directory and any non-regular entry are errors, while a file name without the version only logs a warning. Steps own all renaming and archiving, so bare Linux binaries use `.txz` with `tar` and `xz -T0 -9e` and Windows binaries use `.7z` with LZMA2 at maximum compression, both carrying the version on the archive and on the entry inside it. No separate SHA256SUMS are generated.

Cache keys include the application, target, upstream ref or release tag, the first 16 hex characters of SHA256 over sorted unique paths of `build.yaml` and matching `key_files`, NUL separators and contents, and the pin that fetch writes to its `sha` output: the upstream commit SHA, so a moved branch never restores stale sources, or the SHA256 of the release asset. `key_files` supports recursive `**` and flattens nested sequences in order, so an item `*alias` extends an anchored list of another target, while a list containing itself is an error; cache paths are passed as a multiline string to Actions. Fetch happens before restoring the exact cache key.

## Build step environment

Each step runs as `bash -euo pipefail -c <command>` from the absolute source directory, inheriting the builder's environment. An `image` runs the same command in Podman with host networking, the repository mounted at the same absolute path, `GRAVES_*` values and configured proxy variables. Secrets and variables declared by the target must be non-empty before the first step; a container receives them as `-e <name>`, so their values never appear in Podman arguments.

- `GRAVES_NAME` – application directory name.
- `GRAVES_TARGET` – target name.
- `GRAVES_SOURCE_DIR` – absolute `sources/<name>` directory.
- `GRAVES_UPSTREAM_DIR` – absolute `.graves/upstream`: the clone with patches or the directory of the release asset.
- `GRAVES_UPSTREAM_ASSET` – absolute path of the downloaded `upstream.release` asset, set only for such a recipe.
- `GRAVES_DOWNLOADS_DIR` – absolute `.graves/downloads` with the target `downloads`, recreated empty by every fetch.
- `GRAVES_OUT_DIR` – absolute `.graves/out`, where steps place outputs.
- `GRAVES_UPSTREAM_REF` – the ref from `build.yaml`, or the tag of `upstream.release`.
- `GRAVES_MODE` – `release` or `draft`.
- `GRAVES_TAG` – release tag, empty in draft builds.
- `GRAVES_VERSION` – `<upstream.version or upstream ref without v>-p<patch zero-padded to patch_width>`, plus `.dev.<run>.<attempt>` in drafts.
- `GRAVES_FILE_VERSION` – `GRAVES_VERSION` with dots and slashes replaced by underscores, for file names.
- `GRAVES_RUN` – `<GITHUB_RUN_NUMBER>.<GITHUB_RUN_ATTEMPT>`, locally `0.0`.

Versions come only from the recipe: `upstream.version`, or else `upstream.ref` without its leading `v`, plus `-p<upstream.patch>` zero-padded to `upstream.patch_width` digits when it is set, so `v1.202609.0` with `patch: 1` releases `1.202609.0-p1`, `version: 3.3.0` with `patch: 1` and `patch_width: 3` releases `3.3.0-p001`, and drafts build `1.202609.0-p1.dev.<run>.<attempt>`. Without `patch_width` the number stays unpadded, so `p7` still sorts after `p6` in recipes released before the field existed. The tag is only a label: it does not choose or validate the upstream ref, it does not set the version, and it need not be on master. Steps embed `GRAVES_VERSION` inside the build and `GRAVES_FILE_VERSION` in file names.

## Releases and drafts

A pushed `<name>-<label>` tag releases only that application, with one matrix job per target, and the release title is the tag itself. The build workflow reacts to every `*-*` tag, existing source names are matched by the `<name>-` prefix and the longest match wins, so a tag whose prefix matches no recipe fails `prepare` instead of being ignored. Release versions still come from `build.yaml` and never from the tag, so bump `upstream.ref`, `upstream.version`, `upstream.patch` or `upstream.patch_width` in the same pull request as the tag. A recipe with `upstream.release` takes its version only from `upstream.version`, `upstream.patch` and `upstream.patch_width`, so a changed release asset also needs a combination of `upstream.version` and `upstream.patch` that no earlier release of the recipe used.

When a pull request changes an application's upstream ref or release asset, version, patch number or patch width in `build.yaml` or its patches, update that application's `TAG4REL` below in the same pull request.

- `donut`:

  ```bash
  git checkout master && git pull; TAG4REL='donut-v0.35.5p000'
  git tag -fm $(git branch --sho) ${TAG4REL} && git push --force origin ${TAG4REL}
  ```

- `duplicacy`:

  ```bash
  git checkout master && git pull; TAG4REL='duplicacy-v3.3.0p002'
  git tag -fm $(git branch --sho) ${TAG4REL} && git push --force origin ${TAG4REL}
  ```

- `happ`:

  ```bash
  git checkout master && git pull; TAG4REL='happ-v3.18.1p001'
  git tag -fm $(git branch --sho) ${TAG4REL} && git push --force origin ${TAG4REL}
  ```

- `opencode`:

  ```bash
  git checkout master && git pull; TAG4REL='opencode-v1.15.13p0'
  git tag -fm $(git branch --sho) ${TAG4REL} && git push --force origin ${TAG4REL}
  ```

- `pi-subagents`:

  ```bash
  git checkout master && git pull; TAG4REL='pi-subagents-v0.69.0p1'
  git tag -fm $(git branch --sho) ${TAG4REL} && git push --force origin ${TAG4REL}
  ```

- `pi-web`:

  ```bash
  git checkout master && git pull; TAG4REL='pi-web-v1.202609.0p8'
  git tag -fm $(git branch --sho) ${TAG4REL} && git push --force origin ${TAG4REL}
  ```

Internal pull requests build only applications changed under `sources/<name>/` or their declared shared assets, using `git diff --name-only origin/<base>...HEAD`. Deleted recipes are skipped. A change only to common Go/workflow code builds nothing. Manual workflow `build` accepts a `sources` input containing space- or comma-separated names and builds a draft. Branch pushes never build applications. Fork/dependabot pull requests run neither builds nor repository checks. The repository test schedule runs tests and linters, never application rebuilds.

Draft builds share the `v999` draft of the current repository, except a recipe without `self` and with at least one `repo` destination: its draft is created and filled in the first such repository. `prepare` creates one draft per destination repository of the selected recipes and passes its ID and repository to every matrix entry as `release_id` and `release_repo`, and upload checks that the repository of its entry is the draft destination of the recipe. Identical asset names are replaced, while unrelated and older assets remain until manually removed. Drafts never publish to Telegram. Each successful matrix job uploads its own artifacts; a failed job uploads nothing, other successful jobs remain published and the workflow stays red. Release notes do not add a partial-build notice.

## Destinations and secrets

Tagged release destinations are any combination of `self`, `repo` (a repository name under the current owner) and `telegram`. A telegram-only release is supported. Prepare generates notes and creates/updates all GitHub destinations before the matrix; a final job posts the notes and successful assets to Telegram, even after partial matrix failure.

- `GITHUB_TOKEN` – current-repository GitHub write token, exposed to graves as `GH_TOKEN`.
- `RELEASES_TOKEN` – fine-grained PAT with `Contents: Read and write` on each external repository, passed only to the `prepare`, `fetch` and `upload` steps: it creates and fills external releases and drafts, downloads release assets of private repositories and raises API limits for public ones. External releases keep the same tag and use the destination's default branch when creating that tag.
- `TELEGRAM_BOT_TOKEN` – bot token; the bot must be a channel administrator with permission to post.
- `LLM_API_URL`, `LLM_API_MDL2PI`, `LLM_API_KEY` – exposed as `LLM_URL`, `LLM_NAME`, `LLM_KEY` to the notes generator.
- `SEARCH_MCP_URL`, `SEARCH_MCP_KEY`, `FETCH_MCP_URL`, `FETCH_MCP_KEY` – optional search/fetch endpoints. A server with a missing URL or key is excluded.
- `APK_SIGNING_KEY` – secret with the one-line base64 of the PKCS#8 DER signing key of `happ`.
- `APK_SIGNING_CERT` – repository variable with the PEM certificate of that key.

Target `secrets` and `vars` reach only the `build and package` step and only for matrix entries that declare them: the workflow passes each supported name as `NAME: ${{ contains(matrix.secrets, 'NAME') && secrets.NAME || '' }}`, or with `matrix.vars` and `vars.NAME` for a variable. A new name is added to that step in the same way, without `toJSON(secrets)` or `secrets[...]`.

LLM notes run `pi -p` in `ghcr.io/megalomania428/review-pi_dev:latest`, using the MCP gateway without warm-up and only the MCP tool: context files are attached to the prompt, so the model has no file tools that could read the mounted credentials. Context includes the application README/recipe, applied patches, changes since the previous application tag and, when available, upstream history between refs. The first release has no invented comparison. Stdout is the answer; diagnostics stay on stderr. Notes are in English and must fit `notes_limit` minus the Telegram header, counted in Unicode characters. Empty, oversized or failed answers get up to four calls separated by 3/9/15-second delays; missing credentials or exhausted retries use deterministic fallback notes. Secrets are kept in separate temporary configuration files, not context files or container arguments.

Optional notes environment overrides: `LLM_API=openai-responses`, `LLM_EFF=true`, `LLM_RSN=true`, `LLM_CTX=1000000`, `LLM_MAX=131072`, `LLM_LVL=max` and `NOTES_IMAGE=ghcr.io/megalomania428/review-pi_dev:latest`.

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

`GRAVES_DRY_RUN=1` prints external GitHub/Telegram writes instead of performing them and uses fallback instead of calling the LLM. It does not disable cloning, downloads or build steps. Selection outputs are appended to `GITHUB_OUTPUT`, or printed to stdout outside Actions. Fetch/build/upload read `GRAVES_SOURCE`, `GRAVES_TARGET`, `GRAVES_MODE` and `GRAVES_TAG`; draft upload also requires `GRAVES_RELEASE_ID` and `GRAVES_RELEASE_REPO`. Fetch writes `sha` to `GITHUB_OUTPUT`, reads `RELEASES_TOKEN` for downloads and an optional API endpoint from `GITHUB_API_URL`, for example of a mock server. Telegram reads `GRAVES_TAG` and files in `.graves/notes` and `.graves/out`. The repository root is resolved from the current directory with Git, including when invoked through `go -C golang run`. Missing/unknown subcommands return 2; runtime errors return 1.
