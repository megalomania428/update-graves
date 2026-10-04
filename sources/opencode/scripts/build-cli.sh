#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore vite lzma
export PATH="${HOME}/.bun/bin:${PATH}"
REPO_DIR="${GRAVES_UPSTREAM_DIR:?}"
OUT_DIR="${GRAVES_OUT_DIR:?}"
# upstream build scripts expect a plain semver, which graves already provides
export OPENCODE_VERSION="${GRAVES_VERSION:?}"
echo "build [${OPENCODE_VERSION}] version"
# Install husky globally
sudo "${HOME}/.bun/bin/bun" install --production --cwd /usr/local husky
sudo ln -sfv /usr/local/node_modules/husky/bin.js /usr/local/bin/husky
# Install workspace deps
bun install --production --cwd "${REPO_DIR}"
# `vite` and other devDeps of packages/app are needed to build the embedded
# WebUI bundle (createEmbeddedWebUIBundle in packages/opencode/script/build.ts),
# so pull them separately without --production.
bun install --cwd "${REPO_DIR}/packages/app"
# Build CLI targets
bun run --cwd "${REPO_DIR}/packages/opencode" script/build.ts
mkdir -p "${OUT_DIR}"
FILE_VERSION="${GRAVES_FILE_VERSION:?}"
case "${GRAVES_TARGET:?}" in
windows)
  name="opencode-${FILE_VERSION}-windows-x64"
  cp -v "${REPO_DIR}/packages/opencode/dist/opencode-windows-x64/bin/opencode.exe" \
    "${OUT_DIR}/${name}.exe"
  (
    cd "${OUT_DIR}"
    7z a -t7z -m0=lzma2 -mx=9 "${name}.7z" "${name}.exe"
    rm -f "${name}.exe"
  )
  ;;
linux)
  name="opencode-${FILE_VERSION}-linux-x64"
  cp -v "${REPO_DIR}/packages/opencode/dist/opencode-linux-x64/bin/opencode" \
    "${OUT_DIR}/${name}"
  (
    cd "${OUT_DIR}"
    tar --use-compress-program='xz -T0 -9e' -cf "${name}.txz" "${name}"
    rm -f "${name}"
  )
  ;;
*)
  echo "unsupported GRAVES_TARGET: ${GRAVES_TARGET}" >&2
  exit 1
  ;;
esac
echo "CLI archives ready in ${OUT_DIR}"
