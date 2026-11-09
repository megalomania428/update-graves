# Donut Browser with patches

<!-- cspell:ignore xwin msvc nsis winfsp unzip powershell -->
<!-- markdownlint-disable MD013 -->

[Donut Browser](https://github.com/donutbrowser/donutbrowser) is an anti-detect browser manager built with Tauri. This recipe builds patched Windows x64 and Linux x64 releases from the upstream ref in `build.yaml` and publishes them only to the private `megalomania428/update-bunker` repository.

Every target is a separate matrix job on `ubuntu-24.04`; the glibc baseline of the Linux AppImage comes from the image, not from the runner. `scripts/build.sh` frees disk space and sets up rootless Podman when the runner lacks them, builds a local Podman image from `Dockerfile` – `mirror.gcr.io/ubuntu:22.04` for Linux, `mirror.gcr.io/ubuntu:24.04` with LLVM, NSIS and cargo-xwin for Windows – and runs `scripts/container.sh` in it, which installs pinned Node, pnpm from `packageManager` of the upstream and the pinned Rust toolchain into cached mounts when they are missing. Windows targets are cross-built with cargo-xwin and clang-cl against the pinned MSVC CRT and Windows SDK, downloaded from Microsoft on the first build; by using them the build accepts the Microsoft license at <https://go.microsoft.com/fwlink/?LinkId=2086102>.

Each target publishes one asset, where `<version>` is `GRAVES_FILE_VERSION`:

- `lnx-app` – `donutbrowser-<version>-linux-x86_64.AppImage`;
- `win-set` – `donutbrowser-<version>-windows-x64-setup.exe`, the NSIS installer;
- `win-prt` – `donutbrowser-<version>-windows-x64-portable.7z` with the `donutbrowser-<version>-windows-x64-portable` directory of `Donut.exe`, `donut-proxy.exe`, `xray.exe`, `licenses/Xray-core-LICENSE.txt` and the `.portable` marker.

The portable directory also includes `donut-daemon.exe` and `WebView2Loader.dll` when the build produces them.

Node with pnpm, the Rust toolchain, the cargo registry, the pnpm store, the Tauri tools, the MSVC CRT and SDK of Windows and the Rust `target` tree of dependencies are cached under `~/.cache/donut` and `.graves/upstream/src-tauri/target`; own crates are always rebuilt, so the cached tree never carries an old version. On a Linux host Tauri finds no `NSIS.exe` in its cached `NSIS` directory, so `win-set` recreates it and downloads `nsis_tauri_utils.dll` from GitHub on every build.

## Patches

### `patches/common`

- `001-version-no-updater` – set version `0.35.5-p000` and disable the self-update check and download of the application

### `patches/windows`

- `002-windows-cross-build` – cross-build Windows on Linux: delay-load `winfsp-x64.dll` and pass the Common Controls v6 manifest dependency directly to the linker, and unzip Xray-core instead of calling PowerShell
