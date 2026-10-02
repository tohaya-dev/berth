#!/usr/bin/env bash
# DD-CYN-0116 G-2: 何も無い状態から、1つの手順で最後まで作り直す。
#
#   前提検査 → (残骸掃除) → クラスタ作成 → イメージ取り込み → 基盤層 → 種データ
#   → 索引の作り直し → アプリ層 → 通しの確認
#
# 途中で人手が要る箇所は無い。鍵を新造する罠は塞いである (無ければ止まる)。
#
# 使い方:
#   ./deploy/k8s/rebuild-all.sh                       # そのまま作り直す
#   CYNOVELA_CLUSTER=名前 ./deploy/k8s/rebuild-all.sh  # 別の名前で作る
#   ./deploy/k8s/rebuild-all.sh --recreate            # 同名クラスタを消してから作る
#
# 主な環境変数:
#   CYNOVELA_CLUSTER  クラスタ名 (既定 cynovela-hansolo)
#   ENTRY_PORT        入口のポート (既定 18890)
#   IMAGE             使うイメージ (既定 localhost/cynovela-hansolo:dd0115)
#   HAN_SOLO_MODELS_DIR
#                     node に bind する models directory
#                     (既定 $REPO/store/models)
#   HAN_SOLO_SKIP_INDEX_WAIT
#                     backup restore 前提の scratch 作成では 1 で索引待ちを飛ばす
#   HAN_SOLO_SKIP_FINAL_VERIFY
#                     backup restore 前提の scratch 作成では 1 で通し確認を後段へ回す
#   PYBIN             yaml/cryptography を持つ python
#   LLM_BASE_URL      Pod から見た推論サーバ (既定 http://host.containers.internal:1234/v1)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

CLUSTER="${CYNOVELA_CLUSTER:-cynovela-hansolo}"
ENTRY_PORT="${ENTRY_PORT:-18890}"
API_PORT="${API_PORT:-52659}"
# DD-CYN-0143 K-4: k3s API の待ち受けは既定でこの機械の中だけ (127.0.0.1)。
# 外へ出す必要があるときだけ K3S_API_BIND=0.0.0.0 を明示する。
K3S_API_BIND="${K3S_API_BIND:-127.0.0.1}"
# DD-CYN-0143 K-1: イメージの既定は image.env の1か所で持つ (スクリプトへの直書きをやめ、
# 手パッチとスクリプトの乖離 = 作り直しで旧イメージへ退行する事故を塞ぐ)。
[ -z "${IMAGE:-}" ] && [ -f "$HERE/image.env" ] && . "$HERE/image.env"
IMAGE="${IMAGE:-localhost/cynovela-hansolo:dd0115}"
MODELS_DIR="${HAN_SOLO_MODELS_DIR:-$REPO/store/models}"
K3S_IMAGE="${K3S_IMAGE:-docker.io/rancher/k3s:v1.35.5-k3s1}"
NS=cynovela
RECREATE=0
[ "${1:-}" = "--recreate" ] && RECREATE=1

echo "############ [0/8] 前提検査 (G-3)"
"$HERE/preflight.sh"
[ -d "$MODELS_DIR" ] || { echo "  ERROR: models directory がありません: $MODELS_DIR" >&2; exit 1; }

# k3d は docker の口を探す。記号リンクに頼らず podman の口を直に指す。
# (前走行はこの記号リンクが宙吊りで初動に失敗した)
export DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"
echo "  DOCKER_HOST=$DOCKER_HOST"

echo "############ [1/8] 残骸の掃除 (G-4)"
CYNOVELA_CLUSTER="$CLUSTER" "$HERE/cleanup-stale.sh" --apply || true

if k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER"; then
  if [ "$RECREATE" = "1" ]; then
    echo "  同名クラスタを消します: $CLUSTER"
    k3d cluster delete "$CLUSTER"
  else
    echo "  クラスタ $CLUSTER は既に在ります。中身の入れ直しだけ行います (作り直すなら --recreate)。"
  fi
fi

