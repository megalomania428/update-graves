#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore ldflags trimpath goproxy modzip
# cspell:ignore duplicacy gilbertchen xargs GOARCH lzma gomod endgroup
MODULE='duplicacy.invalid/v3'
OUT_DIR="${GRAVES_OUT_DIR:?}"
FILE_VERSION="${GRAVES_FILE_VERSION:?}"
GO_VERSION="v${GRAVES_VERSION:?}"
MODZIP_DIR="${GRAVES_SOURCE_DIR:?}/scripts/modzip"
STAGE="${1:-}"
STEP=''
STEP_START=0
step_begin() {
  STEP="$1"
  STEP_START="${SECONDS}"
  echo "::group::${STAGE}: ${STEP} (started $(date -u '+%H:%M:%S') UTC)"
}
step_end() {
  echo '::endgroup::'
  echo "${STAGE}: ${STEP} done in $((SECONDS - STEP_START))s"
  STEP=''
}
step_fail() {
  local code=$?
  if [[ -n "${STEP}" ]]; then
    echo '::endgroup::'
    echo "::error::${STAGE}: ${STEP} failed after $((SECONDS - STEP_START))s"
  fi
  exit "${code}"
}
trap step_fail EXIT
mkdir -p "${OUT_DIR}"
cd "${GRAVES_UPSTREAM_DIR:?}"
case "${STAGE}" in
prepare)
  step_begin 'rename module'
  go mod edit -module "${MODULE}"
  grep -rlZ --include='*.go' 'github.com/gilbertchen/duplicacy/src' . |
    xargs -0 sed -i "s#github.com/gilbertchen/duplicacy/src#${MODULE}/src#g"
  step_end
  ;;
test)
  step_begin 'go test ./src'
  go test -count=1 -v ./src
  step_end
  step_begin 'go test ./duplicacy'
  go test -count=1 ./duplicacy
  step_end
  step_begin 'go build duplicacy_main'
  go build -o duplicacy_main ./duplicacy
  step_end
  zone="$(mktemp -d)"
  for name in test copy_test fixed_test resume_test sparse_test threaded_test; do
    step_begin "integration test ${name}"
    (cd integration_tests && TEST_ZONE="${zone}/${name}" bash "${name}.sh")
    step_end
  done
  step_begin 'cleanup'
  rm -rf "${zone}" duplicacy_main
  step_end
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
    step_begin "go build ${platform}"
    CGO_ENABLED=0 GOOS="${os}" GOARCH="${arch}" go build -trimpath -o \
      "${OUT_DIR}/${name}${ext}" \
      -ldflags "-s -w -X 'main.GitCommit=${sha} ${GRAVES_VERSION}'" ./duplicacy
    step_end
    step_begin "archive ${name}"
    (
      cd "${OUT_DIR}"
      if [[ "${os}" == 'windows' ]]; then
        7z a -t7z -m0=lzma2 -mx=9 "${name}.7z" "${name}${ext}"
      else
        tar --use-compress-program='xz -T0 -9e' -cf "${name}.txz" "${name}"
      fi
      rm -f "${name}${ext}"
    )
    step_end
  done
  ;;
gomod)
  step_begin 'clean tree'
  rm -rf duplicacy_paper.pdf images integration_tests
  git status --short
  step_end
  work="$(mktemp -d)"
  proxy="duplicacy-${FILE_VERSION}-goproxy"
  step_begin 'module zip'
  go -C "${MODZIP_DIR}" run . "${MODULE}" "${GO_VERSION}" "$(pwd)" \
    "${work}/${proxy}" "$(git log -1 --format=%cI)"
  step_end
  step_begin "archive ${proxy}"
  tar -C "${work}" --use-compress-program='xz -T0 -9e' \
    -cf "${OUT_DIR}/${proxy}.txz" "${proxy}"
  rm -rf "${work}"
  step_end
  ;;
*)
  echo 'usage: build.sh <prepare|test|bin|gomod>' >&2
  exit 1
  ;;
esac
