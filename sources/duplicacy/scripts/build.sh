#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore ldflags trimpath goproxy modzip
# cspell:ignore duplicacy gilbertchen xargs GOARCH lzma gomod
MODULE='duplicacy.invalid/v3'
OUT_DIR="${GRAVES_OUT_DIR:?}"
FILE_VERSION="${GRAVES_FILE_VERSION:?}"
GO_VERSION="v${GRAVES_VERSION:?}"
MODZIP_DIR="${GRAVES_SOURCE_DIR:?}/scripts/modzip"
mkdir -p "${OUT_DIR}"
cd "${GRAVES_UPSTREAM_DIR:?}"
case "${1:-}" in
prepare)
  go mod edit -module "${MODULE}"
  grep -rlZ --include='*.go' 'github.com/gilbertchen/duplicacy/src' . |
    xargs -0 sed -i "s#github.com/gilbertchen/duplicacy/src#${MODULE}/src#g"
  ;;
test)
  go test -count=1 ./...
  go build -o duplicacy_main ./duplicacy
  zone="$(mktemp -d)"
  for name in test copy_test fixed_test resume_test sparse_test threaded_test; do
    echo "integration test ${name}"
    (cd integration_tests && TEST_ZONE="${zone}/${name}" bash "${name}.sh")
  done
  rm -rf "${zone}" duplicacy_main
  ;;
bin)
  sha="$(git rev-parse --short HEAD)"
  for platform in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64; do
    os="${platform%/*}"
    arch="${platform#*/}"
    name="duplicacy-${FILE_VERSION}-${os}-${arch}"
    ext=''
    if [[ "${os}" == 'windows' ]]; then
      ext='.exe'
    fi
    CGO_ENABLED=0 GOOS="${os}" GOARCH="${arch}" go build -trimpath -o \
      "${OUT_DIR}/${name}${ext}" \
      -ldflags "-s -w -X 'main.GitCommit=${sha} ${GRAVES_VERSION}'" ./duplicacy
    (
      cd "${OUT_DIR}"
      if [[ "${os}" == 'windows' ]]; then
        7z a -t7z -m0=lzma2 -mx=9 "${name}.7z" "${name}${ext}"
      else
        tar --use-compress-program='xz -T0 -9e' -cf "${name}.txz" "${name}"
      fi
      rm -f "${name}${ext}"
    )
  done
  ;;
gomod)
  rm -rf duplicacy_paper.pdf images integration_tests
  git status --short
  work="$(mktemp -d)"
  proxy="duplicacy-${FILE_VERSION}-goproxy"
  go -C "${MODZIP_DIR}" run . "${MODULE}" "${GO_VERSION}" "$(pwd)" \
    "${work}/${proxy}" "$(git log -1 --format=%cI)"
  tar -C "${work}" --use-compress-program='xz -T0 -9e' \
    -cf "${OUT_DIR}/${proxy}.txz" "${proxy}"
  rm -rf "${work}"
  ;;
*)
  echo 'usage: build.sh <prepare|test|bin|gomod>' >&2
  exit 1
  ;;
esac
