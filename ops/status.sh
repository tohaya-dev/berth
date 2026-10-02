#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

print_env
echo
echo "Podman state: $(podman_state)"

if command -v launchctl >/dev/null 2>&1; then
  if launchctl print "gui/$(id -u)/${HAN_SOLO_PODMAN_HOLD_LABEL}" >/dev/null 2>&1; then
    echo "Podman hold: running (${HAN_SOLO_PODMAN_HOLD_LABEL})"
  else
    echo "Podman hold: not running (${HAN_SOLO_PODMAN_HOLD_LABEL})"
  fi
fi

if [ -n "$K3D_BIN" ]; then
  set_podman_docker_host
  echo
  "$K3D_BIN" cluster list || true
fi

if [ -n "$KUBECTL_BIN" ]; then
  echo
  "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" get pods -o wide || true
fi

echo
curl -sS "$HAN_SOLO_BASE_URL/api/health" || true
echo
