#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore ldflags trimpath goproxy modzip gcflags asmflags
# cspell:ignore GOTOOLCHAIN GOPATH modcache
# cspell:ignore duplicacy gilbertchen xargs GOARCH lzma gomod endgroup GOARM riscv ppc
MODULE='duplicacy.invalid/v2'
OUT_DIR="${GRAVES_OUT_DIR:?}"
FILE_VERSION="${GRAVES_FILE_VERSION:?}"
GO_VERSION="v${GRAVES_VERSION:?}"
MODZIP_DIR="${GRAVES_SOURCE_DIR:?}/scripts/modzip"
# Go 1.12.9 built the original 2.7.2 release: a Go 1.21+ go command downloads it and
# runs it instead of itself, and GOTOOLCHAIN=local returns to that command for modzip
export GOTOOLCHAIN='go1.12.9'
# Go 1.12 resolves modules through VCS by default and accepts no list of proxies
export GO111MODULE='on' GOPROXY='https://proxy.golang.org'
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
  tree="$(pwd)"
  gopath="$(go env GOPATH)"
  modcache="${gopath%%:*}/pkg/mod"
  platforms=(
    linux/amd64 linux/arm64 linux/386 linux/arm linux/ppc64le
    darwin/amd64 windows/amd64 windows/386 freebsd/amd64
  )
  for platform in "${platforms[@]}"; do
    os="${platform%/*}"
    arch="${platform#*/}"
    name="duplicacy-${FILE_VERSION}-${os}-${arch}"
    ext=''
    if [[ "${os}" == 'windows' ]]; then
      ext='.exe'
    fi
    step_begin "go build ${platform}"
    # GOARM only affects linux/arm and is ignored by every other GOARCH. Go 1.12 has no
    # -trimpath, so the compiler and the assembler trim the module cache everywhere and
    # the tree in the module packages, where the later pattern wins
    CGO_ENABLED=0 GOOS="${os}" GOARCH="${arch}" GOARM=7 go build -o \
      "${OUT_DIR}/${name}${ext}" \
      -gcflags="all='-trimpath=${modcache}'" -asmflags="all='-trimpath=${modcache}'" \
      -gcflags="${MODULE}/...='-trimpath=${tree}'" \
      -asmflags="${MODULE}/...='-trimpath=${tree}'" \
      -ldflags "-s -w -X 'main.GitCommit=${sha} ${GRAVES_VERSION}'" ./duplicacy
    if [[ "${os}" == 'linux' || "${os}" == 'freebsd' ]]; then
      info="$(file -b "${OUT_DIR}/${name}")"
      echo "${info}"
      if [[ "${info}" != *'statically linked'* ]]; then
        echo "${name} is not statically linked" >&2
        exit 1
      fi
    fi
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
  GOTOOLCHAIN='local' go -C "${MODZIP_DIR}" run . "${MODULE}" "${GO_VERSION}" "$(pwd)" \
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
