#!/usr/bin/env bash
set -euo pipefail
cd "${GRAVES_UPSTREAM_DIR:?}"
npm ci --include=dev
# Unit and integration suites run on the patched sources before the package
# metadata changes; the native Pi and isolated pi-web tests skip themselves
# without their opt-in environment.
npm run test:all
# graves provides a ready semver in GRAVES_VERSION and the file-name form in
# GRAVES_FILE_VERSION, so this script does no version parsing.
npm pkg set 'name=@raven428/pi-subagents' "version=${GRAVES_VERSION:?}" \
  'repository.type=git' \
  'repository.url=git+https://github.com/megalomania428/update-graves.git'
npm run typecheck
# The published package is the compiled dist-pkg tree, as upstream publishes it.
npm run build:pkg
mkdir -p "${GRAVES_OUT_DIR:?}"
# npm pack prints the created file name on stdout and the listing on stderr.
tarball="$(npm pack --ignore-scripts --pack-destination "${GRAVES_OUT_DIR}" ./dist-pkg |
  tail -n1)"
mv -v "${GRAVES_OUT_DIR}/${tarball}" \
  "${GRAVES_OUT_DIR}/pi-subagents-${GRAVES_FILE_VERSION:?}.tgz"
