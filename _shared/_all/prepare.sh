#!/usr/bin/env bash
set -euo pipefail

# Shared by every recipe that declares it: the packages any build and packaging
# step needs. Recipe-specific toolchains belong in sources/<name>/scripts/.

sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends ca-certificates curl git unzip \
  zip xz-utils 7zip