if ! k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER"; then
  echo "############ [2/8] クラスタ作成"
  mkdir -p "$HOME/dt-backups/k8s-pg" "$HOME/dt-backups/k8s-ingest"
  k3d cluster create "$CLUSTER" \
    --servers 1 --agents 1 \
    --image "$K3S_IMAGE" \
    --api-port "${K3S_API_BIND}:${API_PORT}" \
    --port "0.0.0.0:${ENTRY_PORT}:80@loadbalancer" \
    --volume "$MODELS_DIR:/var/lib/cynovela-models/models:ro@server:0" \
    --volume "$MODELS_DIR:/var/lib/cynovela-models/models:ro@agent:0" \
    --volume "$HOME/dt-backups/k8s-pg:/var/lib/cynovela-backups@server:0" \
    --volume "$HOME/dt-backups/k8s-pg:/var/lib/cynovela-backups@agent:0" \
    --volume "$HOME/dt-backups/k8s-ingest:/var/lib/cynovela-ingest@server:0" \
    --volume "$HOME/dt-backups/k8s-ingest:/var/lib/cynovela-ingest@agent:0"
else
  echo "############ [2/8] クラスタ作成 → 既存を使う"
fi
kubectl config use-context "k3d-$CLUSTER" >/dev/null

if command -v podman >/dev/null 2>&1; then
  mkdir -p "$REPO/store"
  IP_STATE="$REPO/store/k3d-${CLUSTER}-ips.env"
  {
    printf 'K3D_NETWORK=%s\n' "k3d-${CLUSTER}"
    printf 'K3D_SERVER_IP=%s\n' "$(podman inspect "k3d-${CLUSTER}-server-0" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)"
    printf 'K3D_AGENT_IP=%s\n' "$(podman inspect "k3d-${CLUSTER}-agent-0" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)"
    printf 'K3D_LB_IP=%s\n' "$(podman inspect "k3d-${CLUSTER}-serverlb" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)"
  } > "$IP_STATE"
  chmod 600 "$IP_STATE"
  echo "  k3d node IP state: $IP_STATE"
fi

echo "############ [3/8] ノードが揃うのを待つ"
# 作った直後は k3s の API がまだ応じず、kubectl が "EOF" で失敗することがある。
# 応じるまで最大 2 分 (2 秒おき) 待ってから、ノードの Ready を待つ。
for i in $(seq 1 60); do
  kubectl get --raw /readyz >/dev/null 2>&1 && break
  sleep 2
done
kubectl wait --for=condition=Ready nodes --all --timeout=180s
kubectl get nodes

echo "############ [4/8] イメージの取り込み"
if podman image exists "${IMAGE#localhost/}" 2>/dev/null || podman image exists "$IMAGE" 2>/dev/null; then
  k3d image import "$IMAGE" -c "$CLUSTER"
else
  echo "  ERROR: イメージ $IMAGE がありません。先に作ってください。" >&2
  echo "  (deploy/k8s/image-supply-runbook-20260719.md 参照)" >&2
  exit 1
fi

echo "############ [5/8] 基盤層 + 種データ + アプリ層 (apply-phase4a.sh)"
IMAGE="$IMAGE" "$REPO/deploy/k8s/phase4a/apply-phase4a.sh"

echo "############ [6/8] 索引: 空なら本体が起動時に作り直す (Z-3)"
# 配備スクリプトの reembed は残してあるが、実行時の起動経路にも同じ復元を入れてある。
# ∴ ここでは「索引が入ったか」を待つだけでよい。
if [ "${HAN_SOLO_SKIP_INDEX_WAIT:-0}" = "1" ]; then
  echo "  HAN_SOLO_SKIP_INDEX_WAIT=1: backup restore 前提のため索引待ちを飛ばします"
else
  for i in $(seq 1 60); do
    N="$(kubectl -n "$NS" exec deploy/cynovela-pgvector -- psql -U cynovela -d cynovela -tAc \
          "SELECT count(*) FROM chunks_vec;" 2>/dev/null | tr -d '[:space:]' || echo 0)"
    [ "${N:-0}" -gt 0 ] && { echo "  索引 $N 行"; break; }
    sleep 10
  done
fi

echo "############ [7/8] 入口が応じるのを待つ"
for i in $(seq 1 60); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${ENTRY_PORT}/api/health" || true)"
  [ "$CODE" = "200" ] && { echo "  health 200"; break; }
  sleep 5
done

echo "############ [8/8] 通しの確認 (G-14)"
if [ "${HAN_SOLO_SKIP_FINAL_VERIFY:-0}" = "1" ]; then
  echo "  HAN_SOLO_SKIP_FINAL_VERIFY=1: backup restore 後に別途確認します"
else
  ENTRY_PORT="$ENTRY_PORT" "$HERE/verify-all.sh" || {
    echo "  確認に不合格がありました (上の出力を見てください)。クラスタは残してあります。" >&2
    exit 1
  }
fi

echo
echo "=== 作り直し完了。入口 http://localhost:${ENTRY_PORT} ==="
