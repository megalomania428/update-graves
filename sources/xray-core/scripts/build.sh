#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore xray xtls ldflags trimpath buildid endgroup GOARCH GOARM GOMIPS GOVERSION
# cspell:ignore softfloat geoip geosite riscv loong mips mipsle armv openbsd Vless Xtls
# cspell:ignore QUIC GOTOOLCHAIN geodata lzma
OUT_DIR="${GRAVES_OUT_DIR:?}"
FILE_VERSION="${GRAVES_FILE_VERSION:?}"
DOWNLOADS_DIR="${GRAVES_DOWNLOADS_DIR:?}"
# Go releases of the official v24.11.11 binaries and of their Windows 7 builds
GO_MAIN='go1.23.3'
GO_WIN7='go1.21.4'
LDFLAGS="-X github.com/xtls/xray-core/core.build=${GRAVES_VERSION:?} -s -w -buildid="
# Tests that need the Internet: name servers of app/dns and REALITY with www.google.com
NET_TESTS=(
  './app/dns TestDOHNameServer'
  './app/dns TestDOHNameServerWithCache'
  './app/dns TestDOHNameServerWithIPv4Override'
  './app/dns TestDOHNameServerWithIPv6Override'
  './app/dns TestLocalNameServer'
  './app/dns TestQUICNameServer'
  './app/dns TestQUICNameServerWithIPv4Override'
  './app/dns TestQUICNameServerWithIPv6Override'
  './app/dns TestTCPLocalNameServer'
  './app/dns TestTCPLocalNameServerWithCache'
  './app/dns TestTCPLocalNameServerWithIPv4Override'
  './app/dns TestTCPLocalNameServerWithIPv6Override'
  './testing/scenarios TestVlessXtlsVisionReality'
)
# <file suffix> <GOOS> <GOARCH> [<VAR=value>...] in the order of the upstream matrix
PLATFORMS=(
  'windows-amd64 windows amd64'
  'windows-386 windows 386'
  'freebsd-amd64 freebsd amd64'
  'freebsd-386 freebsd 386'
  'openbsd-amd64 openbsd amd64'
  'openbsd-386 openbsd 386'
  'linux-amd64 linux amd64'
  'linux-386 linux 386'
  'darwin-amd64 darwin amd64'
  'darwin-arm64 darwin arm64'
  'linux-armv7 linux arm GOARM=7'
  'linux-armv6 linux arm GOARM=6'
  'linux-armv5 linux arm GOARM=5'
  'android-arm64 android arm64'
  'windows-arm64 windows arm64'
  'windows-armv7 windows arm GOARM=7'
  'linux-arm64 linux arm64'
  'linux-riscv64 linux riscv64'
  'linux-loong64 linux loong64'
  'linux-mips64 linux mips64'
  'linux-mips64le linux mips64le'
  'linux-mipsle linux mipsle'
  'linux-mipsle-softfloat linux mipsle GOMIPS=softfloat'
  'linux-mips linux mips'
  'linux-mips-softfloat linux mips GOMIPS=softfloat'
  'linux-ppc64 linux ppc64'
  'linux-ppc64le linux ppc64le'
  'freebsd-arm64 freebsd arm64'
  'freebsd-armv7 freebsd arm GOARM=7'
  'linux-s390x linux s390x'
  'openbsd-arm64 openbsd arm64'
  'openbsd-armv7 openbsd arm GOARM=7'
  "windows7-amd64 windows amd64 GOTOOLCHAIN=${GO_WIN7}"
  "windows7-386 windows 386 GOTOOLCHAIN=${GO_WIN7}"
)
# jscpd:ignore-start
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
# jscpd:ignore-end
check_go() {
  local got
  got="$(GOTOOLCHAIN="$1" go env GOVERSION)"
  echo "GOTOOLCHAIN=$1 runs ${got}"
  if [[ "${got}" != "$1" ]]; then
    echo "GOTOOLCHAIN=$1 runs ${got} instead of $1" >&2
    exit 1
  fi
}
trap step_fail EXIT
export GOTOOLCHAIN="${GO_MAIN}"
mkdir -p "${OUT_DIR}"
cd "${GRAVES_UPSTREAM_DIR:?}"
case "${STAGE}" in
prepare)
  step_begin 'go toolchains'
  check_go "${GO_MAIN}"
  check_go "${GO_WIN7}"
  step_end
  step_begin 'go mod download'
  go mod download
  step_end
  step_begin 'geodata'
  mkdir -p resources
  cp -f "${DOWNLOADS_DIR}/geoip.dat" resources/geoip.dat
  cp -f "${DOWNLOADS_DIR}/dlc.dat" resources/geosite.dat
  step_end
  ;;
test)
  net_names=("${NET_TESTS[@]#* }")
  printf -v net_skip '|%s' "${net_names[@]}"
  net_skip="^(${net_skip#|})$"
  step_begin 'go test without network tests'
  go test -count=1 -timeout 1h -v -skip "${net_skip}" ./...
  step_end
  step_begin 'network tests'
  failed=()
  for entry in "${NET_TESTS[@]}"; do
    read -r pkg name <<<"${entry}"
    if ! go test -count=1 -timeout 1h -v -run "^${name}$" "${pkg}"; then
      failed+=("${name}")
    fi
  done
  if [[ "${#failed[@]}" -gt 0 ]]; then
    echo "::warning::${STAGE}: network tests failed, the build goes on: ${failed[*]}"
  fi
  step_end
  ;;
bin)
  for platform in "${PLATFORMS[@]}"; do
    read -r -a fields <<<"${platform}"
    suffix="${fields[0]}"
    os="${fields[1]}"
    arch="${fields[2]}"
    extra=("${fields[@]:3}")
    want="${GO_MAIN}"
    if [[ "${suffix}" == windows7-* ]]; then
      want="${GO_WIN7}"
    fi
    name="xray-${FILE_VERSION}-${suffix}"
    ext=''
    if [[ "${os}" == 'windows' ]]; then
      ext='.exe'
    fi
    step_begin "go build ${suffix}"
    # GOARM and GOMIPS are empty unless the platform sets them
    env CGO_ENABLED=0 GOOS="${os}" GOARCH="${arch}" GOARM='' GOMIPS='' "${extra[@]}" \
      go build -trimpath -o "${OUT_DIR}/${name}${ext}" -ldflags "${LDFLAGS}" ./main
    built="$(go version "${OUT_DIR}/${name}${ext}")"
    echo "${built}"
    if [[ "${built}" != *": ${want}" ]]; then
      echo "${name}${ext} is not built by ${want}" >&2
      exit 1
    fi
    # jscpd:ignore-start
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
    # jscpd:ignore-end
  done
  ;;
*)
  echo 'usage: build.sh <prepare|test|bin>' >&2
  exit 1
  ;;
esac
