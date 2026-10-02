#!/usr/bin/env bash
# Phase 4a (k3d load): 現行 Phase3 ビルドを k3d 擬似マルチノードへ載せる。
#   前提: k3d クラスタ cynovela-p4a が稼働・イメージ import 済み
#         (localhost/cynovela-{phase3,settle,phase2}:latest)、models をノードに bind-mount 済み。
#   操作は kubectl 直（Lima/k3s 用 apply-phase2.sh とは異なり limactl を使わない）。
#   コード変更ゼロ・マニフェスト/ConfigMap のみで完結。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
NS=cynovela
# yaml を持つ python を実行時に解決する (DD-CYN-0143 R-1: conda 環境の絶対パス前提を外す)。
# 優先順位: 環境変数 PYBIN > conda cynovela 環境 (在れば) > python3。yaml が無ければ名指しで止まる。
if [ -z "${PYBIN:-}" ]; then
  if [ -x "$HOME/miniforge3/envs/cynovela/bin/python" ]; then
    PYBIN="$HOME/miniforge3/envs/cynovela/bin/python"
  else
    PYBIN="$(command -v python3)"
  fi
fi
"$PYBIN" -c 'import yaml' 2>/dev/null || {
  echo "ERROR: $PYBIN に PyYAML がありません。PYBIN=<yamlを持つpython> を指定してください (例: pip install pyyaml)。" >&2
  exit 1
}
# DD-CYN-0143 R-2: 機材の大きさに依存する値をここ1か所へ寄せる (env で差し替えるだけで変わる)。
#   MASKING_PARALLELISM: 伏字の並列度 (既定 3 = DD-CYN-0136 の手パッチで稼働中の実効値。
#                        スクリプト直書きの 1 は作り直しで退行していた = DD-CYN-0143 K-1)
#   WORKER_MEM_LIMIT   : worker のメモリ上限 (既定 6Gi = 稼働中の実効値)
#   API_REPLICAS / WORKER_REPLICAS: レプリカ数 (既定 2)
MASKING_PARALLELISM="${MASKING_PARALLELISM:-3}"
WORKER_MEM_LIMIT="${WORKER_MEM_LIMIT:-6Gi}"
API_REPLICAS="${API_REPLICAS:-2}"
WORKER_REPLICAS="${WORKER_REPLICAS:-2}"
# DD-CYN-0143 K-3: 作り直し後にログインできる初期パスワード。
#   焼き込み demo.db のハッシュは配布物由来で合言葉が分からず、admin_initial_password が
#   空だと seed の初期PW経路がスキップされ「作り直すと誰も入れない」(1回目で実測)。
#   ここで ConfigMap auth.* へ焼き、seed の FORCE で必ず適用する。値は env で差し替え可。
ADMIN_INITIAL_PASSWORD="${ADMIN_INITIAL_PASSWORD:-}"
VIEWER_INITIAL_PASSWORD="${VIEWER_INITIAL_PASSWORD:-}"
[ -n "$ADMIN_INITIAL_PASSWORD" ] || {
  echo "ERROR: ADMIN_INITIAL_PASSWORD is required for a fresh deployment." >&2
  exit 1
}
[ -n "$VIEWER_INITIAL_PASSWORD" ] || {
  echo "ERROR: VIEWER_INITIAL_PASSWORD is required for a fresh deployment." >&2
  exit 1
}
# Pods からホスト側 LLM へ到達するアドレス。Portable 経路では固定 IP を既定にしない。
LLM_BASE_URL="${LLM_BASE_URL:-http://host.containers.internal:1234/v1}"

echo "[1/7] namespace"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -

echo "[1.5/7] ingress(Traefik) HA 上書き（HelmChartConfig: replicas2 + 別ノード分散 + PDB。v3 3-A の単一点是正）"
kubectl apply -f "$HERE/65-traefik-ha.yaml"

