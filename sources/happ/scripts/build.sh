#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore apktool zipalign apksigner nproc pkey pubout noout idsig
# cspell:ignore PYTHONDONTWRITEBYTECODE
TMP_DIR=''
stage_start() {
  printf '==> %s\n' "$1"
}
on_exit() {
  local code=$?
  if [[ -n "${TMP_DIR}" ]]; then
    rm -rf -- "${TMP_DIR}"
  fi
  exit "${code}"
}
trap on_exit EXIT
stage_start 'check tools and inputs'
APKTOOL="${GRAVES_DOWNLOADS_DIR:?}/apktool_3.0.3.jar"
INPUT_APK="${GRAVES_UPSTREAM_ASSET:?}"
DECODED_DIR="${GRAVES_UPSTREAM_DIR:?}/decoded"
OUT_DIR="${GRAVES_OUT_DIR:?}"
OUTPUT_APK="${OUT_DIR}/happ-${GRAVES_FILE_VERSION:?}.apk"
NEUTRALIZE="${GRAVES_SOURCE_DIR:?}/scripts/001-neutralize.py"
for command in java python3 openssl base64; do
  command -v "${command}" >/dev/null || {
    printf 'Required command is unavailable: %s\n' "${command}" >&2
    exit 127
  }
done
BUILD_TOOLS="$(find "${ANDROID_HOME:?}/build-tools" -mindepth 1 -maxdepth 1 -type d \
  -printf '%f\n' | sort -V | tail -n 1)"
if [[ -z "${BUILD_TOOLS}" ]]; then
  printf 'No Android build-tools in %s/build-tools\n' "${ANDROID_HOME}" >&2
  exit 1
fi
ZIPALIGN="${ANDROID_HOME}/build-tools/${BUILD_TOOLS}/zipalign"
APKSIGNER="${ANDROID_HOME}/build-tools/${BUILD_TOOLS}/apksigner"
for file in "${ZIPALIGN}" "${APKSIGNER}"; do
  [[ -x "${file}" ]] || {
    printf 'Required tool is unavailable: %s\n' "${file}" >&2
    exit 127
  }
done
for file in "${APKTOOL}" "${INPUT_APK}" "${NEUTRALIZE}"; do
  [[ -f "${file}" ]] || {
    printf 'Required file not found: %s\n' "${file}" >&2
    exit 1
  }
done
java -version
printf 'Android build-tools %s\n' "${BUILD_TOOLS}"
printf 'apksigner %s\n' "$("${APKSIGNER}" --version)"
stage_start 'unpack signing key and certificate'
TMP_DIR="$(mktemp -d "${RUNNER_TEMP:-/tmp}/happ-build.XXXXXXXX")"
(
  umask 077
  printf '%s' "${APK_SIGNING_KEY:?}" | base64 -d >"${TMP_DIR}/key.pk8"
  printf '%s\n' "${APK_SIGNING_CERT:?}" >"${TMP_DIR}/cert.pem"
)
KEY_PUBLIC="$(openssl pkey -inform DER -in "${TMP_DIR}/key.pk8" -pubout)"
CERT_PUBLIC="$(openssl x509 -in "${TMP_DIR}/cert.pem" -noout -pubkey)"
if [[ "${KEY_PUBLIC}" != "${CERT_PUBLIC}" ]]; then
  printf 'APK_SIGNING_KEY does not match the public key of APK_SIGNING_CERT\n' >&2
  exit 1
fi
printf 'Signing key matches the certificate\n'
stage_start "decode ${INPUT_APK##*/}"
java -jar "${APKTOOL}" decode --force --all-src --jobs "$(nproc)" \
  --output "${DECODED_DIR}" "${INPUT_APK}"
stage_start 'neutralize smali traps'
PYTHONDONTWRITEBYTECODE=1 python3 "${NEUTRALIZE}" "${DECODED_DIR}"
stage_start 'build unsigned APK'
java -jar "${APKTOOL}" build --force --jobs "$(nproc)" \
  --output "${TMP_DIR}/unsigned.apk" "${DECODED_DIR}"
stage_start 'align APK'
"${ZIPALIGN}" -f -p 4 "${TMP_DIR}/unsigned.apk" "${TMP_DIR}/aligned.apk"
stage_start "sign ${OUTPUT_APK##*/}"
mkdir -p -- "${OUT_DIR}"
rm -f -- "${OUTPUT_APK}" "${OUTPUT_APK}.idsig"
"${APKSIGNER}" sign --key "${TMP_DIR}/key.pk8" --cert "${TMP_DIR}/cert.pem" \
  --v4-signing-enabled false --out "${OUTPUT_APK}" "${TMP_DIR}/aligned.apk"
stage_start "verify ${OUTPUT_APK##*/}"
if ! "${ZIPALIGN}" -c -p 4 "${OUTPUT_APK}" ||
  ! "${APKSIGNER}" verify "${OUTPUT_APK}" >"${TMP_DIR}/verify.txt" 2>&1; then
  cat "${TMP_DIR}/verify.txt" >&2 || true
  rm -f -- "${OUTPUT_APK}"
  exit 1
fi
ASSETS="$(find "${OUT_DIR}" -mindepth 1 -maxdepth 1 | wc -l)"
if [[ "${ASSETS}" -ne 1 ]]; then
  printf 'Expected only %s in %s, found %s entries\n' "${OUTPUT_APK##*/}" \
    "${OUT_DIR}" "${ASSETS}" >&2
  exit 1
fi
printf 'APK signature verified successfully\nSigned APK: %s\n' "${OUTPUT_APK}"
