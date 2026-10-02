#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

need_bin PODMAN_BIN podman
need_bin K3D_BIN k3d
set_podman_docker_host

if "$K3D_BIN" cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$HAN_SOLO_CLUSTER"; then
  "$K3D_BIN" cluster stop "$HAN_SOLO_CLUSTER"
else
  echo "Cluster $HAN_SOLO_CLUSTER not found; nothing to stop."
fi