echo "[2/7] ConfigMap cynovela-config (backend=postgres + pgvector 明示 + redis + worker=minimal + llm)"
TMP="$(mktemp -d)"
"$PYBIN" - "$REPO/cynovela.yaml" "$TMP/cynovela.yaml" "$LLM_BASE_URL" "$MASKING_PARALLELISM" \
  "$ADMIN_INITIAL_PASSWORD" "$VIEWER_INITIAL_PASSWORD" <<'PY'
import sys, yaml
src, dst, llm = sys.argv[1], sys.argv[2], sys.argv[3]
masking_par = int(sys.argv[4])
admin_pw, viewer_pw = sys.argv[5], sys.argv[6]
c = yaml.safe_load(open(src))
# queue（Redis 信頼配送）
# DD-CYN-0115 H-1: visibility_timeout 30→300。30 秒では実用サイズの資料の取り込みが終わる前に
#   期限が切れ、まだ動いている worker のジョブを reaper が奪い続けて完走しない（約30秒周期の
#   再配送ループ）。max_redeliver=3 で毒ジョブの無限再配送も止める。
c.setdefault("queue", {}).update({"enabled": True, "url": "redis://cynovela-redis:6379/0",
    "key_prefix": "cynovela", "visibility_timeout": 300, "heartbeat_interval": 5,
    "reserve_timeout": 5, "max_redeliver": 3})
# DD-CYN-0115 H-1 → DD-CYN-0143 R-2: 伏字の並列度は機材の大きさに依存する値のため、
#   apply-phase4a.sh の MASKING_PARALLELISM (env) から渡す。既定 3 = DD-CYN-0136 の
#   手パッチで稼働中の実効値 (直書き 1 のままでは作り直しで退行していた = K-1)。
#   worker の OOM 循環が出る機材では MASKING_PARALLELISM=1 を指定する。
c.setdefault("masking", {}).update({"parallelism": masking_par})
# 関係層=Postgres（Phase 3）。pool_max を明示し接続数会計を確定。
c.setdefault("database", {})["backend"] = "postgres"
c["database"].setdefault("postgres", {}).update({"host": "cynovela-pgvector", "port": 5432,
    "dbname": "cynovela", "user": "cynovela", "password": "cynovela", "pool_max": 10})
# ベクター=pgvector を明示（backend=postgres で自動選択されるが Chroma 既定回避を明示）。
c.setdefault("vector_store", {})["provider"] = "pgvector"
# worker は TF-IDF（bge-m3 ロードせず・メモリ余裕）。API は embedding(bge-m3) を使う。
c.setdefault("worker", {})["embedding_mode"] = "minimal"
# LLM 宛先（pods から Mac ホストへ）。provider=lmstudio で起動時アダプタを LMStudioAdapter にし、
# get_current_adapter() が DB settings.llm_endpoint を都度読み直す DB 駆動経路にする
# （in-image 既定 provider=ollama は OpenAICompatibleAdapter を _state にキャッシュさせ全レプリカで切替不可になるため上書き）。
c.setdefault("llm", {}); c["llm"]["provider"] = "lmstudio"; c["llm"]["base_url"] = llm
c["llm"]["model"] = "gemma-4-e4b-it@4bit"
# DD-CYN-0143 K-3: 作り直し後の初期パスワード (seed _apply_initial_passwords が読む)。
c.setdefault("auth", {}).update({"admin_initial_password": admin_pw,
                                 "viewer_initial_password": viewer_pw})
# pii_mode は in-image の standard を維持（admin 原本 / viewer 伏字の二層）。
yaml.safe_dump(c, open(dst, "w"), allow_unicode=True, sort_keys=False)
print("  backend=%s vector=%s queue=%s llm=%s vis=%s max_redeliver=%s masking_par=%s" % (
    c["database"]["backend"], c["vector_store"]["provider"], c["queue"]["enabled"], c["llm"]["base_url"],
    c["queue"]["visibility_timeout"], c["queue"]["max_redeliver"], c["masking"]["parallelism"]))
