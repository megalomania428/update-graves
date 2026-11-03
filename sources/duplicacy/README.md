# Duplicacy with patches

<!-- markdownlint-disable MD013 -->
<!-- cspell:ignore duplicacy gilbertchen goproxy GONOSUMDB GOPRIVATE GONOPROXY dpx Keychain xattr setfattr Setxattr pipefail GOARM riscv ppc goroutines errorf -->

Patched build of the [Duplicacy](https://github.com/gilbertchen/duplicacy) CLI, distributed in [GitHub Releases](https://github.com/megalomania428/update-graves/releases) as binaries and as a file-based GOPROXY with the Go module `duplicacy.invalid/v3`. Upstream has no tag for 3.3.0, so `build.yaml` pins commit `c8530c4d0fd4b607fd3e0fd98db359f6d7a15a19` with `version: 3.3.0`, `patch: 2` and `patch_width: 3`: the release version is `3.3.0-p002` and the Go module version is `v3.3.0-p002`. Pull requests and manual builds use `3.3.0-p002.dev.<run>.<attempt>` and the shared `v999` draft. Every build runs all upstream Go tests and integration tests before packaging, and one target publishes nothing if any of them fails.

A release has these assets, where `<version>` is the file-name form `3_3_0-p002`:

- `duplicacy-<version>-<os>-<arch>.txz` for `linux-amd64`, `linux-arm64`, `linux-386`, `linux-arm` (ARMv7, `GOARM=7`), `linux-riscv64`, `linux-ppc64le`, `darwin-amd64`, `darwin-arm64`, `freebsd-amd64` and `freebsd-arm64` – one binary named like the archive without the extension; the Linux and FreeBSD binaries are fully static, and the build fails if `file` does not report them as statically linked;
- `duplicacy-<version>-windows-amd64.7z` and `duplicacy-<version>-windows-386.7z` – the same binary for Windows with `.exe`;
- `duplicacy-<version>-goproxy.txz` – the `duplicacy-<version>-goproxy` directory of the file-based GOPROXY.

Installation on Linux x64:

```bash
curl -fsSL 'https://github.com/megalomania428/update-graves/releases/download/duplicacy-v3.3.0p002/duplicacy-3_3_0-p002-linux-amd64.txz' | tar -xJ -C /tmp
install -m 0755 /tmp/duplicacy-3_3_0-p002-linux-amd64 ~/.local/bin/duplicacy
```

`duplicacy -h` shows `VERSION: 3.3.0 (c8530c4 3.3.0-p002)`: the upstream version, the short upstream commit and the recipe version. All binaries are built with `CGO_ENABLED=0`, so the macOS binaries cannot use the Keychain: enter passwords interactively or pass them through `DUPLICACY_PASSWORD` and the other `DUPLICACY_*` environment variables.

## Go module

The module path is `duplicacy.invalid/v3`, and the package is imported as `duplicacy "duplicacy.invalid/v3/src"`. The `.invalid` domain is reserved by RFC 6761 and never resolves, so without the file proxy `go` fails at once instead of fetching an unrelated repository. The module requires Go 1.26.0 or newer.

```bash
gh release download duplicacy-v3.3.0p002 -R megalomania428/update-graves \
  -p 'duplicacy-*-goproxy.txz' -D /tmp/dpx
tar -C /tmp/dpx -xf /tmp/dpx/duplicacy-*-goproxy.txz
export GOPROXY="file://$(echo /tmp/dpx/duplicacy-*-goproxy),https://proxy.golang.org,direct"
export GONOSUMDB='duplicacy.invalid'
go get duplicacy.invalid/v3@v3.3.0-p002
```

Never set `GOPRIVATE` or `GONOPROXY` for `duplicacy.invalid`: they disable every proxy for the module, `file://` included. `GONOSUMDB` only skips the public checksum database, which cannot know this module.

## List of patches

- `001-linux-xattr-strict-tests` – `-exclude-by-attribute` works on Linux: the `user.duplicacy_exclude` attribute (`setfattr -n user.duplicacy_exclude <file>`) is matched with its namespace, as `github.com/pkg/xattr` returns it. `TestEntryExcludeByAttribute` sets the Linux attribute through `github.com/pkg/xattr`, the library of the production code, and fails on its errors: `github.com/gilbertchen/xattr` reports every successful `setxattr` on Linux as `errno 0`. Integration tests run under `set -euo pipefail` and accept `TEST_ZONE` from the environment; `resume_test.sh` no longer fails on the missing `file3`, requires its three deliberately failing backups to fail and uses files of up to 199 bytes, so a run with fewer than eleven chunks is practically impossible. `duplicacy_main.go` logs script output through a constant `%s` format, so the vet checks of `go test` pass with Go 1.26. `TestChunkOperator` sets the global logging level to `INFO` instead of `DEBUG`, which leaked TRACE and DEBUG output into every later test and flooded CI logs; a comment there explains switching back to `DEBUG` to investigate its failures and the alternative of a deferred reset to `INFO`.
- `002-worker-errors` – a panic of an upload task or of a listing goroutine of `Backup` no longer exits the process from its goroutine: it is recorded and raised again by `Backup` in the goroutine that called it, unless `Backup` has already returned `false` or panicked by itself, so the CLI exits with the same code while a program embedding the library can recover it. This covers chunk uploads of its `ChunkOperator`, which skip the queued uploads once an upload failure has been recorded and keep the first recorded failure even when `LogFunction` does not panic, while later ones are not raised, and the local and remote listing goroutines; after such a failure no goroutine of the backup is left behind, except a remote listing blocked by a failed download as described below. When `LogFunction` does not panic, `Backup` no longer blocks when the local listing can't read the top directory and returns `false`, or `true` with `enumOnly`, and it returns `false` instead of reporting success after an error that ends `UploadSnapshot` early, such as a failed upload of the snapshot file, which otherwise panics in the calling goroutine as before; a failed chunk upload is still raised and takes precedence. Failed downloads of the chunks of the previous snapshot behave as in upstream: they exit the process from the worker unless `LogFunction` returns, and when it returns, a failed chunk blocks `Backup` for the chunk or length sequence, and the remote listing for the file sequence, together with `Backup` if it still waits for a remote entry. The patch also fixes the five `go vet` findings of upstream: `net.JoinHostPort` for the SAMBA address, the timeout context of the Swift storage released when the storage can't be created, the `json` tags of `DataShards` and `ParityShards` written with double quotes and keeping their keys, and the discarded `fmt.Errorf` result in `CheckSnapshot`, whose entry check errors are still not returned, as in upstream.
