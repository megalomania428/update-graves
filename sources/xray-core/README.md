# Xray-core

<!-- markdownlint-disable MD013 -->
<!-- cspell:ignore xray xtls geoip geosite geodata softfloat riscv loong mips mipsle armv openbsd Vless Xtls trimpath buildid GOARM GOMIPS -->

Unpatched build of [Xray-core](https://github.com/XTLS/Xray-core), distributed in [GitHub Releases](https://github.com/megalomania428/update-graves/releases) as binaries. `build.yaml` pins tag `v24.11.11` with `patch: 0` and `patch_width: 3`: the release version is `24.11.11-p000`, pull requests and manual builds use `24.11.11-p000.dev.<run>.<attempt>`. The binaries are built like the official upstream release: `CGO_ENABLED=0`, `-trimpath`, `-s -w -buildid=`, Go 1.23.3, and Go 1.21.4 for Windows 7. `xray version` shows the upstream version `24.11.11` and then the recipe version in place of the upstream build name.

A release has these assets, where `<version>` is the file-name form `24_11_11-p000`, each archive with one binary named like the archive without the extension, plus `.exe` on Windows:

- `xray-<version>-<platform>.txz` for `linux-amd64`, `linux-386`, `linux-arm64`, `linux-armv7`, `linux-armv6`, `linux-armv5` (`GOARM` 7, 6 and 5), `linux-riscv64`, `linux-loong64`, `linux-mips64`, `linux-mips64le`, `linux-mips`, `linux-mipsle`, `linux-mips-softfloat`, `linux-mipsle-softfloat` (`GOMIPS=softfloat`), `linux-ppc64`, `linux-ppc64le`, `linux-s390x`, `android-arm64`, `darwin-amd64`, `darwin-arm64`, `freebsd-amd64`, `freebsd-386`, `freebsd-arm64`, `freebsd-armv7`, `openbsd-amd64`, `openbsd-386`, `openbsd-arm64` and `openbsd-armv7`; the Linux and FreeBSD binaries are fully static, and the build fails if `file` does not report them as statically linked;
- `xray-<version>-<platform>.7z` for `windows-amd64`, `windows-386`, `windows-arm64`, `windows-armv7`, `windows7-amd64` and `windows7-386` – the same binary with `.exe`, the `windows7-*` ones built by Go 1.21.4 for Windows 7.

Installation on Linux x64:

```bash
T="$(mktemp -d)" && curl -fsSL 'https://github.com/megalomania428/update-graves/releases/download/xray-core-v24.11.11p000/xray-24_11_11-p000-linux-amd64.txz' -o "${T}/xray.txz" \
  && tar -xJf "${T}/xray.txz" -C "${T}" && install -D -m 0755 "${T}/xray-24_11_11-p000-linux-amd64" ~/.local/bin/xray; rc=$?
rm -rf "${T}"; (exit "${rc}")
```

The archives carry no `geoip.dat` and `geosite.dat`. Configurations with `geoip:` or `geosite:` rules need them in the directory of the binary or, if `XRAY_LOCATION_ASSET` is set, in its directory instead of the directory of the binary; outside Windows Xray-core also looks in `/usr/local/share/xray`, `/usr/share/xray` and `/opt/share/xray`, in this order, when the file is not in the first directory.

Before packaging, every build runs all upstream Go tests on Linux x64 with Go 1.23.3 and the geodata pinned in `build.yaml`: `geoip.dat` of `v2fly/geoip` release `202411070052` and `dlc.dat` of `v2fly/domain-list-community` release `20241104071109` as `geosite.dat`. A failed test fails the build and publishes nothing, except the tests that need the Internet – the DNS name server tests of `app/dns` and `TestVlessXtlsVisionReality` – whose failures only add a warning to the build.

## List of patches

No patches yet: the upstream tag is built as is.
