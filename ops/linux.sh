#!/usr/bin/env bash
# Linux Kubernetes adapter. The Mac Podman/k3d entry points remain unchanged.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Environment: BERTH_* is the primary name. HAN_SOLO_* is still honoured as a deprecated alias; when both are
# set, BERTH_* wins.
DATA="${BERTH_DATA_DIR:-${HAN_SOLO_DATA_DIR:-$HOME/.local/share/berth}}"
# Default resolution (only when neither variable is set): the default for new installs is
# ~/.local/share/berth. An install that relied on the former default ~/.local/share/hansolo and has no
# ~/.local/share/berth keeps finding its data; set BERTH_DATA_DIR explicitly to silence the notice.
if [[ -z "${BERTH_DATA_DIR:-}${HAN_SOLO_DATA_DIR:-}" && ! -d "$DATA" && -d "$HOME/.local/share/hansolo" ]]; then
  DATA="$HOME/.local/share/hansolo"
  echo "notice: using legacy default data directory $DATA (set BERTH_DATA_DIR to choose explicitly)" >&2
fi
NS="${BERTH_NAMESPACE:-${HAN_SOLO_NAMESPACE:-berth}}"
CONTEXT="${BERTH_CONTEXT:-${HAN_SOLO_CONTEXT:-}}"
: "${CONTEXT:?Set BERTH_CONTEXT (deprecated alias: HAN_SOLO_CONTEXT) to the target Kubernetes context}"
PORT="${BERTH_ENTRY_PORT:-${HAN_SOLO_ENTRY_PORT:-18765}}"
# The entry forward binds loopback by default. A lab can widen it (for example to reach the UI through a VPN
# interface) with BERTH_ENTRY_ADDRESS (deprecated alias: HAN_SOLO_ENTRY_ADDRESS), or with a one-line file "$DATA/entry-address". When widened, restrict
# the port with a host firewall: the forward itself does no source filtering.
ADDR="${BERTH_ENTRY_ADDRESS:-${HAN_SOLO_ENTRY_ADDRESS:-$(cat "$DATA/entry-address" 2>/dev/null || true)}}"; ADDR="${ADDR:-127.0.0.1}"
[[ "$ADDR" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "Invalid entry address: $ADDR" >&2; exit 2; }
IMAGE="${IMAGE:-}"
PYTHON="${PYBIN:-python3}"
RENDER="$DATA/rendered"
k() { kubectl --context "$CONTEXT" -n "$NS" "$@"; }
verify() {
  k get deployment cynovela cynovela-worker cynovela-pgvector cynovela-redis -o json |
    "$PYTHON" -c 'import json,sys; rows=json.load(sys.stdin)["items"]; expected={"cynovela":2,"cynovela-worker":2,"cynovela-pgvector":1,"cynovela-redis":1}; assert len(rows)==4; assert all(x["spec"]["replicas"]==expected[x["metadata"]["name"]] and x.get("status",{}).get("readyReplicas",0)==expected[x["metadata"]["name"]] for x in rows);print("replicas PASS")'
  curl -fsS --max-time 30 "http://127.0.0.1:$PORT/api/health" |
    "$PYTHON" -c 'import json,sys; assert json.load(sys.stdin)["status"]=="ok"; print("health PASS")'
  curl -fsS --max-time 30 "http://127.0.0.1:$PORT/api/ready" >/dev/null
  k exec deploy/cynovela-pgvector -- psql -v ON_ERROR_STOP=1 -U cynovela -d cynovela -tAc "SELECT count(*) FROM pg_extension WHERE extname='vector';" |
    "$PYTHON" -c 'import sys;assert sys.stdin.read().strip()=="1";print("pgvector PASS")'
}
case "${1:-}" in
  install|start)
    : "${IMAGE:?Set IMAGE to a built/imported Berth image}"
    "$PYTHON" "$ROOT/deploy/k8s/linux/render.py" --namespace "$NS" --data "$DATA" --image "$IMAGE" --output "$RENDER"
    k apply -f "$RENDER/infrastructure.yaml"
    k rollout status deploy/cynovela-pgvector --timeout=300s
    k rollout status deploy/cynovela-redis --timeout=180s
    k delete job hansolo-bootstrap --ignore-not-found
    k apply -f "$RENDER/bootstrap.yaml"
    k wait --for=condition=complete job/hansolo-bootstrap --timeout=300s
    k apply -f "$RENDER/workloads.yaml"
    k rollout status deploy/cynovela --timeout=900s
    k rollout status deploy/cynovela-worker --timeout=600s
    echo "Runtime ready. Run './ops/linux.sh connect' in a terminal; endpoint http://127.0.0.1:$PORT"
    ;;
  connect) exec kubectl --context "$CONTEXT" -n "$NS" port-forward --address "$ADDR" service/cynovela-svc "$PORT:8765" ;;
  serve)
    trap 'exit 0' INT TERM
    while true; do
      kubectl --context "$CONTEXT" -n "$NS" port-forward --address "$ADDR" service/cynovela-svc "$PORT:8765" || true
      sleep 3
    done
    ;;
  status) k get pods,deployments,services,pvc ;;
  verify) verify ;;
  stop) k scale deploy/cynovela deploy/cynovela-worker deploy/cynovela-redis deploy/cynovela-pgvector --replicas=0 ;;
  restart)
    k rollout restart deploy/cynovela deploy/cynovela-worker
    k rollout status deploy/cynovela --timeout=900s
    k rollout status deploy/cynovela-worker --timeout=600s
    ;;
  backup|restore)
    # Both names are exported so an older or newer pg-backup.sh works during the transition.
    export NS BERTH_CONTEXT="$CONTEXT" HAN_SOLO_CONTEXT="$CONTEXT"
    export BERTH_SECRET_FILE="$DATA/secrets/secret_key" HAN_SOLO_SECRET_FILE="$DATA/secrets/secret_key"
    exec "$ROOT/deploy/k8s/pg-backup.sh" "$@"
    ;;
  *)
    echo "Usage: linux.sh install|start|connect|serve|status|verify|stop|restart|backup|restore DUMP --yes" >&2
    echo "Environment: BERTH_CONTEXT (required), BERTH_DATA_DIR, BERTH_NAMESPACE, BERTH_ENTRY_PORT, BERTH_ENTRY_ADDRESS, IMAGE (install/start), PYBIN; the HAN_SOLO_* names are accepted as deprecated aliases" >&2
    exit 2 ;;
esac