PY
kubectl create configmap cynovela-config -n "$NS" --from-file=cynovela.yaml="$TMP/cynovela.yaml" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "[3/7] Secret cynovela-secret (Fernet/JWT 共有鍵・API/worker/seed 同一)"
# DD-CYN-0116 G-2: 鍵が無いときに新しく作らない。作ってしまうと、既に暗号化されている
# 本文が二度と復号できなくなる（従来は手引きに注意書きがあるだけで、手順は黙って作っていた）。
if [ ! -f "$REPO/store/secret.key" ]; then
  echo "ERROR: $REPO/store/secret.key がありません。" >&2
  echo "  既存データの鍵をここへ置いてから実行してください（新しい鍵を作ると既存の本文を復号できなくなります）。" >&2
  echo "  本当に新規に作る場合のみ: CYNOVELA_ALLOW_NEW_KEY=1 を付けて実行してください。" >&2
  if [ "${CYNOVELA_ALLOW_NEW_KEY:-}" != "1" ]; then exit 1; fi
  "$PYBIN" -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())' > "$REPO/store/secret.key"
  echo "  新しい鍵を作りました: $REPO/store/secret.key" >&2
fi
SECRET_KEY="$(cat "$REPO/store/secret.key")"
kubectl create secret generic cynovela-secret -n "$NS" --from-literal=secret_key="$SECRET_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
rm -rf "$TMP"

echo "[4/7] Postgres(pgvector, C ロケール) + Redis(AOF)"
kubectl apply -f "$REPO/deploy/k8s/phase2/50-pgvector.yaml"
kubectl apply -f "$REPO/deploy/k8s/phase2/40-redis.yaml"
kubectl -n "$NS" rollout status deploy/cynovela-pgvector --timeout=240s
kubectl -n "$NS" rollout status deploy/cynovela-redis    --timeout=120s

echo "[5/7] seed Job（DDL→importer→setval・件数 chunks1005/collections19/audit1058/parent730/files150）"
# DD-CYN-0136 X13: seed job の image は yaml に固定されており (dd0115)、IMAGE 指定が
# 効かず再構築が ErrImageNeverPull で止まった (実測)。API/worker と同じ IMAGE に置換する。
sed "s|image: localhost/cynovela-hansolo:.*|image: ${IMAGE:-localhost/cynovela-hansolo:dd0115}|" \
  "$REPO/deploy/k8s/phase3/10-seed-job.yaml" | kubectl apply -f -
kubectl -n "$NS" wait --for=condition=complete job/cynovela-pg-seed --timeout=300s

echo "[6/7] worker(replicas${WORKER_REPLICAS}) + API(replicas${API_REPLICAS}, 分散) + Service + Ingress"
# DD-CYN-0136 X13: API/worker の image も yaml 固定 (dd0116h) で IMAGE 指定が効かなかった。
# seed と同じく IMAGE へ置換して適用する (未指定時は yaml の値のまま)。
# DD-CYN-0143 R-2: worker のメモリ上限とレプリカ数も env から差し替える (yaml 手パッチの退行防止)。
if [ -n "${IMAGE:-}" ]; then
  sed -e "s|image: localhost/cynovela-hansolo:.*|image: ${IMAGE}|" \
      -e "s|limits: { memory: \".*\" }|limits: { memory: \"${WORKER_MEM_LIMIT}\" }|" \
      -e "s|replicas: .*|replicas: ${WORKER_REPLICAS}|" \
      "$HERE/70-worker-deployment.yaml" | kubectl apply -f -
else
  kubectl apply -f "$HERE/70-worker-deployment.yaml"
fi
kubectl apply -f "$REPO/deploy/k8s/30-service.yaml"
if [ -n "${IMAGE:-}" ]; then
  sed -e "s|image: localhost/cynovela-hansolo:.*|image: ${IMAGE}|" \
      -e "s|replicas: .*|replicas: ${API_REPLICAS}|" \
      "$HERE/20-api-deployment.yaml" | kubectl apply -f -
else
  kubectl apply -f "$HERE/20-api-deployment.yaml"
