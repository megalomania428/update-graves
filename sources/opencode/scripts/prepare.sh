#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore rustup NSIS rcedit multiarch

# Run on every build job (not cached).
# Installs the opencode toolchain that can't live in
# ~/.cargo / ~/.bun / ~/.rustup / ~/.cache (apt packages).
# Common packages come from _shared/_all/prepare.sh, which runs first.

export PATH="$HOME/.bun/bin:$PATH"

# Install Bun
if ! command -v bun &>/dev/null; then
  curl -fsSL https://bun.sh/install | bash
fi

# System deps for electron-builder:
# - rpm is required to build .rpm packages on Ubuntu
sudo apt-get install -y --no-install-recommends rpm

if [[ "${GRAVES_TARGET:-}" == 'windows' ]]; then
  # electron-builder ships winCodeSign with rcedit-ia32.exe (32-bit PE); wine
  # must have i386/wow64 support or that step fails with "wine32 is missing".
  # `wine` on Ubuntu 24.04 is 64-bit only by default, so enable i386 multiarch
  # and pull the 32-bit runtime explicitly.
  sudo dpkg --add-architecture i386
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends wine wine32:i386
fi
