#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

need_bin PODMAN_BIN podman
need_bin K3D_BIN k3d
need_bin KUBECTL_BIN kubectl

if [ "$(podman_state)" != "running" ]; then
  "$PODMAN_BIN" machine start
fi
set_podman_docker_host

start_existing_cluster_with_recorded_ips() {
  local state_file="$REPO_ROOT/store/k3d-${HAN_SOLO_CLUSTER}-ips.env"
  [ -f "$state_file" ] || return 1
  # shellcheck disable=SC1090
  . "$state_file"
  [ -n "${K3D_NETWORK:-}" ] && [ -n "${K3D_SERVER_IP:-}" ] && [ -n "${K3D_AGENT_IP:-}" ] && [ -n "${K3D_LB_IP:-}" ] || return 1

  local server="k3d-${HAN_SOLO_CLUSTER}-server-0"
  local agent="k3d-${HAN_SOLO_CLUSTER}-agent-0"
  local lb="k3d-${HAN_SOLO_CLUSTER}-serverlb"

  reconnect_node() {
    local name="$1"
    local ip="$2"
    "$PODMAN_BIN" stop "$name" >/dev/null 2>&1 || true
    "$PODMAN_BIN" network disconnect "$K3D_NETWORK" "$name" >/dev/null 2>&1 || true
    "$PODMAN_BIN" network connect --ip "$ip" "$K3D_NETWORK" "$name" >/dev/null
  }

  reconnect_node "$server" "$K3D_SERVER_IP"
  reconnect_node "$agent" "$K3D_AGENT_IP"
  reconnect_node "$lb" "$K3D_LB_IP"
  "$PODMAN_BIN" start "$server" >/dev/null
  sleep 8
  "$PODMAN_BIN" start "$agent" >/dev/null
  sleep 4
  "$PODMAN_BIN" start "$lb" >/dev/null
}

if "$K3D_BIN" cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$HAN_SOLO_CLUSTER"; then
  start_existing_cluster_with_recorded_ips || "$K3D_BIN" cluster start "$HAN_SOLO_CLUSTER"
else
  echo "Cluster $HAN_SOLO_CLUSTER not found; running non-destructive rebuild path." >&2
  HAN_SOLO_CLUSTER="$HAN_SOLO_CLUSTER" \
  CYNOVELA_CLUSTER="$HAN_SOLO_CLUSTER" \
  ENTRY_PORT="$HAN_SOLO_ENTRY_PORT" \
  IMAGE="${IMAGE:-localhost/cynovela-hansolo:dd0144-ga}" \
  HAN_SOLO_MODELS_DIR="${HAN_SOLO_MODELS_DIR:-$REPO_ROOT/store/models}" \
  "$REPO_ROOT/deploy/k8s/rebuild-all.sh"
fi

wait_for_runtime() {
  # 起動した直後は k3s の API がまだ応じないことがあるため、応じるまで最大 2 分待つ。
  for _ in $(seq 1 60); do
    "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" get --raw /readyz >/dev/null 2>&1 && break
    sleep 2
  done
  "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" wait --for=condition=Ready nodes --all --timeout=180s
  if ! "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n kube-system wait \
    --for=condition=Ready pod -l k8s-app=kube-dns --timeout=120s; then
    "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n kube-system delete pod -l k8s-app=kube-dns || true
    "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n kube-system wait \
      --for=condition=Ready pod -l k8s-app=kube-dns --timeout=120s
  fi
  "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" wait \
    --for=condition=Available deploy/cynovela-pgvector deploy/cynovela-redis --timeout=180s
  "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" wait \
    --for=condition=Available deploy/cynovela deploy/cynovela-worker --timeout=180s || return 1
}

if ! wait_for_runtime; then
  "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" rollout restart deploy/cynovela deploy/cynovela-worker
  "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" rollout status deploy/cynovela --timeout=180s
  "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" rollout status deploy/cynovela-worker --timeout=180s
fi

for _ in $(seq 1 24); do
  health="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HAN_SOLO_BASE_URL/api/health" || true)"
  ready="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HAN_SOLO_BASE_URL/api/ready" || true)"
  [ "$health" = "200" ] && [ "$ready" = "200" ] && break
  sleep 5
done

"$HERE/verify.sh"
