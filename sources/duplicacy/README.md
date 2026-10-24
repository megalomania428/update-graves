# Duplicacy with patches

<!-- markdownlint-disable MD013 -->
<!-- cspell:ignore duplicacy gilbertchen goproxy GONOSUMDB GOPRIVATE GONOPROXY dpx Keychain xattr setfattr Setxattr pipefail GOARM riscv ppc GOTOOLCHAIN Gopkg jmespath Hubic acdclient hubicclient oneclient -->

Patched build of the [Duplicacy](https://github.com/gilbertchen/duplicacy) CLI, distributed in [GitHub Releases](https://github.com/megalomania428/update-graves/releases) as binaries and as a file-based GOPROXY with the Go module `duplicacy.invalid/v2`. `build.yaml` pins the upstream tag `v2.7.2` with `patch: 1` and `patch_width: 3`: the release version is `2.7.2-p001` and the Go module version is `v2.7.2-p001`. Pull requests and manual builds use `2.7.2-p001.dev.<run>.<attempt>` and the shared `v999` draft. Every build runs all upstream Go tests and integration tests before packaging, and one target publishes nothing if any of them fails.

Like the original 2.7.2 release, the binaries are built and tested with Go 1.12.9: `scripts/build.sh` exports `GOTOOLCHAIN=go1.12.9`, so the Go 1.21+ `go` command of CI downloads that toolchain from the Go module proxy and runs it, and only the packaging of the file-based GOPROXY runs on the Go of CI itself. Go 1.12.9 cannot build `linux-riscv64`, `darwin-arm64` and `freebsd-arm64`, so a release has no binaries for them.

A release has these assets, where `<version>` is the file-name form `2_7_2-p001`:

- `duplicacy-<version>-<os>-<arch>.txz` for `linux-amd64`, `linux-arm64`, `linux-386`, `linux-arm` (ARMv7, `GOARM=7`), `linux-ppc64le`, `darwin-amd64` and `freebsd-amd64` – one binary named like the archive without the extension; the Linux and FreeBSD binaries are fully static, and the build fails if `file` does not report them as statically linked;
- `duplicacy-<version>-windows-amd64.7z` and `duplicacy-<version>-windows-386.7z` – the same binary for Windows with `.exe`;
- `duplicacy-<version>-goproxy.txz` – the `duplicacy-<version>-goproxy` directory of the file-based GOPROXY.

Installation on Linux x64:

```bash
curl -fsSL 'https://github.com/megalomania428/update-graves/releases/download/duplicacy-v2.7.2p001/duplicacy-2_7_2-p001-linux-amd64.txz' | tar -xJ -C /tmp
install -m 0755 /tmp/duplicacy-2_7_2-p001-linux-amd64 ~/.local/bin/duplicacy
```

`duplicacy -h` shows `VERSION: 2.7.2 (175adb1 2.7.2-p001)`: the upstream version, the short upstream commit and the recipe version. All binaries are built with `CGO_ENABLED=0`, so the macOS binary cannot use the Keychain: enter passwords interactively or pass them through `DUPLICACY_PASSWORD` and the other `DUPLICACY_*` environment variables.

## Go module

The module path is `duplicacy.invalid/v2`, and the package is imported as `duplicacy "duplicacy.invalid/v2/src"`. The `.invalid` domain is reserved by RFC 6761 and never resolves, so without the file proxy `go` fails at once instead of fetching an unrelated repository. The module declares `go 1.12`, and its five `replace` directives hold `cloud.google.com/go`, `github.com/jmespath/go-jmespath`, `golang.org/x/crypto`, `golang.org/x/net` and `github.com/googleapis/gax-go/v2` at the revisions of the upstream `Gopkg.lock` only in this build, because Go ignores `replace` outside the main module and a module that requires `duplicacy.invalid/v2` gets newer versions of them, for example `cloud.google.com/go v0.38.0`.

```bash
gh release download duplicacy-v2.7.2p001 -R megalomania428/update-graves \
  -p 'duplicacy-*-goproxy.txz' -D /tmp/dpx
tar -C /tmp/dpx -xf /tmp/dpx/duplicacy-*-goproxy.txz
export GOPROXY="file://$(echo /tmp/dpx/duplicacy-*-goproxy),https://proxy.golang.org,direct"
export GONOSUMDB='duplicacy.invalid'
go get duplicacy.invalid/v2@v2.7.2-p001
```

Never set `GOPRIVATE` or `GONOPROXY` for `duplicacy.invalid`: they disable every proxy for the module, `file://` included. `GONOSUMDB` only skips the public checksum database, which cannot know this module.

## List of patches

- `001-linux-xattr-strict-tests` – `-exclude-by-attribute` works on Linux: the `user.duplicacy_exclude` attribute (`setfattr -n user.duplicacy_exclude <file>`) is matched with its namespace, as `github.com/pkg/xattr` returns it. `TestEntryExcludeByAttribute` sets the Linux attribute through `github.com/pkg/xattr`, the library of the production code, and fails on its errors: `github.com/gilbertchen/xattr` reports every successful `setxattr` on Linux as `errno 0`. Integration tests run under `set -euo pipefail` and accept `TEST_ZONE` from the environment; `resume_test.sh` no longer fails on the missing `file3`, requires its three deliberately failing backups to fail and uses files of up to 199 bytes, so a run with fewer than eleven chunks is practically impossible. `duplicacy_main.go` logs script output through a constant `%s` format, so a `%` in the output of a script is printed as is. The patch also adds `go.mod` and `go.sum`, which upstream introduced only after 2.7.2: Go 1.12.9 `go mod init` copied the 45 requirements of `Gopkg.lock`, five `replace` directives keep the modules that minimal version selection would raise at their `Gopkg.lock` revisions, and `go.sum` covers the nine platforms and the test packages. It also deletes the ACD, B2, Hubic and OneDrive client tests `src/duplicacy_{acdclient,b2client,hubicclient,oneclient}_test.go`, which need credential files of real storages and fail without them, as upstream did after 2.7.2.
