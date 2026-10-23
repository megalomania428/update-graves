#!/usr/bin/env bash
set -euo pipefail
cd "${GRAVES_UPSTREAM_DIR:?}"
npm ci --include=dev
# Run the whole upstream suite on the patched sources before the package metadata
# changes, so a failing test stops the build before anything is built.
npm test
# graves provides a ready semver in GRAVES_VERSION and the file-name form in
# GRAVES_FILE_VERSION, so this script does no version parsing.
npm pkg set 'name=@raven428/pi-web' "version=${GRAVES_VERSION:?}" 'repository.type=git' \
  'repository.url=git+https://github.com/megalomania428/update-graves.git'
npm run typecheck
npm run build
mkdir -p "${GRAVES_OUT_DIR:?}"
# npm pack prints the created file name on stdout and the listing on stderr.
tarball="$(npm pack --ignore-scripts --pack-destination "${GRAVES_OUT_DIR}" | tail -n1)"
mv -v "${GRAVES_OUT_DIR}/${tarball}" \
  "${GRAVES_OUT_DIR}/pi-web-${GRAVES_FILE_VERSION:?}.tgz"
