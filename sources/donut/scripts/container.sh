#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore nullglob msvc xwin nsis lzma libdonutbrowser appimage rustup rustc
# cspell:ignore RUSTFLAGS
# Runs in the toolchain image from the upstream clone: builds GRAVES_TARGET and
# leaves its only asset in GRAVES_OUT_DIR.
upstream="${GRAVES_UPSTREAM_DIR:?}"
out="${GRAVES_OUT_DIR:?}"
target_dir="${upstream}/src-tauri/target"
name="donutbrowser-${GRAVES_FILE_VERSION:?}"
# build.rs embeds BUILD_TAG as the application version.
export BUILD_TAG="v${GRAVES_VERSION:?}"
export STABLE_RELEASE=true
export PNPM_CONFIG_STORE_DIR=/cache/pnpm-store
export XDG_CACHE_HOME=/cache/xdg
shopt -s nullglob
# Node with pnpm and the Rust toolchain live in the cached mounts /cache/node and
# /cache/rustup, so a warm cache installs nothing.
node_version='26.13.0'
node_sha256="d28\
a96af6c38596f6d9d97c0ee531c13d238d757742387d1928de822d959e997" # DevSkim: ignore DS173237
rust_version='1.99.1'
node_dir="/cache/node/node-v${node_version}-linux-x64"
if [[ ! -x "${node_dir}/bin/node" ]]; then
  work="$(mktemp -d -p /cache/node)"
  curl -fsSLo "${work}/node.tar.xz" \
    "https://nodejs.org/dist/v${node_version}/node-v${node_version}-linux-x64.tar.xz"
  echo "${node_sha256}  ${work}/node.tar.xz" | sha256sum -c -
  tar -xJf "${work}/node.tar.xz" -C "${work}" --no-same-owner
  rm -rf "${node_dir}"
  mv "${work}/node-v${node_version}-linux-x64" "${node_dir}"
  rm -rf "${work}"
fi
export PATH="${node_dir}/bin:${PATH}"
if [[ "$(pnpm --version 2>/dev/null || true)" != "${PNPM_VERSION:?}" ]]; then
  npm install -g "pnpm@${PNPM_VERSION}"
fi
export RUSTUP_TOOLCHAIN="${rust_version}"
if ! rustup toolchain list | grep -q "^${rust_version}-x86_64-unknown-linux-gnu"; then
  rustup toolchain install "${rust_version}" --profile minimal --no-self-update
fi
if [[ "${GRAVES_TARGET:?}" != 'linux' ]]; then
  rustup target add x86_64-pc-windows-msvc
fi
rustc --version
# build.rs never reruns when BUILD_TAG changes, so the cached target trees keep
# dependencies only: own crates, sidecars and bundles are removed before the
# build and again before the cache is saved.
clean_own() {
  local dir
  for dir in "${target_dir}/release" "${target_dir}/x86_64-pc-windows-msvc/release"; do
    rm -rf "${dir}/bundle" "${dir}/nsis" "${dir}/licenses" "${dir}/incremental" \
      "${dir}"/donutbrowser* "${dir}"/libdonutbrowser* "${dir}"/donut-proxy* \
      "${dir}"/donut_proxy* "${dir}"/xray* "${dir}"/.fingerprint/donutbrowser-* \
      "${dir}"/build/donutbrowser-* "${dir}"/deps/donutbrowser* \
      "${dir}"/deps/libdonutbrowser* "${dir}"/deps/donut_proxy*
  done
  rm -rf "${target_dir}/appimage"
}
clean_own
mkdir -p "${out}"
pnpm install --frozen-lockfile
case "${GRAVES_TARGET:?}" in
lnx-app)
  # Rootless containers have no FUSE device, so run AppImage tools by extraction.
  export APPIMAGE_EXTRACT_AND_RUN=1
  pnpm tauri build --bundles appimage
  images=("${target_dir}/release/bundle/appimage/"*.AppImage)
  if [[ "${#images[@]}" -ne 1 ]]; then
    echo "expected one AppImage, found ${#images[@]}" >&2
    exit 1
  fi
  cp -v "${images[0]}" "${out}/${name}-linux-x86_64.AppImage"
  ;;
win-prt | win-set)
  export TARGET='x86_64-pc-windows-msvc'
  export CARGO_BUILD_TARGET="${TARGET}"
  # MSVC CRT and SDK pins; cargo-xwin checks only the architecture of its cache.
  export XWIN_CACHE_DIR=/cache/xwin XWIN_ARCH=x86_64 XWIN_VERSION=17
  export XWIN_CRT_VERSION='14.44.17.14' XWIN_SDK_VERSION='10.0.26100'
  # cargo-xwin also logs to stdout, so only its export lines are evaluated.
  xwin_env="$(cargo xwin env --target "${TARGET}")"
  xwin_exports="$(grep '^export ' <<<"${xwin_env}")"
  eval "${xwin_exports}"
  # The empty RUSTFLAGS export would override the target flags of cargo-xwin.
  unset RUSTFLAGS
  release="${target_dir}/${TARGET}/release"
  if [[ "${GRAVES_TARGET}" == 'win-set' ]]; then
    pnpm tauri build --bundles nsis
    setups=("${release}/bundle/nsis/"*-setup.exe)
    if [[ "${#setups[@]}" -ne 1 ]]; then
      echo "expected one NSIS installer, found ${#setups[@]}" >&2
      exit 1
    fi
    cp -v "${setups[0]}" "${out}/${name}-windows-x64-setup.exe"
  else
    pnpm tauri build --no-bundle
    # Same layout as the portable ZIP of the upstream release workflow.
    portable="${name}-windows-x64-portable"
    work="$(mktemp -d)"
    mkdir -p "${work}/${portable}/licenses"
    cp -v "${release}/donutbrowser.exe" "${work}/${portable}/Donut.exe"
    cp -v "${release}/donut-proxy.exe" "${work}/${portable}/donut-proxy.exe"
    binaries="${upstream}/src-tauri/binaries"
    cp -v "${binaries}/xray-${TARGET}.exe" "${work}/${portable}/xray.exe"
    cp -v "${binaries}/xray-LICENSE.txt" \
      "${work}/${portable}/licenses/Xray-core-LICENSE.txt"
    for optional in donut-daemon.exe WebView2Loader.dll; do
      if [[ -f "${release}/${optional}" ]]; then
        cp -v "${release}/${optional}" "${work}/${portable}/"
      fi
    done
    touch "${work}/${portable}/.portable"
    seven="$(command -v 7z || command -v 7zz)"
    (cd "${work}" && "${seven}" a -t7z -m0=lzma2 -mx=9 "${out}/${portable}.7z" \
      "${portable}")
    rm -rf "${work}"
  fi
  ;;
*)
  echo "unsupported GRAVES_TARGET: ${GRAVES_TARGET}" >&2
  exit 1
  ;;
esac
clean_own
