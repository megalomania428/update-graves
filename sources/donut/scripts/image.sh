#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore rustup libayatana appindicator librsvg libwebkit libxdo libgtk libssl
# cspell:ignore libclang nsis xwin musl
# Runs inside podman build: installs the system packages of the TOOLCHAIN target kind,
# rustup without a toolchain and, for Windows, cargo-xwin. Node and the Rust toolchain
# are installed by scripts/container.sh into cached mounts.
rustup_version='1.29.3'
rustup_sha256="b1c\
8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55" # DevSkim: ignore DS173237
xwin_version='0.23.1'
xwin_sha256="c49\
2c6dfb7e5ac0eee586b796e0fc950ba13077e7f0bbb046f445d71790d5360" # DevSkim: ignore DS173237
packages=(libayatana-appindicator3-dev build-essential ca-certificates curl git unzip
  xz-utils)
case "${TOOLCHAIN:?}" in
linux)
  packages+=(file libgtk-3-dev librsvg2-dev libssl-dev
    libwebkit2gtk-4.1-dev libxdo-dev pkg-config wget xdg-utils)
  ;;
windows)
  packages+=(7zip clang libclang-dev lld llvm nsis)
  ;;
*)
  echo "unsupported TOOLCHAIN: ${TOOLCHAIN}" >&2
  exit 1
  ;;
esac
apt-get update
apt-get install -y --no-install-recommends "${packages[@]}"
rm -rf /var/lib/apt/lists/*
tmp="$(mktemp -d)"
rustup_url="https://static.rust-lang.org/rustup/archive/${rustup_version}"
curl -fsSLo "${tmp}/rustup-init" "${rustup_url}/x86_64-unknown-linux-gnu/rustup-init"
echo "${rustup_sha256}  ${tmp}/rustup-init" | sha256sum -c -
chmod 755 "${tmp}/rustup-init"
"${tmp}/rustup-init" -y --no-modify-path --profile minimal --default-toolchain none
if [[ "${TOOLCHAIN}" == 'windows' ]]; then
  xwin_url="https://github.com/rust-cross/cargo-xwin/releases/download/v${xwin_version}"
  xwin_asset="cargo-xwin-v${xwin_version}.x86_64-unknown-linux-musl.tar.gz"
  curl -fsSLo "${tmp}/${xwin_asset}" "${xwin_url}/${xwin_asset}"
  echo "${xwin_sha256}  ${tmp}/${xwin_asset}" | sha256sum -c -
  tar -xzf "${tmp}/${xwin_asset}" -C "${CARGO_HOME:?}/bin" --no-same-owner cargo-xwin
fi
rm -rf "${tmp}" "${RUSTUP_HOME:?}"
