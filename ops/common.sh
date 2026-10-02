#!/usr/bin/env bash
set -euo pipefail

HAN_SOLO_CLUSTER="${HAN_SOLO_CLUSTER:-berth-live}"
HAN_SOLO_CONTEXT="${HAN_SOLO_CONTEXT:-k3d-${HAN_SOLO_CLUSTER}}"
HAN_SOLO_NAMESPACE="${HAN_SOLO_NAMESPACE:-cynovela}"
HAN_SOLO_ENTRY_PORT="${HAN_SOLO_ENTRY_PORT:-18765}"
HAN_SOLO_BASE_URL="${HAN_SOLO_BASE_URL:-http://127.0.0.1:${HAN_SOLO_ENTRY_PORT}}"
HAN_SOLO_PODMAN_HOLD_LABEL="${HAN_SOLO_PODMAN_HOLD_LABEL:-local.hansolo.podman.hold}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

find_bin() {
  local env_name="$1"
  local name="$2"
  local candidate
  candidate="${!env_name:-}"
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  if command -v "$name" >/dev/null 2>&1; then
    command -v "$name"
    return 0
  fi
  for candidate in \
    "$HOME/.local/bin/$name" \
    "$HOME/.vs-kubernetes/tools/$name/$name" \
    "$HOME/.vs-kubernetes/tools/helm/darwin-arm64/$name" \
    "/opt/podman/bin/$name" \
    "/opt/homebrew/bin/$name" \
    "/usr/local/bin/$name"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

PODMAN_BIN="$(find_bin PODMAN_BIN podman || true)"
K3D_BIN="$(find_bin K3D_BIN k3d || true)"
KUBECTL_BIN="$(find_bin KUBECTL_BIN kubectl || true)"

need_bin() {
  local var_name="$1"
  local label="$2"
  if [ -z "${!var_name:-}" ]; then
    echo "ERROR: $label not found. Set $var_name or add it to PATH." >&2
    exit 1
  fi
}

set_podman_docker_host() {
  need_bin PODMAN_BIN podman
  local socket_path
  socket_path="$("$PODMAN_BIN" machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}' 2>/dev/null | head -1 || true)"
  if [ -n "$socket_path" ]; then
    export DOCKER_HOST="unix://${socket_path}"
  fi
}

podman_state() {
  if [ -z "$PODMAN_BIN" ]; then
    echo "missing"
    return 0
  fi
  "$PODMAN_BIN" machine inspect --format '{{.State}}' 2>/dev/null | head -1 || echo "unknown"
}

print_env() {
  cat <<EOF
repo=$REPO_ROOT
cluster=$HAN_SOLO_CLUSTER
context=$HAN_SOLO_CONTEXT
namespace=$HAN_SOLO_NAMESPACE
base_url=$HAN_SOLO_BASE_URL
podman=${PODMAN_BIN:-missing}
k3d=${K3D_BIN:-missing}
kubectl=${KUBECTL_BIN:-missing}
EOF
}