fi
kubectl apply -f "$HERE/60-ingress.yaml"
# DD-CYN-0143 X-1: /api/health が稼働イメージのタグを併記できるよう env を注入する。
kubectl -n "$NS" set env deploy/cynovela        CYNOVELA_IMAGE_TAG="${IMAGE:-}" >/dev/null
kubectl -n "$NS" set env deploy/cynovela-worker CYNOVELA_IMAGE_TAG="${IMAGE:-}" >/dev/null
# DD-CYN-0143 K-1 判断: 80-db-backup-cronjob.yaml (日次バックアップ) は既定で適用しない
# (最上位-049: 自動で走る仕掛けを既定にしない)。使うときだけ明示で開ける:
if [ "${APPLY_BACKUP_CRONJOB:-}" = "1" ]; then
  echo "  [任意] 日次バックアップ CronJob を適用します (APPLY_BACKUP_CRONJOB=1)"
  kubectl apply -f "$HERE/80-db-backup-cronjob.yaml"
else
  echo "  [注記] 日次バックアップ CronJob は既定で適用しない。手動は deploy/k8s/pg-backup.sh backup、常設は APPLY_BACKUP_CRONJOB=1"
fi

echo "[7/8] rollout 待ち"
kubectl -n "$NS" rollout status deploy/cynovela-worker --timeout=240s || true
kubectl -n "$NS" rollout status deploy/cynovela        --timeout=600s || true
kubectl -n "$NS" get pods -o wide

echo "[8/8] harden: LLM 宛先既定の是正(B-3) + 索引自動復元(B-2)"
# B-3: seed は demo.db の settings.llm_endpoint=localhost:1234 を焼き込み、provider=lmstudio の
#      get_current_adapter() が DB 値を都度読むため pod 内で自分自身を指し RAG 不達になる(大総決算で実測)。
#      配備既定を ConfigMap と同じ gateway 値に上書き(idempotent UPSERT)。保護対象(モデルの正体)不接触。
LLM_MODEL="${LLM_MODEL:-gemma-4-e4b-it@4bit}"
PG_POD="$(kubectl -n "$NS" get pod -l app=cynovela-pgvector -o jsonpath='{.items[0].metadata.name}')"
if [ -n "$PG_POD" ]; then
  kubectl -n "$NS" exec "$PG_POD" -- psql -U cynovela -d cynovela -c \
    "INSERT INTO settings(key,value) VALUES('llm_endpoint','$LLM_BASE_URL') ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value; \
     INSERT INTO settings(key,value) VALUES('llm_model','$LLM_MODEL') ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value;" \
    && echo "  [B-3] settings.llm_endpoint=$LLM_BASE_URL / llm_model=$LLM_MODEL" || echo "  [B-3] WARN: settings 上書き失敗(継続)"
fi
# B-2: seed は関係層のみ・chunks_vec 空 → BGE-M3 で自動 reembed(冪等 upsert)。
# DD-CYN-0143 K-3: exec 先の API Pod は bge-m3 ロード済み (limit 6Gi) で、2 本目の bge-m3 が
#   OOM (exit 137) になり chunks_vec が作られなかった (1回目で実測)。専用 Job (limit 8Gi) へ変更。
NV="$(kubectl -n "$NS" exec deploy/cynovela-pgvector -- psql -U cynovela -d cynovela -tAc \
      "SELECT count(*) FROM chunks_vec;" 2>/dev/null | tr -d '[:space:]' || echo 0)"
if [ "${NV:-0}" -gt 0 ]; then
  echo "  [B-2] chunks_vec は既に $NV 行。reembed を飛ばす"
else
  kubectl -n "$NS" delete job cynovela-reembed --ignore-not-found >/dev/null 2>&1
  sed "s|image: localhost/cynovela-hansolo:.*|image: ${IMAGE:-localhost/cynovela-hansolo:dd0115}|" \
    "$HERE/90-reembed-job.yaml" | kubectl apply -f -
  if kubectl -n "$NS" wait --for=condition=complete job/cynovela-reembed --timeout=1800s; then
    echo "  [B-2] reembed Job 完了(idempotent)"
  else
    echo "  [B-2] WARN: reembed Job が完走しない (kubectl -n $NS logs job/cynovela-reembed で確認・継続)"
  fi
fi
echo "done."
