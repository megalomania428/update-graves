#!/usr/bin/env bash
set -euo pipefail
# cspell:ignore subuid subgid subuids subgids newuidmap newgidmap uidmap apparmor userns
# cspell:ignore setuid usermod
subuid="${SUBUID_FILE:-/etc/subuid}"
subgid="${SUBGID_FILE:-/etc/subgid}"
# Rootless podman needs the host settings below, so all of them come before the
# first podman command. Ubuntu 24.04 can confine unprivileged user namespaces to
# profiled binaries; the runner is ephemeral, so the restriction is lifted instead
# of relying on profiles.
userns="$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)"
if [[ "${userns}" != '0' ]]; then
  sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
fi
# Subordinate ID ranges and the setuid ID mappers.
user="$(id -un)"
if ! command -v newuidmap newgidmap >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends uidmap
fi
if ! grep -q "^${user}:" "${subuid}" || ! grep -q "^${user}:" "${subgid}"; then
  for file in "${subuid}" "${subgid}"; do
    if [[ ! -e "${file}" ]]; then
      sudo install -o root -g root -m 0644 /dev/null "${file}"
    fi
  done
  start="$({ cat "${subuid}" "${subgid}" 2>/dev/null || true; } | awk -F: '
    { end = $2 + $3; if (end > maximum) maximum = end }
    END { print maximum + 100000 }
  ')"
  end=$((start + 65535))
  sudo usermod --add-subuids "${start}-${end}" --add-subgids "${start}-${end}" "${user}"
  migrate=1
fi
grep "^${user}:" "${subuid}" "${subgid}"
# A podman started before the ranges existed keeps its old ID map until migrated.
if [[ -n "${migrate:-}" ]]; then
  podman system migrate
fi
podman info --format '{{.Host.Security.Rootless}} {{.Store.GraphDriverName}}'
