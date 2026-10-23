# Duplicacy with patches

<!-- markdownlint-disable MD013 -->
<!-- cspell:ignore duplicacy gilbertchen goproxy GONOSUMDB GOPRIVATE GONOPROXY dpx Keychain xattr setfattr Setxattr pipefail GOARM riscv ppc -->

Patched build of the [Duplicacy](https://github.com/gilbertchen/duplicacy) CLI, distributed in [GitHub Releases](https://github.com/megalomania428/update-graves/releases) as binaries and as a file-based GOPROXY with the Go module `duplicacy.invalid/v3`. Upstream has no tag for 3.3.0, so `build.yaml` pins commit `c8530c4d0fd4b607fd3e0fd98db359f6d7a15a19` with `version: 3.3.0`, `patch: 1` and `patch_width: 3`: the release version is `3.3.0-p001` and the Go module version is `v3.3.0-p001`. Pull requests and manual builds use `3.3.0-p001.dev.<run>.<attempt>` and the shared `v999` draft. Every build runs all upstream Go tests and integration tests before packaging, and one target publishes nothing if any of them fails.

A release has these assets, where `<version>` is the file-name form `3_3_0-p001`:

- `duplicacy-<version>-<os>-<arch>.txz` for `linux-amd64`, `linux-arm64`, `linux-386`, `linux-arm` (ARMv7, `GOARM=7`), `linux-riscv64`, `linux-ppc64le`, `darwin-amd64`, `darwin-arm64`, `freebsd-amd64` and `freebsd-arm64` – one binary named like the archive without the extension; the Linux and FreeBSD binaries are fully static, and the build fails if `file` does not report them as statically linked;
- `duplicacy-<version>-windows-amd64.7z` and `duplicacy-<version>-windows-386.7z` – the same binary for Windows with `.exe`;
- `duplicacy-<version>-goproxy.txz` – the `duplicacy-<version>-goproxy` directory of the file-based GOPROXY.

Installation on Linux x64:

```bash
curl -fsSL 'https://github.com/megalomania428/update-graves/releases/download/duplicacy-v3.3.0p001/duplicacy-3_3_0-p001-linux-amd64.txz' | tar -xJ -C /tmp
install -m 0755 /tmp/duplicacy-3_3_0-p001-linux-amd64 ~/.local/bin/duplicacy
```

`duplicacy -h` shows `VERSION: 3.3.0 (c8530c4 3.3.0-p001)`: the upstream version, the short upstream commit and the recipe version. All binaries are built with `CGO_ENABLED=0`, so the macOS binaries cannot use the Keychain: enter passwords interactively or pass them through `DUPLICACY_PASSWORD` and the other `DUPLICACY_*` environment variables.

## Go module

The module path is `duplicacy.invalid/v3`, and the package is imported as `duplicacy "duplicacy.invalid/v3/src"`. The `.invalid` domain is reserved by RFC 6761 and never resolves, so without the file proxy `go` fails at once instead of fetching an unrelated repository. The module requires Go 1.26.0 or newer.

```bash
gh release download duplicacy-v3.3.0p001 -R megalomania428/update-graves \
  -p 'duplicacy-*-goproxy.txz' -D /tmp/dpx
tar -C /tmp/dpx -xf /tmp/dpx/duplicacy-*-goproxy.txz
export GOPROXY="file://$(echo /tmp/dpx/duplicacy-*-goproxy),https://proxy.golang.org,direct"
export GONOSUMDB='duplicacy.invalid'
go get duplicacy.invalid/v3@v3.3.0-p001
```

Never set `GOPRIVATE` or `GONOPROXY` for `duplicacy.invalid`: they disable every proxy for the module, `file://` included. `GONOSUMDB` only skips the public checksum database, which cannot know this module.

## List of patches

- `001-linux-xattr-strict-tests` – `-exclude-by-attribute` works on Linux: the `user.duplicacy_exclude` attribute (`setfattr -n user.duplicacy_exclude <file>`) is matched with its namespace, as `github.com/pkg/xattr` returns it. `TestEntryExcludeByAttribute` sets the Linux attribute through `github.com/pkg/xattr`, the library of the production code, and fails on its errors: `github.com/gilbertchen/xattr` reports every successful `setxattr` on Linux as `errno 0`. Integration tests run under `set -euo pipefail` and accept `TEST_ZONE` from the environment; `resume_test.sh` no longer fails on the missing `file3`, requires its three deliberately failing backups to fail and uses files of up to 199 bytes, so a run with fewer than eleven chunks is practically impossible. `duplicacy_main.go` logs script output through a constant `%s` format, so the vet checks of `go test` pass with Go 1.26. `TestChunkOperator` sets the global logging level to `INFO` instead of `DEBUG`, which leaked TRACE and DEBUG output into every later test and flooded CI logs; a comment there explains switching back to `DEBUG` to investigate its failures and the alternative of a deferred reset to `INFO`.
