#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

PASS=0
FAIL=0

ok() { printf '[PASS] %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '[FAIL] %s\n' "$1"; FAIL=$((FAIL + 1)); }

need_bin PODMAN_BIN podman
need_bin K3D_BIN k3d
need_bin KUBECTL_BIN kubectl
set_podman_docker_host

state="$(podman_state)"
[ "$state" = "running" ] && ok "Podman machine available" || bad "Podman machine state: $state"

if command -v launchctl >/dev/null 2>&1 && launchctl print "gui/$(id -u)/${HAN_SOLO_PODMAN_HOLD_LABEL}" >/dev/null 2>&1; then
  ok "Podman hold state"
else
  bad "Podman hold state"
fi

if "$K3D_BIN" cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$HAN_SOLO_CLUSTER"; then
  ok "k3d cluster exists"
else
  bad "k3d cluster exists"
fi

if "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" get nodes --no-headers 2>/dev/null | awk '$1 ~ /server/ && $2 == "Ready" { found=1 } END { exit !found }'; then
  ok "k3d server healthy"
else
  bad "k3d server healthy"
fi

if "$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" get nodes --no-headers 2>/dev/null | awk '$1 ~ /agent/ && $2 == "Ready" { found=1 } END { exit !found }'; then
  ok "k3d agent healthy"
else
  bad "k3d agent healthy"
fi

deploy_ready() {
  local name="$1"
  local out
  out="$("$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" get deploy "$name" -o jsonpath='{.status.readyReplicas}/{.spec.replicas}' 2>/dev/null || true)"
  [ -n "$out" ] && [ "${out%/*}" = "${out#*/}" ] && [ "${out%/*}" != "0" ]
}

deploy_ready cynovela && ok "API pods Ready" || bad "API pods Ready"
deploy_ready cynovela-worker && ok "worker pods Ready" || bad "worker pods Ready"
deploy_ready cynovela-pgvector && ok "PostgreSQL Ready" || bad "PostgreSQL Ready"
deploy_ready cynovela-redis && ok "Redis Ready" || bad "Redis Ready"

# Pod の入れ替え直後は ingress の振り分け先が追いつくまで 502 になることがあるため、
# 最大 2 分 (5 秒おきに 24 回) 待ってから判定する。
health_status=""; ready_code=""
for _ in $(seq 1 24); do
  health_json="$(curl -sS --max-time 20 "$HAN_SOLO_BASE_URL/api/health" 2>/dev/null || true)"
  health_status="$(printf '%s' "$health_json" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("status") or "").lower())' 2>/dev/null || true)"
  ready_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$HAN_SOLO_BASE_URL/api/ready" 2>/dev/null || true)"
  [ "$health_status" = "ok" ] && [ "$ready_code" = "200" ] && break
  sleep 5
done
[ "$health_status" = "ok" ] && ok "/api/health = ok" || bad "/api/health = ${health_status:-unavailable}"

[ "$ready_code" = "200" ] && ok "/api/ready = 200" || bad "/api/ready = ${ready_code:-unavailable}"

counts="$("$KUBECTL_BIN" --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" exec deploy/cynovela-pgvector -- \
  psql -U cynovela -d cynovela -tAc "SELECT count(*) FROM chunks WHERE tier='masked'; SELECT count(*) FROM chunks_vec;" 2>/dev/null || true)"
masked="$(printf '%s\n' "$counts" | sed -n '1p' | tr -d '[:space:]')"
vectors="$(printf '%s\n' "$counts" | sed -n '2p' | tr -d '[:space:]')"
if [ -n "$masked" ] && [ "$masked" = "$vectors" ]; then
  ok "vector consistency: $vectors / $masked"
else
  bad "vector consistency: ${vectors:-?} / ${masked:-?}"
fi

echo
if [ "$FAIL" = "0" ]; then
  echo "BERTH READY"
  exit 0
fi

echo "BERTH NOT READY ($FAIL failed, $PASS passed)"
exit 1
