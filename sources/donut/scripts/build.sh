#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore subuid subgid subuids subgids newuidmap newgidmap uidmap apparmor userns
# cspell:ignore ghcup dotnet hostedtoolcache setuid rustup xwin
# Runs on the runner: builds the toolchain image of GRAVES_TARGET and runs
# scripts/container.sh in it with the repository and the caches mounted.
root="${GRAVES_SOURCE_DIR:?}"
root="${root%/sources/*}"
case "${GRAVES_TARGET:?}" in
lnx-app)
  base='mirror.gcr.io/ubuntu:22.04'
  toolchain='linux'
  ;;
win-prt | win-set)
  base='mirror.gcr.io/ubuntu:24.04'
  toolchain='windows'
  ;;
*)
  echo "unsupported GRAVES_TARGET: ${GRAVES_TARGET}" >&2
  exit 1
  ;;
esac
# A cold build keeps the image, the target tree, the toolchains and the cache archive
# on the root filesystem, so preinstalled software the build never uses is removed
# when the runner has less free space than that.
free_gib() {
  echo $(($(df --output=avail -k / | tail -n 1) / 1024 / 1024))
}
if [[ "$(free_gib)" -lt 40 ]]; then
  sudo rm -rf /usr/local/lib/android /usr/share/dotnet /usr/share/swift /opt/ghc \
    /usr/local/.ghcup /opt/hostedtoolcache/CodeQL
fi
df -h /
if [[ "$(free_gib)" -lt 20 ]]; then
  echo "less than 20 GiB free on /, the build would run out of disk space" >&2
  exit 1
fi
bash "${GRAVES_SOURCE_DIR}/scripts/host.sh"
pnpm_version="$(jq -r '.packageManager' "${GRAVES_UPSTREAM_DIR:?}/package.json")"
pnpm_version="${pnpm_version#pnpm@}"
if [[ ! "${pnpm_version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "unsupported packageManager pnpm version: ${pnpm_version}" >&2
  exit 1
fi
image="localhost/donutbrowser-${toolchain}:local" # DevSkim: ignore DS162092
podman build --network host --build-arg "BASE_IMAGE=${base}" \
  --build-arg "TOOLCHAIN=${toolchain}" -f Dockerfile -t "${image}" .
# Separate mounts, not one: these directories are the cache.paths of build.yaml.
cache="${HOME}/.cache/donutbrowser"
mkdir -p "${cache}/cargo/registry" "${cache}/node" "${cache}/pnpm-store" \
  "${cache}/rustup" "${cache}/xdg" "${cache}/xwin"
podman run --rm --network host \
  -v "${root}:${root}" \
  -v "${cache}/cargo/registry:/usr/local/cargo/registry" \
  -v "${cache}/node:/cache/node" \
  -v "${cache}/pnpm-store:/cache/pnpm-store" \
  -v "${cache}/rustup:/cache/rustup" \
  -v "${cache}/xdg:/cache/xdg" \
  -v "${cache}/xwin:/cache/xwin" \
  -w "${GRAVES_UPSTREAM_DIR}" \
  --env 'GRAVES_*' -e CI=true -e "PNPM_VERSION=${pnpm_version}" \
  "${image}" bash "${GRAVES_SOURCE_DIR}/scripts/container.sh"
