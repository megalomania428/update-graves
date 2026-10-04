#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore vite metainfo NSIS

export PATH="$HOME/.bun/bin:$PATH"

REPO_DIR="${GRAVES_UPSTREAM_DIR:?}"
OUT_DIR="${GRAVES_OUT_DIR:?}"

DESKTOP_DIR="$REPO_DIR/packages/desktop"

export OPENCODE_CHANNEL="${OPENCODE_CHANNEL:-prod}"
# prepare.ts writes Script.version into package.json; graves already provides
# a plain semver, so no parsing happens here.
export OPENCODE_VERSION="${GRAVES_VERSION:?}"

# CLI node bundle is wired by electron-vite itself (virtual:opencode-server)
# and rebuilt from source by prepare.ts below; no prebuilt CLI binary from
# build-cli.sh's output dir is copied around anymore.

# devDeps are required here: electron, electron-builder, electron-vite, vite
case "${GRAVES_TARGET:-}" in
linux | windows) ;;
*)
  echo "unsupported GRAVES_TARGET: ${GRAVES_TARGET:-unset}" >&2
  exit 1
  ;;
esac

if [[ "$GRAVES_TARGET" == 'windows' ]]; then
  bun install --cwd "$DESKTOP_DIR" --os=win32 --cpu=x64
else
  bun install --cwd "$DESKTOP_DIR"
fi

# icons/metainfo for the channel + CLI node bundle in packages/opencode/dist/node
bun run --cwd "$DESKTOP_DIR" scripts/prepare.ts

# electron-vite build -> packages/desktop/out/
bun run --cwd "$DESKTOP_DIR" build

mkdir -p "$OUT_DIR"
FILE_VERSION="${GRAVES_FILE_VERSION:?}"

if [[ "$GRAVES_TARGET" == 'linux' ]]; then
  (
    cd "$DESKTOP_DIR"
    npx electron-builder --linux --x64 --publish never --config electron-builder.config.ts
  )
  # electron-builder names files as opencode-desktop-linux-{arch}.{ext};
  # graves publishes GRAVES_OUT_DIR as is, so the version is added here.
  cp -v "$DESKTOP_DIR/dist/opencode-desktop-linux-x86_64.AppImage" \
    "$OUT_DIR/opencode-desktop-${FILE_VERSION}-linux-x86_64.AppImage"
  cp -v "$DESKTOP_DIR/dist/opencode-desktop-linux-amd64.deb" \
    "$OUT_DIR/opencode-desktop-${FILE_VERSION}-linux-amd64.deb"
  cp -v "$DESKTOP_DIR/dist/opencode-desktop-linux-x86_64.rpm" \
    "$OUT_DIR/opencode-desktop-${FILE_VERSION}-linux-x86_64.rpm"
elif [[ "$GRAVES_TARGET" == 'windows' ]]; then
  # Cross-build on Ubuntu: NSIS target needs wine, nothing else
  (
    cd "$DESKTOP_DIR"
    npx electron-builder --win --x64 --publish never --config electron-builder.config.ts
  )
  # electron-builder's ${os} placeholder resolves to "win" for Windows,
  # unlike "linux" above which matches our own naming already.
  cp -v "$DESKTOP_DIR/dist/opencode-desktop-win-x64.exe" \
    "$OUT_DIR/opencode-desktop-${FILE_VERSION}-windows-x64.exe"
else
  echo "unsupported GRAVES_TARGET: $GRAVES_TARGET" >&2
  exit 1
fi

echo "Desktop artifacts ready in $OUT_DIR"

# Keep the CI cache lean: unpacked app trees and installers are recreated
# on every run anyway.
rm -rf "$DESKTOP_DIR/dist" "$DESKTOP_DIR/out"
