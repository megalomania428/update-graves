#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore subuid subgid subuids subgids usermod newuidmap newgidmap uidmap
# cspell:ignore apparmor userns mktemp sysctl
scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
mkdir -p "${work}/bin"
cat >"${work}/bin/stub" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
command="${0##*/}"
if [[ "${command}" == 'sudo' ]]; then
  exec "$@"
fi
printf '%s\n' "${command} $*" >>"${CALLS}"
case "${command}" in
sysctl)
  if [[ "$1" == '-n' ]]; then
    echo 1
  fi
  ;;
id)
  [[ "$*" == '-un' ]]
  echo runner
  ;;
install)
  [[ "$#" == 8 && "$1 $2 $3 $4 $5 $6 $7" == \
    '-o root -g root -m 0644 /dev/null' ]]
  [[ ! -e "$8" ]]
  /usr/bin/install -m 0644 /dev/null "$8"
  ;;
usermod)
  [[ "$#" == 5 && "$1" == '--add-subuids' && "$3" == '--add-subgids' ]]
  [[ "$2" == "$4" && "$5" == 'runner' ]]
  [[ -f "${SUBUID_FILE}" && -f "${SUBGID_FILE}" ]] || exit 2
  start="${2%-*}"
  end="${2#*-}"
  for file in "${SUBUID_FILE}" "${SUBGID_FILE}"; do
    printf 'runner:%s:%s\n' "${start}" "$((end - start + 1))" >>"${file}"
  done
  ;;
podman)
  [[ "$*" == 'system migrate' || "$*" == \
    'info --format {{.Host.Security.Rootless}} {{.Store.GraphDriverName}}' ]]
  ;;
newuidmap | newgidmap)
  ;;
*)
  echo "unexpected command: ${command} $*" >&2
  exit 1
  ;;
esac
STUB
chmod +x "${work}/bin/stub"
for command in sudo sysctl id install usermod podman newuidmap newgidmap apt-get; do
  ln -s stub "${work}/bin/${command}"
done
export PATH="${work}/bin:${PATH}"
export SUBUID_FILE SUBGID_FILE CALLS
for scenario in no-subuid no-subgid neither both ready; do
  dir="${work}/${scenario}"
  mkdir -p "${dir}"
  SUBUID_FILE="${dir}/subuid"
  SUBGID_FILE="${dir}/subgid"
  CALLS="${dir}/calls"
  : >"${CALLS}"
  if [[ "${scenario}" != 'no-subuid' && "${scenario}" != 'neither' ]]; then
    printf 'other:200000:65536\n' >"${SUBUID_FILE}"
    chmod 0600 "${SUBUID_FILE}"
  fi
  if [[ "${scenario}" != 'no-subgid' && "${scenario}" != 'neither' ]]; then
    printf 'other:900000:65536\n' >"${SUBGID_FILE}"
    chmod 0640 "${SUBGID_FILE}"
  fi
  if [[ "${scenario}" == 'ready' ]]; then
    printf 'runner:400000:65536\n' >>"${SUBUID_FILE}"
    printf 'runner:500000:65536\n' >>"${SUBGID_FILE}"
  fi
  expected="${dir}/expected-calls"
  printf '%s\n' 'sysctl -n kernel.apparmor_restrict_unprivileged_userns' \
    'sysctl -w kernel.apparmor_restrict_unprivileged_userns=0' 'id -un' \
    >"${expected}"
  for file in "${SUBUID_FILE}" "${SUBGID_FILE}"; do
    if [[ -f "${file}" ]]; then
      cp "${file}" "${file}.expected"
      stat -c '%a' "${file}" >"${file}.mode"
    else
      : >"${file}.expected"
      echo 644 >"${file}.mode"
      echo "install -o root -g root -m 0644 /dev/null ${file}" >>"${expected}"
    fi
  done
  if [[ "${scenario}" != 'ready' ]]; then
    case "${scenario}" in
    neither) start=100000 ;;
    no-subgid) start=365536 ;;
    *) start=1065536 ;;
    esac
    end=$((start + 65535))
    echo "usermod --add-subuids ${start}-${end} --add-subgids ${start}-${end} runner" \
      >>"${expected}"
    echo 'podman system migrate' >>"${expected}"
    for file in "${SUBUID_FILE}" "${SUBGID_FILE}"; do
      printf 'runner:%s:65536\n' "${start}" >>"${file}.expected"
    done
  fi
  echo 'podman info --format {{.Host.Security.Rootless}} {{.Store.GraphDriverName}}' \
    >>"${expected}"
  bash "${scripts}/host.sh"
  diff -u "${expected}" "${CALLS}"
  for file in "${SUBUID_FILE}" "${SUBGID_FILE}"; do
    diff -u "${file}.expected" "${file}"
    [[ "$(stat -c '%a' "${file}")" == "$(<"${file}.mode")" ]]
  done
  # A repeated invocation must preserve both files and must not migrate again.
  : >"${CALLS}"
  bash "${scripts}/host.sh"
  head -n 3 "${expected}" >"${dir}/warm-calls"
  tail -n 1 "${expected}" >>"${dir}/warm-calls"
  diff -u "${dir}/warm-calls" "${CALLS}"
  for file in "${SUBUID_FILE}" "${SUBGID_FILE}"; do
    diff -u "${file}.expected" "${file}"
    [[ "$(stat -c '%a' "${file}")" == "$(<"${file}.mode")" ]]
  done
  echo "PASS: ${scenario}"
done
